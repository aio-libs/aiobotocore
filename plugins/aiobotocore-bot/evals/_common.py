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
# Sonnet (not Opus) for the same reason claude-code-action defaults to
# Sonnet in the other workflows: structured classification with a clear
# system prompt doesn't need Opus-level reasoning, and Sonnet is faster
# + ~5× cheaper per call. First-run comparison on this branch showed
# 8/8 on both eval suites at Opus; Sonnet is expected to match.
DEFAULT_MODEL = "claude-sonnet-4-6"

UPPER_RE = re.compile(r'"botocore\s*>=\s*[\d.]+\s*,\s*<\s*([\d.]+)"')
LOWER_RE = re.compile(r'"botocore\s*>=\s*([\d.]+)\s*,')


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


async def invoke_and_parse(
    client: anthropic.AsyncAnthropic,
    system: str,
    user: str,
    model: str,
    verdict_re: re.Pattern[str],
) -> tuple[str, str]:
    """Call the Anthropic API and extract a verdict from the response.

    The verdict regex should capture the verdict string as group 1 (e.g.
    `^CLASSIFICATION:\\s*(\\S+)`).

    The system prompt uses ephemeral cache_control so repeated calls
    within the same eval session (N runs × M cases ≈ 96 calls at
    defaults) hit the cache on the ~2K-token command body.
    """
    resp = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(
        block.text
        for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    if m := verdict_re.search(raw):
        # strip trailing `:` and `*` — models sometimes emit
        # `**CLASSIFICATION: no-port**` (bold wrapping both label AND value),
        # which leaves `no-port**` in group 1.
        return (m.group(1).strip().rstrip(":*").lower(), raw)
    return ("parse-error", raw)


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
