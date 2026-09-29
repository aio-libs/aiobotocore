"""Shared helpers for the plugin evals.

The three eval scripts (check_async_need.py, check_override_drift.py,
generate_scenarios.py) all load skill bodies, parse a narrow YAML schema,
run the Anthropic client, and consolidate verdicts. This module centralizes
the pieces they share so behavior can only change in one place.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import ssl
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import anthropic
import certifi

REPO_ROOT = Path(__file__).resolve().parents[3]
AIOBOTOCORE_DIR = REPO_ROOT / "aiobotocore"
# Matches the botocore-sync classify job; tied Opus 5.5 (8/8) at about half the cost.
DEFAULT_MODEL = "claude-sonnet-5-5"
# Pinned explicitly: the API default differs per model (Opus 5.5 `medium`, Sonnet 5.5 `high`).
DEFAULT_EFFORT = "high"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

UPPER_RE = re.compile(r'"botocore\s*>=\s*[\d.]+\s*,\s*<\s*([\d.]+)"')
LOWER_RE = re.compile(r'"botocore\s*>=\s*([\d.]+)\s*,')

# USD per MTok (https://platform.claude.com/docs/en/about-claude/pricing); thinking bills as `output`.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {
        "input": 4.0,
        "output": 20.0,
        "cache_write_5m": 5.0,
        "cache_read": 0.20,
    },
    "claude-sonnet-5-5": {
        "input": 2.0,
        "output": 10.0,
        "cache_write_5m": 2.5,
        "cache_read": 0.20,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_write_5m": 1.25,
        "cache_read": 0.10,
    },
}


# Per-model usage accumulator updated by every invoke_and_* call in
# this module. Single-event-loop asyncio so no lock needed.
_USAGE: dict[str, dict[str, int]] = {}


def _record_usage(model: str, usage: object) -> None:
    """Accumulate token counts from a Messages API response into the
    module-level tally. `usage` is the `resp.usage` attribute.
    """
    bucket = _USAGE.setdefault(
        model,
        {
            "input": 0,
            "output": 0,
            "cache_write_5m": 0,
            "cache_read": 0,
            "calls": 0,
        },
    )
    bucket["calls"] += 1
    bucket["input"] += getattr(usage, "input_tokens", 0) or 0
    bucket["output"] += getattr(usage, "output_tokens", 0) or 0
    bucket["cache_write_5m"] += (
        getattr(usage, "cache_creation_input_tokens", 0) or 0
    )
    bucket["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0


def usage_summary() -> str:
    """Render a human-readable token+cost breakdown of the eval session.

    Returns empty string if no API calls were made. Lines are one per
    model: call count, each token bucket, and estimated cost.
    """
    if not _USAGE:
        return ""
    lines = ["", "== Token usage / cost =="]
    grand_total = 0.0
    for model, u in sorted(_USAGE.items()):
        p = MODEL_PRICING.get(model)
        if p is None:
            lines.append(f"  {model}: {u} (pricing unknown)")
            continue
        cost = (
            u["input"] * p["input"]
            + u["output"] * p["output"]
            + u["cache_write_5m"] * p["cache_write_5m"]
            + u["cache_read"] * p["cache_read"]
        ) / 1_000_000
        grand_total += cost
        lines.append(
            f"  {model}: {u['calls']} calls, "
            f"in={u['input']:,}, out={u['output']:,}, "
            f"cache_r={u['cache_read']:,}, cache_w={u['cache_write_5m']:,}"
            f" → ${cost:.4f}"
        )
    if len(_USAGE) > 1:
        lines.append(f"  Total: ${grand_total:.4f}")
    return "\n".join(lines)


def require_env(name: str) -> None:
    if name not in os.environ:
        sys.stderr.write(f"{name} not set.\n")
        sys.exit(2)


def load_skill_body(path: Path) -> str:
    """Strip YAML frontmatter and return the Markdown body."""
    text = path.read_text()
    if text.startswith("---"):
        _, _, rest = text.split("---", 2)
        return rest.strip()
    return text


def _git_show(commit: str, path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{commit}:{path}"], cwd=REPO_ROOT, text=True
    )


def overridden_paths(commit: str | None = None) -> set[str]:
    """Relative paths of every aiobotocore/*.py file, at `commit` if given.

    Full relative paths (via rglob) so nested files like retries/adaptive.py
    are covered and botocore/docs/client.py doesn't falsely match by basename.
    """
    if commit is None:
        return {
            p.relative_to(AIOBOTOCORE_DIR).as_posix()
            for p in AIOBOTOCORE_DIR.rglob("*.py")
        }
    out = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", commit, "--", "aiobotocore/"],
        cwd=REPO_ROOT,
        text=True,
    )
    return {
        line.removeprefix("aiobotocore/")
        for line in out.splitlines()
        if line.endswith(".py")
    }


TEST_PATCHES_PATH = REPO_ROOT / "tests/test_patches.py"


def overridden_symbols(commit: str | None = None) -> set[str]:
    """Parse tests/test_patches.py and return the set of botocore symbols
    aiobotocore overrides.

    Each entry in the `test_patches` pytest.mark.parametrize body is
    `(<symbol-reference>, {hashes})`. Three shapes are possible:

    - Bare module-level function (`_apply_request_trailer_checksum`) —
      returned as-is.
    - Class method (`ClientArgsCreator.get_client_args`) — returned in
      dotted form only. The bare tail is intentionally NOT added, so a
      change to `SomeOtherClass.get_client_args` elsewhere doesn't
      falsely match.
    - Bare class name (`URLLib3Session`) — returned as-is, meaning the
      class's whole source is tracked but NOT that every method is
      mirrored. Callers must not generalize class-tracked to method-
      tracked.
    """
    names: set[str] = set()
    source = (
        TEST_PATCHES_PATH.read_text()
        if commit is None
        else _git_show(commit, "tests/test_patches.py")
    )
    tree = ast.parse(source)
    targets: list[ast.expr] = []
    for node in ast.walk(tree):
        # older test_patches.py kept entries in a `_API_DIGESTS = {obj: {hashes}}` dict
        if isinstance(node, ast.Dict):
            targets.extend(k for k in node.keys if k is not None)
        elif (
            isinstance(node, ast.Tuple)
            and isinstance(node.ctx, ast.Load)
            and len(node.elts) == 2
        ):
            targets.append(node.elts[0])
    for target in targets:
        parts: list[str] = []
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
            parts.reverse()
            names.add(".".join(parts))
    return names


# Sync-signature methods that delegate to async internals in
# aiobotocore. E.g. `HierarchicalEmitter.emit` stays sync but calls
# `self._emit`, which aiobotocore overrides as `async def`. A caller in
# a sync context hitting `.emit(...)` gets back a coroutine instead of
# a result — so callers must be async-aware even though `emit` itself
# isn't. `async_names()` can't auto-detect this from AST alone (we'd
# need symbolic analysis of sync→async delegation), so it's an
# explicit curated list. Grow as discovered.
_SYNC_BUT_CONTAMINATED_NAMES: frozenset[str] = frozenset(
    {
        "emit",
    }
)


def async_names(commit: str | None = None) -> tuple[set[str], set[str]]:
    """Scan aiobotocore/**/*.py for async surfaces.

    Returns two sets:

    - Async method / function names (bare): every `async def <name>`
      defined anywhere under aiobotocore/, plus the curated
      `_SYNC_BUT_CONTAMINATED_NAMES` entries. Used for duck-typed
      contamination matching — e.g. if botocore adds new code that
      calls `.read(...)` on any object, and `read` is in this set,
      the new code is suspect because aiobotocore's version of that
      method is async (or returns a coroutine).
    - Aio* class names: every `class Aio<Name>(...)` definition. A
      new botocore-side call that instantiates or references one of
      these class's botocore parents (e.g. `ClientCreator(...)`) maps
      to an async override in aiobotocore.
    """
    method_names: set[str] = set(_SYNC_BUT_CONTAMINATED_NAMES)
    class_names: set[str] = set()
    if commit is None:
        sources = (path.read_text() for path in AIOBOTOCORE_DIR.rglob("*.py"))
    else:
        sources = (
            _git_show(commit, f"aiobotocore/{rel}")
            for rel in sorted(overridden_paths(commit))
        )
    for source in sources:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                method_names.add(node.name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith(
                "Aio"
            ):
                class_names.add(node.name)
    return method_names, class_names


def decrement_patch(ver: str) -> str:
    parts = list(map(int, ver.split(".")))
    parts[-1] = max(0, parts[-1] - 1)
    return ".".join(map(str, parts))


def derive_versions(sha: str) -> tuple[str, str, bool] | None:
    """Extract (from_ver, to_ver, lower_changed) from pyproject.toml at commit.

    `lower_changed` is True iff the botocore lower bound moved — the signal
    for a port-required sync (a no-port sync only raises the upper bound).
    Authoritative now that PR titles are uniform ("Bump ..." regardless).
    """
    try:
        before = subprocess.check_output(
            ["git", "show", f"{sha}^:pyproject.toml"],
            cwd=REPO_ROOT,
            text=True,
        )
        after = subprocess.check_output(
            ["git", "show", f"{sha}:pyproject.toml"],
            cwd=REPO_ROOT,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None
    upper_b = UPPER_RE.search(before)
    upper_a = UPPER_RE.search(after)
    if not (upper_b and upper_a):
        sys.stderr.write(
            f"derive_versions({sha}): could not parse botocore upper "
            "bound from pyproject.toml — has the dependency spec format "
            "changed? Check UPPER_RE in _common.py.\n",
        )
        return None
    lower_b = LOWER_RE.search(before)
    lower_a = LOWER_RE.search(after)
    lower_changed = bool(
        lower_b and lower_a and lower_b.group(1) != lower_a.group(1)
    )
    return (
        decrement_patch(upper_b.group(1)),
        decrement_patch(upper_a.group(1)),
        lower_changed,
    )


def list_sync_prs(
    limit: int, extra_fields: tuple[str, ...] = ()
) -> list[dict]:
    """Fetch merged botocore-sync PRs via gh."""
    fields = ["number", "title", "mergeCommit", *extra_fields]
    out = subprocess.check_output(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            "aio-libs/aiobotocore",
            "--search",
            "botocore dependency in:title",
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            ",".join(fields),
        ],
        text=True,
    )
    return json.loads(out)


# Files that can change on any botocore sync without implying a port.
# Hash-only updates to test_patches.py can happen on no-port syncs too
# (newly-tracked functions we depend on but don't override).
_NO_PORT_OK_FILES = {
    "aiobotocore/__init__.py",
    "pyproject.toml",
    "CHANGES.rst",
    "uv.lock",
    "tests/test_patches.py",
}

# aiobotocore-only files (no botocore mirror). A change to one of these
# isn't a "port" — aiobotocore is free to evolve these without upstream
# tracking. If a sync PR bundles changes here they shouldn't flip the
# classifier's ground-truth label.
_AIOBOTOCORE_ONLY_FILES = {
    "_constants.py",
    "_endpoint_helpers.py",
    "_helpers.py",
    "context.py",
    "httpxsession.py",
}


def aiobotocore_port_happened(pr_number: int) -> bool | None:
    """True if the PR modified overridden aiobotocore code (beyond housekeeping).

    Authoritative port-required signal: did the PR modify a `.py` file that
    has a botocore mirror? Housekeeping files (`_NO_PORT_OK_FILES`) don't
    count, and neither do aiobotocore-only files with no botocore mirror
    (`_helpers.py`, `httpxsession.py`, `context.py`, `_constants.py`,
    `_endpoint_helpers.py`) — a PR bundling an unrelated fix to one of
    those is not a port in the classifier sense.

    Returns None if the PR metadata can't be fetched.
    """
    try:
        out = subprocess.check_output(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                "aio-libs/aiobotocore",
                "--json",
                "files",
                "--jq",
                "[.files[].path]",
            ],
            text=True,
        )
    except subprocess.CalledProcessError:
        return None
    paths = json.loads(out)
    for p in paths:
        if p in _NO_PORT_OK_FILES:
            continue
        if not (p.startswith("aiobotocore/") and p.endswith(".py")):
            continue
        rel = p.removeprefix("aiobotocore/")
        if rel in _AIOBOTOCORE_ONLY_FILES:
            continue
        return True
    return False


def parse_scenarios_yaml(
    path: Path,
    scalar_keys: set[str],
    block_scalar_keys: set[str] = frozenset({"rationale", "notes"}),
) -> list[dict[str, str]]:
    """Parse the narrow subset of YAML the generator emits.

    Schema: top-level `scenarios:` list, each item starting with `- pr: N`,
    sub-keys at 2-space indent. `scalar_keys` names the scalar fields each
    caller cares about; other scalars are skipped. Block-scalar bodies
    (`|` style) are consumed but their content isn't captured.
    """
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}
    in_block_scalar = False
    for raw in path.read_text().splitlines():
        line = raw.rstrip()
        if in_block_scalar:
            if line.startswith("    ") or not line.strip():
                continue
            in_block_scalar = False
        if not line or line.lstrip().startswith("#") or line == "---":
            continue
        if line.startswith("- pr:"):
            if current:
                rows.append(current)
                current = {}
            current["pr"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("  ") and ":" in line:
            key, _, value = line.strip().partition(":")
            key = key.strip()
            value = value.strip()
            if key in scalar_keys:
                current[key] = value.strip('"')
            elif key in block_scalar_keys and value == "|":
                in_block_scalar = True
    if current:
        rows.append(current)
    return rows


def new_client() -> anthropic.AsyncAnthropic:
    """Construct the async Anthropic client. Keeps `anthropic` as an
    implementation detail so callers don't import it directly.
    """
    # httpx2's default truststore context races (heap corruption) under concurrent handshakes
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    return anthropic.AsyncAnthropic(
        http_client=anthropic.DefaultAsyncHttpxClient(verify=ssl_context)
    )


def classify_output_schema(verdict_enum: list[str]) -> dict:
    """JSON schema for the structured-output verdict.

    Flat `verdict` + free-form `rationale`: a nested per-function array made
    Opus 4.7 emit empty objects on large diffs.
    """
    return {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": verdict_enum,
                "description": "Top-line roll-up classification.",
            },
            "rationale": {
                "type": "string",
                "description": (
                    "Full reasoning: one paragraph per changed "
                    "function with file, name, change-type, "
                    "verdict, and reason. Rollup summary at the end."
                ),
            },
        },
        "required": ["verdict", "rationale"],
        "additionalProperties": False,
    }


async def invoke_and_classify(
    client: anthropic.AsyncAnthropic,
    system: str,
    user: str,
    model: str,
    effort: str,
    schema: dict,
) -> tuple[str, str, dict | None]:
    """Classify via structured output. Returns (verdict, raw_text, parsed).

    `raw_text` is the JSON text, prefixed with the stop reason when it is
    not `end_turn` so truncations and refusals show up in rationales.
    """
    async with client.messages.stream(
        model=model,
        max_tokens=64000,
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user}],
    ) as stream:
        resp = await stream.get_final_message()
    _record_usage(model, resp.usage)
    raw = "".join(
        block.text
        for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    if resp.stop_reason and resp.stop_reason != "end_turn":
        raw = f"[stop_reason={resp.stop_reason}]\n{raw}"
        return ("parse-error", raw, None)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ("parse-error", raw, None)
    return (parsed.get("verdict", "parse-error").lower(), raw, parsed)


async def followup_on_misclassification(
    client: anthropic.AsyncAnthropic,
    system: str,
    user: str,
    model: str,
    effort: str,
    assistant_reply: str,
    expected: str,
    got: str,
) -> str:
    """Ask the model, in a continuing conversation, what led to the bad
    output. Handles two failure modes:

    - Wrong verdict: ask which prompt phrase it anchored on.
    - Parse error (empty or malformed JSON): ask why it didn't produce
      the classification — that's a non-classification failure we
      otherwise can't diagnose.

    Returns the follow-up assistant text.
    """
    if got == "parse-error":
        followup_q = (
            "Your response did not produce a usable classification — "
            "the JSON came back empty, truncated, or malformed. Why "
            "didn't you populate the `verdict` and `rationale` "
            "fields? Was the prompt unclear, the diff too long to "
            "reason through, the schema confusing, or something "
            "else? Be specific: what would you have needed to complete "
            f"the classification (expected answer was `{expected}`)?"
        )
    else:
        followup_q = (
            f"You classified this as `{got}` but the historical "
            f"ground-truth label is `{expected}`. Walk through your "
            "reasoning step by step: which exact phrase or rule in the "
            "system prompt led you to the verdict you gave? Quote the "
            "text you relied on. Then identify what would have needed "
            "to be different in the prompt for you to arrive at "
            f"`{expected}` instead. Be specific about which rule and "
            "which sentence misled (or failed to steer) you."
        )
    resp = await client.messages.create(
        model=model,
        # thinking is always on for the 5.5 models and counts against max_tokens
        max_tokens=16000,
        output_config={"effort": effort},
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant_reply},
            {"role": "user", "content": followup_q},
        ],
    )
    _record_usage(model, resp.usage)
    return "".join(
        block.text
        for block in resp.content
        if getattr(block, "type", None) == "text"
    )


async def run_cases_concurrent(
    cases: list,
    runs: int,
    invoke_one: Callable,
) -> list[list[str]]:
    """Fire N runs per case in parallel, return per-case verdict lists.

    `invoke_one(case) -> Awaitable[str]` runs a single classifier invocation
    and returns the verdict string.
    """

    async def per_case(case) -> list[str]:
        return list(
            await asyncio.gather(*(invoke_one(case) for _ in range(runs)))
        )

    return list(await asyncio.gather(*(per_case(c) for c in cases)))
