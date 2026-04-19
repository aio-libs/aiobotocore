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
import subprocess
import sys
from pathlib import Path
from typing import Callable

import anthropic

REPO_ROOT = Path(__file__).resolve().parents[3]
AIOBOTOCORE_DIR = REPO_ROOT / "aiobotocore"
# Opus 4.7 at default effort. The recent Opus 4.5+ pricing drop
# (to $5/$25 per M input/output, down from $15/$75) narrowed the gap
# to Sonnet+thinking to ~25%, so the accuracy advantage makes Opus the
# better cost/quality point. Baseline Opus eval was 8/8 without
# thinking; Sonnet+thinking was 7/8. Default across eval, sync, and
# reviewer workflows. Switch back to Sonnet if Opus ever underperforms
# on the regression suite.
DEFAULT_MODEL = "claude-opus-4-7"

UPPER_RE = re.compile(r'"botocore\s*>=\s*[\d.]+\s*,\s*<\s*([\d.]+)"')
LOWER_RE = re.compile(r'"botocore\s*>=\s*([\d.]+)\s*,')

# Per-million-token pricing (USD) for models we actually run the evals
# against. From https://platform.claude.com/docs/en/about-claude/pricing
# as of 2026-04-19. Update when Anthropic publishes new rates.
# `cache_write_5m` is the short-duration write price; `cache_read` is
# any cache-hit read. Thinking tokens bill as `output`.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 5.0,
        "output": 25.0,
        "cache_write_5m": 6.25,
        "cache_read": 0.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_write_5m": 3.75,
        "cache_read": 0.30,
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


def overridden_paths() -> set[str]:
    """Relative paths of every aiobotocore/*.py file.

    Full relative paths (via rglob) so nested files like retries/adaptive.py
    are covered and botocore/docs/client.py doesn't falsely match by basename.
    """
    return {
        p.relative_to(AIOBOTOCORE_DIR).as_posix()
        for p in AIOBOTOCORE_DIR.rglob("*.py")
    }


TEST_PATCHES_PATH = REPO_ROOT / "tests/test_patches.py"


def overridden_symbols() -> set[str]:
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
    tree = ast.parse(TEST_PATCHES_PATH.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple) or len(node.elts) != 2:
            continue
        target = node.elts[0]
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


def async_names() -> tuple[set[str], set[str]]:
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
    for path in AIOBOTOCORE_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
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
    return anthropic.AsyncAnthropic()


def classify_tool_schema(
    tool_name: str,
    verdict_enum: list[str],
    per_function_label: str,  # noqa: ARG001 — kept for API stability
) -> dict:
    """Build a minimal tool schema for structured verdict extraction.

    `verdict_enum` constrains the top-line classification (e.g.
    `["no-port", "port-required", "ambiguous"]` for check-async-need,
    `["clean", "cosmetic-drift", "behavioral-drift"]` for
    check-override-drift).

    Intentionally schema-light: only `verdict` and `rationale` fields.
    An earlier version included a rich `per_function_verdicts` array
    of objects, but Opus 4.7 sometimes emitted an empty tool input
    (`{}`) on larger PR diffs despite being forced via tool_choice,
    even with 16K max_tokens and no truncation. The root cause was
    likely the nested-array schema creating generation ambiguity.
    `rationale` as a free-form string preserves the per-function
    detail without the brittleness.
    """
    return {
        "name": tool_name,
        "description": (
            "Emit the final classification. Call this ONCE, at the end, "
            "after you've reasoned through each changed function. "
            "Include the per-function verdict breakdown in `rationale`."
        ),
        "input_schema": {
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
        },
    }


async def invoke_and_classify(
    client: anthropic.AsyncAnthropic,
    system: str,
    user: str,
    model: str,
    tool: dict,
) -> tuple[str, str, dict | None]:
    """Call the Anthropic API forcing the model to emit its verdict via
    the `tool` schema. Returns (verdict, raw_text, full_tool_input).

    `raw_text` captures any text blocks the model emitted alongside the
    tool call — useful for debugging and for the follow-up-on-miss flow.

    16K max_tokens headroom: per-function verdict arrays for 10+ changed
    functions plus reasoning text can exceed 4K. A truncated tool_use
    block returns empty-dict input and a `parse-error` verdict —
    previously this manifested as mysterious failures on large PRs.
    Stop reason is surfaced in `raw_text` on truncation so debugging
    rationales show the cause.

    The system prompt uses ephemeral cache_control so repeated calls
    within the same eval session (N runs × M cases ≈ 96 calls at
    defaults) hit the cache on the ~2K-token command body.
    """
    resp = await client.messages.create(
        model=model,
        max_tokens=16000,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user}],
    )
    _record_usage(model, resp.usage)
    raw = "".join(
        block.text
        for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    if resp.stop_reason and resp.stop_reason != "end_turn":
        raw = f"[stop_reason={resp.stop_reason}]\n{raw}"
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            tool_input = block.input
            verdict = tool_input.get("verdict", "parse-error")
            return (verdict.lower(), raw, tool_input)
    return ("parse-error", raw, None)


async def followup_on_misclassification(
    client: anthropic.AsyncAnthropic,
    system: str,
    user: str,
    model: str,
    assistant_reply: str,
    expected: str,
    got: str,
) -> str:
    """Ask the model, in a continuing conversation, what led to the bad
    output. Handles two failure modes:

    - Wrong verdict: ask which prompt phrase it anchored on.
    - Parse error (empty or malformed tool call): ask why it didn't
      populate the tool input — that's a non-classification failure
      we otherwise can't diagnose.

    Returns the follow-up assistant text.
    """
    if got == "parse-error":
        followup_q = (
            "Your response did not produce a usable classification — "
            "the tool call came back with empty or missing input. Why "
            "didn't you populate the tool's `verdict` and `rationale` "
            "fields? Was the prompt unclear, the diff too long to "
            "reason through, the tool schema confusing, or something "
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
        max_tokens=2048,
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
