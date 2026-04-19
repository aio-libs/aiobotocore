#!/usr/bin/env python3
"""Evaluate the check-async-need skill against historical sync PRs.

For each merged botocore-sync PR, the correct port-vs-no-port verdict is known
via `aiobotocore_port_happened` — did the PR actually modify overridden `.py`
code? Replay the classifier against the pre-computed botocore diff and check
whether it agrees.

The eval pre-computes the diff and passes it to the model as input, bypassing
the tool-orchestration layer. That isolates classification quality — the exact
thing we worry about when prompts drift.

Run:

    uv run python plugins/aiobotocore-bot/evals/check_async_need.py

Env:

    ANTHROPIC_API_KEY — required
    BOTOCORE_CLONE    — optional, default /tmp/botocore (bare clone of boto/botocore)

Exits 0 if every case passes the majority vote, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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
    aiobotocore_port_happened,
    derive_versions,
    invoke_and_parse,
    list_sync_prs,
    load_skill_body,
    new_client,
    overridden_paths,
    overridden_symbols,
    parse_scenarios_yaml,
    require_env,
    run_cases_concurrent,
)

SKILL_PATH = (
    REPO_ROOT / "plugins/aiobotocore-bot/skills/check-async-need/SKILL.md"
)
SCENARIOS_PATH = REPO_ROOT / "plugins/aiobotocore-bot/evals/scenarios.yaml"
BOTOCORE_CLONE = Path(os.environ.get("BOTOCORE_CLONE", "/tmp/botocore"))

# Tolerate markdown-bold wrappers and leading whitespace — `**CLASSIFICATION**:`
# and `CLASSIFICATION:` are both plausible model outputs; the previous
# strict `^CLASSIFICATION:` would miss the bolded form and return
# parse-error. Matches on any line start (MULTILINE) with optional
# leading whitespace/asterisks around the label.
VERDICT_RE = re.compile(
    r"^\s*\**\s*CLASSIFICATION\s*\**\s*:\s*(\S+)",
    re.MULTILINE,
)


@dataclass
class Case:
    pr: int
    title: str
    from_ver: str
    to_ver: str
    expected: str  # "no-port" | "port-required"


def _case_from_dict(d: dict[str, str]) -> Case:
    return Case(
        pr=int(d["pr"]),
        title=d.get("title", ""),
        from_ver=d["from"],
        to_ver=d["to"],
        expected=d["expected"],
    )


def load_scenarios_yaml(path: Path) -> list[Case] | None:
    rows = parse_scenarios_yaml(path, {"title", "expected", "from", "to"})
    return [_case_from_dict(r) for r in rows] if rows else None


def list_historical_cases(limit: int) -> list[Case]:
    cases: list[Case] = []
    for pr in list_sync_prs(limit * 2):
        if "botocore" not in pr["title"].lower():
            continue
        if not (versions := derive_versions(pr["mergeCommit"]["oid"])):
            continue
        from_ver, to_ver, _ = versions
        if from_ver == to_ver:
            continue
        port_happened = aiobotocore_port_happened(pr["number"])
        if port_happened is None:
            continue
        expected = "port-required" if port_happened else "no-port"
        cases.append(
            Case(
                pr=pr["number"],
                title=pr["title"],
                from_ver=from_ver,
                to_ver=to_ver,
                expected=expected,
            )
        )
        if len(cases) >= limit:
            break
    return cases


def compute_filtered_diff(case: Case, overridden: set[str]) -> str:
    """Diff between two botocore tags, restricted to overridden files.

    `overridden` holds relative paths under aiobotocore/ (e.g. 'client.py',
    'retries/adaptive.py'); each becomes a 'botocore/<path>' pathspec. Nested
    paths matter — retries/*.py syncs would otherwise be silently skipped.
    """
    pathspecs = [f"botocore/{p}" for p in overridden]
    try:
        return subprocess.check_output(
            [
                "git",
                "-C",
                str(BOTOCORE_CLONE),
                "diff",
                f"{case.from_ver}..{case.to_ver}",
                "--",
                *pathspecs,
            ],
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to diff {case.from_ver}..{case.to_ver} in "
            f"{BOTOCORE_CLONE}. Ensure the clone exists and both tags "
            f"are fetched. Original: {e}",
        ) from e


def build_user_message(case: Case, diff: str, overrides: set[str]) -> str:
    overrides_block = "\n".join(f"- {s}" for s in sorted(overrides))
    return (
        textwrap.dedent(
            """
        Classify the following botocore diff using the rules in your system prompt.
        from={from_ver} to={to_ver}

        The diff below is ALREADY FILTERED to changes in botocore files that have a
        mirror in aiobotocore/. Do NOT re-run git diff — use this content directly.

        ## Authoritative aiobotocore override registry

        These are the ONLY botocore symbols aiobotocore currently overrides
        (from `tests/test_patches.py`, the source of truth). For a
        changed function to drive a `port-required` verdict, its name or
        dotted name MUST appear in this list. Do not assume overrides from
        context — verify against this list.

        {overrides_block}

        ## Diff

        ```diff
        {diff}
        ```

        Output format — strict:

        1. The VERY FIRST line of your response must be `CLASSIFICATION: <verdict>`
           where <verdict> is one of `no-port`, `port-required`, or `ambiguous`.
           No preamble, no explanation, no markdown formatting on this line.
        2. Any supporting reasoning goes AFTER the classification line, per Step 4
           of your system prompt.
        """,
        )
        .format(
            from_ver=case.from_ver,
            to_ver=case.to_ver,
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
        "--limit", type=int, default=8, help="Max historical PRs to evaluate"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help="Anthropic model to use"
    )
    parser.add_argument(
        "--case",
        type=int,
        action="append",
        help="Only evaluate these PR numbers (repeatable)",
    )
    parser.add_argument(
        "--json-out", type=Path, help="Write full per-run results here"
    )
    args = parser.parse_args()

    if not BOTOCORE_CLONE.exists():
        sys.stderr.write(
            f"Bare botocore clone not found at {BOTOCORE_CLONE}.\n"
            f"    git clone --bare <botocore-repo-url> {BOTOCORE_CLONE}\n"
            f"    git -C {BOTOCORE_CLONE} fetch --tags\n"
            "    (see https://github.com/boto/botocore for the URL)\n",
        )
        return 2
    require_env("ANTHROPIC_API_KEY")

    skill_body = load_skill_body(SKILL_PATH)
    overridden = overridden_paths()
    override_symbols = overridden_symbols()
    print(
        f"Overridden files: {len(overridden)} ({', '.join(sorted(overridden)[:3])}, ...)"
    )
    print(f"Override symbols from test_patches.py: {len(override_symbols)}")

    # Prefer the committed scenarios.yaml (faster, deterministic, has rationales).
    yaml_cases = load_scenarios_yaml(SCENARIOS_PATH)
    if yaml_cases is not None:
        print(f"Using committed scenarios.yaml ({len(yaml_cases)} cases)")
        # scenarios.yaml is sorted oldest-first by PR number. Slice from the
        # END so the default --limit evaluates the most recent N cases — those
        # exercise current classifier criteria against current botocore; older
        # cases are stable regression baselines and can be hit via --case.
        cases = yaml_cases[-args.limit :] if args.limit > 0 else yaml_cases
    else:
        print("scenarios.yaml not found — deriving from gh pr list")
        cases = list_historical_cases(args.limit)
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c.pr in wanted]
    print(
        f"Evaluating {len(cases)} historical PR(s) x {args.runs} run(s) with {args.model}"
    )

    client = new_client()

    # Pre-compute diffs once per case (used by all N runs).
    diffs: dict[int, str] = {}
    for case in cases:
        try:
            diffs[case.pr] = compute_filtered_diff(case, overridden)
        except RuntimeError as e:
            print(f"  SKIP #{case.pr}: {e}")

    runnable = [c for c in cases if c.pr in diffs]

    async def invoke_one(case: Case) -> tuple[str, str]:
        diff = diffs[case.pr]
        if not diff.strip():
            return ("no-port", "")
        user = build_user_message(case, diff, override_symbols)
        return await invoke_and_parse(
            client,
            skill_body,
            user,
            args.model,
            VERDICT_RE,
        )

    per_case_verdicts = await run_cases_concurrent(
        runnable, args.runs, invoke_one
    )

    results: list[dict] = []
    failures: list[dict] = []
    # zip without strict= for Python 3.9 compat; lengths are equal by
    # construction (per_case_verdicts is gathered over runnable).
    assert len(runnable) == len(per_case_verdicts)
    for case, pairs in zip(runnable, per_case_verdicts):
        verdicts = [v for v, _ in pairs]
        rationales = [r for _, r in pairs]
        print(f"\n#{case.pr} [{case.expected}] {case.title}")
        print(
            f"  from={case.from_ver} to={case.to_ver} diff={diffs[case.pr].count(chr(10))} lines"
        )
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
            "from": case.from_ver,
            "to": case.to_ver,
            "expected": case.expected,
            "verdicts": verdicts,
            "rationales": rationales,
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
