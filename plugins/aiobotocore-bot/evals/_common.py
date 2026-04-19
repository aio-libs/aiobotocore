"""Shared helpers for the plugin evals.

The three eval scripts (check_async_need.py, check_override_drift.py,
generate_scenarios.py) all load command bodies, parse a narrow YAML schema,
run the Anthropic client, and consolidate verdicts. This module centralizes
the pieces they share so behavior can only change in one place.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    import anthropic

REPO_ROOT = Path(__file__).resolve().parents[3]
AIOBOTOCORE_DIR = REPO_ROOT / "aiobotocore"
DEFAULT_MODEL = "claude-opus-4-7"

UPPER_RE = re.compile(r'"botocore\s*>=\s*[\d.]+\s*,\s*<\s*([\d.]+)"')
LOWER_RE = re.compile(r'"botocore\s*>=\s*([\d.]+)\s*,')


def require_anthropic(script_name: str) -> None:
    """Exit early with an install hint if the anthropic SDK isn't importable."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "anthropic package not installed. Run with:\n"
            f"    uv run --with anthropic python {script_name}\n",
        )
        sys.exit(2)


def require_env(name: str) -> None:
    if name not in os.environ:
        sys.stderr.write(f"{name} not set.\n")
        sys.exit(2)


def load_command_body(path: Path) -> str:
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
        ]
    )
    return json.loads(out)


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
    """
    resp = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(
        block.text
        for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    if m := verdict_re.search(raw):
        return (m.group(1).strip().rstrip(":").lower(), raw)
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
