#!/usr/bin/env python3
"""Generate a stub scenarios.yaml from merged botocore sync PRs.

Mechanical pass — no LLM. For each merged "Relax"/"Bump" sync PR, emit:

- PR number, title
- from_ver / to_ver (derived from pyproject.toml upper-bound diff)
- expected category (from title)
- botocore and aiobotocore diff URLs
- list of overridden botocore files touched by the diff
- empty `rationale` / `notes` fields for a human to fill in

The intent is to produce a stable, commit-tracked ground-truth file that the
eval reads instead of re-deriving at runtime, AND that serves as an archaeology
artifact — reviewing each row is a forcing function to find gaps in the
workflow prompts.

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

REPO_ROOT = Path(__file__).resolve().parents[3]
AIOBOTOCORE_DIR = REPO_ROOT / "aiobotocore"
DEFAULT_CLONE = Path("/tmp/botocore")

UPPER_RE = re.compile(r'"botocore\s*>=\s*[\d.]+\s*,\s*<\s*([\d.]+)"')


AIOBOTOCORE_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([\d.]+)["\']')


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
    """Read __version__ from aiobotocore/__init__.py at the given commit."""
    try:
        src = subprocess.check_output(
            ["git", "show", sha + ":aiobotocore/__init__.py"],
            cwd=REPO_ROOT,
            text=True,
        )
    except subprocess.CalledProcessError:
        return "unknown"
    m = AIOBOTOCORE_VERSION_RE.search(src)
    return m.group(1) if m else "unknown"


def decrement_patch(ver: str) -> str:
    parts = list(map(int, ver.split(".")))
    parts[-1] = max(0, parts[-1] - 1)
    return ".".join(map(str, parts))


def overridden_basenames() -> set[str]:
    return {p.name for p in AIOBOTOCORE_DIR.glob("*.py")}


def gh_json(*args: str) -> object:
    out = subprocess.check_output(["gh", *args])
    return json.loads(out)


def list_candidate_prs(limit: int) -> list[dict]:
    return gh_json(
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
        "number,title,mergeCommit,url",
    )


def derive_versions(sha: str) -> tuple[str, str] | None:
    try:
        before = subprocess.check_output(
            ["git", "show", sha + "^:pyproject.toml"],
            cwd=REPO_ROOT,
            text=True,
        )
        after = subprocess.check_output(
            ["git", "show", sha + ":pyproject.toml"],
            cwd=REPO_ROOT,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None
    m_b = UPPER_RE.search(before)
    m_a = UPPER_RE.search(after)
    if not (m_b and m_a):
        return None
    return (decrement_patch(m_b.group(1)), decrement_patch(m_a.group(1)))


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
                from_ver + ".." + to_ver,
                "--",
                "botocore/",
            ],
            text=True,
        )
    except subprocess.CalledProcessError:
        return []
    touched = []
    for line in out.splitlines():
        name = Path(line).name
        if name in overridden:
            touched.append(line)
    return sorted(touched)


def scenarios_from_prs(
    prs: list[dict], clone: Path, overridden: set[str]
) -> list[Scenario]:
    scenarios = []
    for pr in prs:
        title_low = pr["title"].lower()
        # Use the classifier's current verdict names. If/when the rename lands,
        # replace both these literals AND the ones in check-async-need.md.
        if "relax" in title_low:
            expected = "no-port"
        elif "bump" in title_low:
            expected = "port-required"
        else:
            continue
        versions = derive_versions(pr["mergeCommit"]["oid"])
        if not versions:
            continue
        from_ver, to_ver = versions
        if from_ver == to_ver:
            continue
        touched = touched_overridden_files(clone, from_ver, to_ver, overridden)
        scenarios.append(
            Scenario(
                pr=pr["number"],
                title=pr["title"],
                expected=expected,
                from_ver=from_ver,
                to_ver=to_ver,
                merge_commit=pr["mergeCommit"]["oid"],
                aiobotocore_version=aiobotocore_version_at(
                    pr["mergeCommit"]["oid"],
                ),
                botocore_files_touched=touched,
            ),
        )
    return scenarios


def render_yaml(scenarios: list[Scenario]) -> str:
    """Emit YAML by hand — no PyYAML dep, and the output is deterministic."""
    lines = [
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
        lines.extend(
            [
                "  - pr: " + str(s.pr),
                "    title: " + json.dumps(s.title),
                "    expected: " + s.expected,
                "    from: " + json.dumps(s.from_ver),
                "    to: " + json.dumps(s.to_ver),
                "    merge_commit: " + s.merge_commit,
                "    aiobotocore_version: "
                + json.dumps(s.aiobotocore_version),
                "    botocore_diff: "
                + "https://github.com/boto/botocore/compare/"
                + s.from_ver
                + "..."
                + s.to_ver,
                "    aiobotocore_pr: "
                + "https://github.com/aio-libs/aiobotocore/pull/"
                + str(s.pr),
                "    botocore_files_touched:",
            ],
        )
        if s.botocore_files_touched:
            for f in s.botocore_files_touched:
                lines.append("      - " + f)
        else:
            lines.append("      []")
        lines.extend(
            [
                "    rationale: |",
                "      TODO: one paragraph explaining WHY this was "
                + s.expected
                + ".",
                "    notes: null",
                "",
            ],
        )
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
        "--limit",
        type=int,
        default=40,
        help="Max PRs to scan (default 40)",
    )
    args = parser.parse_args()

    if not args.clone.exists():
        sys.stderr.write(
            "Bare botocore clone not found at " + str(args.clone) + ".\n"
            "    git clone --bare <botocore-url> " + str(args.clone) + "\n"
            "    git -C " + str(args.clone) + " fetch --tags\n",
        )
        return 2

    overridden = overridden_basenames()
    prs = list_candidate_prs(args.limit)
    scenarios = scenarios_from_prs(prs, args.clone, overridden)

    yaml = render_yaml(scenarios)
    args.out.write_text(yaml)
    print("Wrote " + str(len(scenarios)) + " scenarios to " + str(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
