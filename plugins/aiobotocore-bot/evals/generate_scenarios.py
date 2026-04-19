#!/usr/bin/env python3
"""Generate a stub scenarios.yaml from merged botocore sync PRs.

Mechanical pass — no LLM. For each merged "Relax"/"Bump" sync PR, emit:

- PR number, title
- from_ver / to_ver (derived from pyproject.toml upper-bound diff)
- expected category (from title)
- botocore and aiobotocore diff URLs
- list of overridden botocore files touched by the diff
- empty rationale / notes fields for a human to fill in

The intent: a stable, commit-tracked ground-truth file that the eval reads
instead of re-deriving at runtime, AND an archaeology artifact — reviewing
each row is a forcing function to find gaps in the workflow prompts.

Run:

    uv run python plugins/aiobotocore-bot/evals/generate_scenarios.py \\
        --out plugins/aiobotocore-bot/evals/scenarios.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from _common import (
    REPO_ROOT,
    derive_versions,
    list_sync_prs,
    overridden_paths,
)

DEFAULT_CLONE = Path("/tmp/botocore")
AIOBOTOCORE_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([\d.]+)["\']')
# URL templates kept as plain strings (not f-strings) so the repo's
# no-f-string-URLs rule isn't tripped on URL construction.
BOTOCORE_COMPARE_URL = "https://github.com/boto/botocore/compare/"
AIOBOTOCORE_PR_URL = "https://github.com/aio-libs/aiobotocore/pull/"


@dataclass
class Scenario:
    pr: int
    title: str
    expected: str
    from_ver: str
    to_ver: str
    merge_commit: str
    aiobotocore_version: str
    botocore_files_touched: list[str]


def aiobotocore_version_at(sha: str) -> str:
    try:
        src = subprocess.check_output(
            ["git", "show", f"{sha}:aiobotocore/__init__.py"],
            cwd=REPO_ROOT,
            text=True,
        )
    except subprocess.CalledProcessError:
        return "unknown"
    if m := AIOBOTOCORE_VERSION_RE.search(src):
        return m.group(1)
    return "unknown"


def touched_overridden_files(
    clone: Path,
    from_ver: str,
    to_ver: str,
    overridden: set[str],
) -> list[str]:
    try:
        out = subprocess.check_output(
            [
                "git",
                "-C",
                str(clone),
                "diff",
                "--name-only",
                f"{from_ver}..{to_ver}",
                "--",
                "botocore/",
            ],
            text=True,
        )
    except subprocess.CalledProcessError:
        return []
    return sorted(
        line
        for line in out.splitlines()
        if line.removeprefix("botocore/") in overridden
    )


def scenarios_from_prs(
    prs: list[dict],
    clone: Path,
    overridden: set[str],
) -> list[Scenario]:
    out: list[Scenario] = []
    for pr in prs:
        title_low = pr["title"].lower()
        if "relax" in title_low:
            expected = "no-port"
        elif "bump" in title_low:
            expected = "port-required"
        else:
            continue
        sha = pr["mergeCommit"]["oid"]
        if not (versions := derive_versions(sha)):
            continue
        from_ver, to_ver = versions
        if from_ver == to_ver:
            continue
        out.append(
            Scenario(
                pr=pr["number"],
                title=pr["title"],
                expected=expected,
                from_ver=from_ver,
                to_ver=to_ver,
                merge_commit=sha,
                aiobotocore_version=aiobotocore_version_at(sha),
                botocore_files_touched=touched_overridden_files(
                    clone,
                    from_ver,
                    to_ver,
                    overridden,
                ),
            )
        )
    return out


def render_yaml(scenarios: list[Scenario]) -> str:
    """Emit YAML matching the project .yamllint (indent-sequences: false)."""
    lines = [
        "---",
        "# Auto-generated stub. Fill in `rationale` and `notes` by hand.",
        "#",
        "# `expected` uses the classifier's current verdict names",
        "# (`no-port` / `port-required`). If those get renamed in",
        "# check-async-need.md, update this file too.",
        "#",
        "# Regenerate: python plugins/aiobotocore-bot/evals/generate_scenarios.py",
        "scenarios:",
    ]
    for s in sorted(scenarios, key=lambda s: s.pr):
        compare_url = BOTOCORE_COMPARE_URL + s.from_ver + "..." + s.to_ver
        pr_url = AIOBOTOCORE_PR_URL + str(s.pr)
        lines += [
            f"- pr: {s.pr}",
            f"  title: {json.dumps(s.title)}",
            f"  expected: {s.expected}",
            f"  from: {json.dumps(s.from_ver)}",
            f"  to: {json.dumps(s.to_ver)}",
            f"  merge_commit: {s.merge_commit}",
            f"  aiobotocore_version: {json.dumps(s.aiobotocore_version)}",
            f"  botocore_diff: {compare_url}",
            f"  aiobotocore_pr: {pr_url}",
            "  botocore_files_touched:",
        ]
        if s.botocore_files_touched:
            lines += [f"  - {f}" for f in s.botocore_files_touched]
        else:
            lines.append("    []")
        lines += [
            "  rationale: |",
            f"    TODO: one paragraph explaining WHY this was {s.expected}.",
            "  notes: null",
            "",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "plugins/aiobotocore-bot/evals/scenarios.yaml",
    )
    parser.add_argument(
        "--clone",
        type=Path,
        default=DEFAULT_CLONE,
        help="Bare clone of boto/botocore (default: /tmp/botocore)",
    )
    parser.add_argument(
        "--limit", type=int, default=40, help="Max PRs to scan (default 40)"
    )
    args = parser.parse_args()

    if not args.clone.exists():
        sys.stderr.write(
            f"Bare botocore clone not found at {args.clone}.\n"
            f"    git clone --bare <botocore-url> {args.clone}\n"
            f"    git -C {args.clone} fetch --tags\n",
        )
        return 2

    overridden = overridden_paths()
    prs = list_sync_prs(args.limit, extra_fields=("url",))
    scenarios = scenarios_from_prs(prs, args.clone, overridden)
    args.out.write_text(render_yaml(scenarios))
    print(f"Wrote {len(scenarios)} scenarios to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
