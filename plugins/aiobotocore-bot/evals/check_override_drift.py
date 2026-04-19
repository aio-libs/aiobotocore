#!/usr/bin/env python3
"""Evaluate the check-override-drift skill against labeled historical PRs.

Reads drift_scenarios.yaml, fetches each PR's diff via gh, invokes Claude with
the check-override-drift skill as system prompt, and compares the top-line
verdict (`clean` | `cosmetic-drift` | `behavioral-drift`) against the label.

Pre-computed inputs skip the command's tool-orchestration layer so the eval
isolates classification quality.

Run:

    uv run python plugins/aiobotocore-bot/evals/check_override_drift.py --runs 3

Env:

    ANTHROPIC_API_KEY — required

Exits 0 if every case passes the majority vote, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from _common import (
    DEFAULT_MODEL,
    REPO_ROOT,
    invoke_and_parse,
    load_skill_body,
    new_client,
    overridden_symbols,
    parse_scenarios_yaml,
    require_env,
    run_cases_concurrent,
)

SKILL_PATH = (
    REPO_ROOT / "plugins/aiobotocore-bot/skills/check-override-drift/SKILL.md"
)
SCENARIOS_PATH = (
    REPO_ROOT / "plugins/aiobotocore-bot/evals/drift_scenarios.yaml"
)

# Matches `OVERRIDE_DRIFT:`, `**OVERRIDE_DRIFT**:`, or leading whitespace —
# same tolerance as check_async_need.py's VERDICT_RE.
VERDICT_RE = re.compile(
    r"^\s*\**\s*OVERRIDE_DRIFT\s*\**\s*:\s*(\S+)",
    re.MULTILINE,
)
VALID_VERDICTS = {"clean", "cosmetic-drift", "behavioral-drift"}


@dataclass
class Case:
    pr: int
    title: str
    expected: str


def _case_from_dict(d: dict[str, str]) -> Case:
    return Case(
        pr=int(d["pr"]),
        title=d.get("title", ""),
        expected=d["expected"],
    )


def load_scenarios(path: Path) -> list[Case]:
    rows = parse_scenarios_yaml(path, {"title", "expected"})
    return [_case_from_dict(r) for r in rows]


def fetch_pr_diff(pr: int) -> str:
    return subprocess.check_output(
        ["gh", "pr", "diff", str(pr), "--repo", "aio-libs/aiobotocore"],
        text=True,
    )


def build_user_message(case: Case, diff: str, overrides: set[str]) -> str:
    overrides_block = "\n".join(f"- {s}" for s in sorted(overrides))
    return (
        textwrap.dedent(
            """
        Run the override-drift classifier on PR #{pr} ({title}).

        The diff below is the complete PR diff. Apply your classification rules
        from the system prompt. For `aiobotocore/*.py` files that have a botocore
        mirror, compare each added/changed line against what the matching botocore
        function looks like (use your knowledge of the botocore source for the
        currently-pinned version; you can assume botocore is approximately at its
        latest stable release). For `aiobotocore/` files without a botocore mirror
        (e.g. httpxsession.py), mark the file as out-of-scope and skip.

        ## Authoritative aiobotocore override registry

        These are the botocore symbols aiobotocore overrides (from
        `tests/test_patches.py`). Use this to distinguish a tracked
        override that must mirror botocore from new aiobotocore-only
        code that isn't under drift-check scope.

        {overrides_block}

        Output format — strict:

        1. The VERY FIRST line of your response must be `OVERRIDE_DRIFT: <verdict>`
           where <verdict> is one of `clean`, `cosmetic-drift`, or `behavioral-drift`.
           No preamble, no explanation, no markdown formatting on this line.
        2. Any supporting reasoning goes AFTER the classification line, per Step 4
           of your system prompt.

        ```diff
        {diff}
        ```
        """,
        )
        .format(
            pr=case.pr,
            title=case.title,
            diff=diff,
            overrides_block=overrides_block,
        )
        .strip()
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=int, default=3, help="Runs per case (majority vote)"
    )
    parser.add_argument(
        "--case",
        type=int,
        action="append",
        help="Only evaluate these PR numbers (repeatable)",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help="Anthropic model to use"
    )
    parser.add_argument(
        "--json-out", type=Path, help="Write full per-run results here"
    )
    args = parser.parse_args()

    require_env("ANTHROPIC_API_KEY")

    skill_body = load_skill_body(SKILL_PATH)
    override_symbols = overridden_symbols()
    print(f"Override symbols from test_patches.py: {len(override_symbols)}")
    cases = load_scenarios(SCENARIOS_PATH)
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c.pr in wanted]

    if not cases:
        sys.stderr.write("No cases to evaluate.\n")
        return 2

    print(
        f"Evaluating {len(cases)} case(s) x {args.runs} run(s) with {args.model}"
    )

    client = new_client()
    diffs: dict[int, str] = {}
    for case in cases:
        try:
            diffs[case.pr] = fetch_pr_diff(case.pr)
        except subprocess.CalledProcessError as e:
            print(f"  SKIP #{case.pr}: could not fetch PR diff: {e}")

    runnable = [c for c in cases if c.pr in diffs]

    async def invoke_one(case: Case) -> str:
        diff = diffs[case.pr]
        user = build_user_message(case, diff, override_symbols)
        verdict, _raw = await invoke_and_parse(
            client,
            skill_body,
            user,
            args.model,
            VERDICT_RE,
        )
        if verdict not in VALID_VERDICTS and verdict != "parse-error":
            verdict = f"unknown:{verdict}"
        return verdict

    per_case_verdicts = await run_cases_concurrent(
        runnable, args.runs, invoke_one
    )

    results: list[dict] = []
    failures: list[dict] = []
    # zip without strict= for Python 3.9 compat; lengths are equal by
    # construction (per_case_verdicts is gathered over runnable).
    assert len(runnable) == len(per_case_verdicts)
    for case, verdicts in zip(runnable, per_case_verdicts):
        print(f"\n#{case.pr} [{case.expected}] {case.title}")
        print(f"  diff: {diffs[case.pr].count(chr(10))} lines")
        for i, v in enumerate(verdicts, 1):
            ok = "PASS" if v == case.expected else "FAIL"
            print(f"  run {i}: {v}  {ok}")
        majority, count = Counter(verdicts).most_common(1)[0]
        passed = majority == case.expected and count > args.runs // 2
        status = "PASS" if passed else "FAIL"
        print(f"  majority {majority} ({count}/{args.runs}): {status}")
        result = {
            "pr": case.pr,
            "title": case.title,
            "expected": case.expected,
            "verdicts": verdicts,
            "majority": majority,
            "passed": passed,
        }
        results.append(result)
        if not passed:
            failures.append(result)

    print(
        f"\n== Summary: {len(results) - len(failures)}/{len(results)} passed =="
    )
    for f in failures:
        print(
            f"  FAIL #{f['pr']}: expected {f['expected']}, got {f['verdicts']}"
        )

    if args.json_out:
        args.json_out.write_text(json.dumps(results, indent=2))
        print(f"Wrote {args.json_out}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
