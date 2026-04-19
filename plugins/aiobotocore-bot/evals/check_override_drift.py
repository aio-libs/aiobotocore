#!/usr/bin/env python3
"""Evaluate /aiobotocore-bot:check-override-drift against labeled historical PRs.

Reads drift_scenarios.yaml (a small committed ground-truth file), fetches each
PR's diff via gh, invokes Claude with the check-override-drift command body as
system prompt and the diff as user input, and compares the top-line verdict
(`clean` | `cosmetic-drift` | `behavioral-drift`) against the label.

Narrow on purpose — same tradeoff as check_async_need.py: we pre-fetch inputs
and skip the command's tool-orchestration layer so the eval isolates the
classification quality.

Run:

    uv run --with anthropic python \\
        plugins/aiobotocore-bot/evals/check_override_drift.py --runs 3

Env:

    ANTHROPIC_API_KEY — required

Exits 0 if every case passes the majority vote, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

try:
    import anthropic
except ImportError:
    sys.stderr.write(
        "anthropic package not installed. Run with:\n"
        "    uv run --with anthropic python "
        "plugins/aiobotocore-bot/evals/check_override_drift.py\n",
    )
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMAND_PATH = (
    REPO_ROOT / "plugins/aiobotocore-bot/commands/check-override-drift.md"
)
SCENARIOS_PATH = (
    REPO_ROOT / "plugins/aiobotocore-bot/evals/drift_scenarios.yaml"
)
DEFAULT_MODEL = "claude-opus-4-7"

VERDICT_RE = re.compile(r"^OVERRIDE_DRIFT:\s*(\S+)", re.MULTILINE)
VALID_VERDICTS = {"clean", "cosmetic-drift", "behavioral-drift"}


@dataclass
class Case:
    pr: int
    title: str
    expected: str  # "clean" | "cosmetic-drift" | "behavioral-drift"


def load_command_body() -> str:
    text = COMMAND_PATH.read_text()
    if text.startswith("---"):
        _, _, rest = text.split("---", 2)
        return rest.strip()
    return text


def load_scenarios(path: Path) -> list[Case]:
    """Minimal YAML parser for the known drift_scenarios.yaml schema."""
    if not path.exists():
        sys.stderr.write(
            "drift_scenarios.yaml not found at " + str(path) + "\n"
        )
        return []
    cases: list[Case] = []
    current: dict[str, str] = {}
    in_rationale = False
    for raw in path.read_text().splitlines():
        line = raw.rstrip()
        if in_rationale:
            # Consume indented rationale/notes lines (block scalar continuation)
            if line.startswith("    ") or not line.strip():
                continue
            in_rationale = False
        if not line or line.lstrip().startswith("#") or line == "---":
            continue
        if line.startswith("- pr:"):
            if current:
                cases.append(_case_from_dict(current))
                current = {}
            current["pr"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("  ") and ":" in line:
            key, _, value = line.strip().partition(":")
            key = key.strip()
            value = value.strip()
            if key in {"title", "expected"}:
                current[key] = value.strip('"')
            elif key in {"rationale", "notes"} and value == "|":
                in_rationale = True
    if current:
        cases.append(_case_from_dict(current))
    return cases


def _case_from_dict(d: dict[str, str]) -> Case:
    return Case(
        pr=int(d["pr"]),
        title=d.get("title", ""),
        expected=d["expected"],
    )


def fetch_pr_diff(pr: int) -> str:
    """Fetch the unified diff for a PR via gh."""
    return subprocess.check_output(
        ["gh", "pr", "diff", str(pr), "--repo", "aio-libs/aiobotocore"],
        text=True,
    )


def invoke_classifier(
    client: anthropic.Anthropic,
    command_body: str,
    case: Case,
    diff: str,
    model: str,
) -> tuple[str, str]:
    """Run Claude once for a case; return (verdict, raw output)."""
    user = (
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

        Emit the exact structured output described in your Step 4, starting with
        `OVERRIDE_DRIFT: <verdict>`.

        ```diff
        {diff}
        ```
        """,
        )
        .format(pr=case.pr, title=case.title, diff=diff)
        .strip()
    )

    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=command_body,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(
        block.text
        for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    m = VERDICT_RE.search(raw)
    verdict = m.group(1).strip().rstrip(":").lower() if m else "parse-error"
    if verdict not in VALID_VERDICTS and verdict != "parse-error":
        verdict = "unknown:" + verdict
    return (verdict, raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Runs per case (majority vote)",
    )
    parser.add_argument(
        "--case",
        type=int,
        action="append",
        help="Only evaluate these PR numbers (repeatable)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Anthropic model to use",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Write full per-run results here",
    )
    args = parser.parse_args()

    if "ANTHROPIC_API_KEY" not in os.environ:
        sys.stderr.write("ANTHROPIC_API_KEY not set.\n")
        return 2

    command_body = load_command_body()
    cases = load_scenarios(SCENARIOS_PATH)
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c.pr in wanted]

    if not cases:
        sys.stderr.write("No cases to evaluate.\n")
        return 2

    print(
        "Evaluating "
        + str(len(cases))
        + " case(s) x "
        + str(args.runs)
        + " run(s) with "
        + args.model,
    )

    client = anthropic.Anthropic()
    results: list[dict] = []
    failures: list[dict] = []

    for case in cases:
        print("\n#" + str(case.pr) + " [" + case.expected + "] " + case.title)
        try:
            diff = fetch_pr_diff(case.pr)
        except subprocess.CalledProcessError as e:
            print("  SKIP: could not fetch PR diff: " + str(e))
            continue
        print("  diff: " + str(diff.count("\n")) + " lines")

        verdicts = []
        for i in range(args.runs):
            verdict, _raw = invoke_classifier(
                client,
                command_body,
                case,
                diff,
                args.model,
            )
            verdicts.append(verdict)
            ok = "PASS" if verdict == case.expected else "FAIL"
            print("  run " + str(i + 1) + ": " + verdict + "  " + ok)

        tally = Counter(verdicts)
        majority, count = tally.most_common(1)[0]
        passed = majority == case.expected and count > args.runs // 2
        status = "PASS" if passed else "FAIL"
        print(
            "  majority "
            + majority
            + " ("
            + str(count)
            + "/"
            + str(args.runs)
            + "): "
            + status,
        )

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
        "\n== Summary: "
        + str(len(results) - len(failures))
        + "/"
        + str(len(results))
        + " passed ==",
    )
    for f in failures:
        print(
            "  FAIL #"
            + str(f["pr"])
            + ": expected "
            + f["expected"]
            + ", got "
            + str(f["verdicts"]),
        )

    if args.json_out:
        args.json_out.write_text(json.dumps(results, indent=2))
        print("Wrote " + str(args.json_out))

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
