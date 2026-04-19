#!/usr/bin/env python3
"""Evaluate /aiobotocore-bot:check-async-need against historical sync PRs.

For each merged "Relax botocore" / "Bump botocore" PR, we know the correct
port-vs-no-port verdict from the PR title. Replay the classifier against the
pre-computed botocore diff and check whether it agrees.

The eval is narrow on purpose: it pre-computes the diff and passes it to the
model as input, bypassing the tool-orchestration layer. That isolates the
classification quality — the exact thing we worry about when prompts drift.

Run:

    uv run --with anthropic python plugins/aiobotocore-bot/evals/check_async_need.py

Env:

    ANTHROPIC_API_KEY — required
    BOTOCORE_CLONE    — optional, default /tmp/botocore (bare clone of boto/botocore)

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
        "    uv run --with anthropic python plugins/aiobotocore-bot/evals/check_async_need.py\n"
    )
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMAND_PATH = (
    REPO_ROOT / "plugins/aiobotocore-bot/commands/check-async-need.md"
)
SCENARIOS_PATH = REPO_ROOT / "plugins/aiobotocore-bot/evals/scenarios.yaml"
AIOBOTOCORE_DIR = REPO_ROOT / "aiobotocore"
BOTOCORE_CLONE = Path(os.environ.get("BOTOCORE_CLONE", "/tmp/botocore"))
DEFAULT_MODEL = "claude-opus-4-7"

VERDICT_RE = re.compile(r"^CLASSIFICATION:\s*(\S+)", re.MULTILINE)
UPPER_RE = re.compile(r'"botocore\s*>=\s*[\d.]+\s*,\s*<\s*([\d.]+)"')


@dataclass
class Case:
    pr: int
    title: str
    from_ver: str
    to_ver: str
    expected: str  # "no-port" | "port-required"


def load_command_body() -> str:
    """Return the classification command prompt (frontmatter stripped)."""
    text = COMMAND_PATH.read_text()
    if text.startswith("---"):
        _, _, rest = text.split("---", 2)
        return rest.strip()
    return text


def overridden_basenames() -> set[str]:
    """Filename-mirror convention: aiobotocore/foo.py overrides botocore/foo.py."""
    return {p.name for p in AIOBOTOCORE_DIR.glob("*.py")}


def decrement_patch(ver: str) -> str:
    parts = list(map(int, ver.split(".")))
    parts[-1] = max(0, parts[-1] - 1)
    return ".".join(map(str, parts))


def load_scenarios_yaml(path: Path) -> list[Case] | None:
    """Read scenarios.yaml if present. Returns None if file doesn't exist.

    Tiny YAML parser — only handles the subset that generate_scenarios.py emits.
    Avoids adding PyYAML as a dep for a single data file with a known shape.
    """
    if not path.exists():
        return None
    cases: list[Case] = []
    current: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.rstrip()
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
            if key in {"title", "expected", "from", "to"}:
                current[key] = value.strip('"')
    if current:
        cases.append(_case_from_dict(current))
    return cases


def _case_from_dict(d: dict[str, str]) -> Case:
    return Case(
        pr=int(d["pr"]),
        title=d.get("title", ""),
        from_ver=d["from"],
        to_ver=d["to"],
        expected=d["expected"],
    )


def list_historical_cases(limit: int) -> list[Case]:
    """Fetch merged sync PRs and derive (from, to, expected verdict) for each."""
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
            str(limit * 2),
            "--json",
            "number,title,mergeCommit",
        ],
    )
    prs = json.loads(out)
    cases: list[Case] = []
    for pr in prs:
        title_low = pr["title"].lower()
        if "relax" in title_low:
            expected = "no-port"
        elif "bump" in title_low:
            expected = "port-required"
        else:
            continue

        sha = pr["mergeCommit"]["oid"]
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
            continue

        if not (m_before := UPPER_RE.search(before)):
            continue
        if not (m_after := UPPER_RE.search(after)):
            continue

        from_ver = decrement_patch(m_before.group(1))
        to_ver = decrement_patch(m_after.group(1))
        if from_ver == to_ver:
            continue

        cases.append(
            Case(
                pr=pr["number"],
                title=pr["title"],
                from_ver=from_ver,
                to_ver=to_ver,
                expected=expected,
            ),
        )
        if len(cases) >= limit:
            break
    return cases


def compute_filtered_diff(case: Case, overridden: set[str]) -> str:
    """Diff between two botocore tags, restricted to overridden files."""
    pathspecs = ["botocore/" + name for name in overridden]
    try:
        return subprocess.check_output(
            [
                "git",
                "-C",
                str(BOTOCORE_CLONE),
                "diff",
                case.from_ver + ".." + case.to_ver,
                "--",
                *pathspecs,
            ],
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "Failed to diff "
            + case.from_ver
            + ".."
            + case.to_ver
            + " in "
            + str(BOTOCORE_CLONE)
            + ". Ensure the bare clone exists and both tags "
            "are fetched. Original: " + str(e),
        ) from e


def invoke_classifier(
    client: anthropic.Anthropic,
    command_body: str,
    case: Case,
    filtered_diff: str,
    model: str,
) -> tuple[str, str]:
    """Ask Claude to run the classifier on a pre-computed diff; return (verdict, raw)."""
    if not filtered_diff.strip():
        return ("no-port", "(pre-determined: empty filtered diff)")

    user = (
        textwrap.dedent(
            """
        Classify the following botocore diff using the rules in your system prompt.
        from={from_ver} to={to_ver}

        The diff below is ALREADY FILTERED to changes in botocore files that have a
        mirror in aiobotocore/. Do NOT re-run git diff — use this content directly.

        ```diff
        {diff}
        ```

        Emit the exact structured output described in Step 4 of your system prompt,
        starting with a line `CLASSIFICATION: <verdict>`.
        """,
        )
        .format(from_ver=case.from_ver, to_ver=case.to_ver, diff=filtered_diff)
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
    return (verdict, raw)


def main() -> int:
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
            "Bare botocore clone not found at " + str(BOTOCORE_CLONE) + ".\n"
            "    git clone --bare <botocore-repo-url> "
            + str(BOTOCORE_CLONE)
            + "\n"
            "    git -C " + str(BOTOCORE_CLONE) + " fetch --tags\n"
            "    (botocore-repo-url: see https://github.com/boto/botocore)\n",
        )
        return 2

    if "ANTHROPIC_API_KEY" not in os.environ:
        sys.stderr.write("ANTHROPIC_API_KEY not set.\n")
        return 2

    command_body = load_command_body()
    overridden = overridden_basenames()
    print(
        "Overridden files: "
        + str(len(overridden))
        + " ("
        + ", ".join(sorted(overridden)[:3])
        + ", ...)"
    )

    # Prefer the committed scenarios.yaml (faster, deterministic, has human
    # rationales). Fall back to deriving from gh pr list for unknown-PR cases.
    yaml_cases = load_scenarios_yaml(SCENARIOS_PATH)
    if yaml_cases is not None:
        print(
            "Using committed scenarios.yaml ("
            + str(len(yaml_cases))
            + " cases)"
        )
        cases = yaml_cases[: args.limit]
    else:
        print("scenarios.yaml not found — deriving from gh pr list")
        cases = list_historical_cases(args.limit)
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c.pr in wanted]
    print(
        "Evaluating "
        + str(len(cases))
        + " historical PR(s) x "
        + str(args.runs)
        + " run(s) with "
        + args.model,
    )

    client = anthropic.Anthropic()
    results: list[dict] = []
    failures: list[dict] = []

    for case in cases:
        print("\n#" + str(case.pr) + " [" + case.expected + "] " + case.title)
        print("  from=" + case.from_ver + " to=" + case.to_ver)
        try:
            diff = compute_filtered_diff(case, overridden)
        except RuntimeError as e:
            print("  SKIP: " + str(e))
            continue
        diff_lines = diff.count("\n")
        print("  filtered diff: " + str(diff_lines) + " lines")

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
        majority, majority_count = tally.most_common(1)[0]
        passed = majority == case.expected and majority_count > args.runs // 2
        status = "PASS" if passed else "FAIL"
        print(
            "  majority "
            + majority
            + " ("
            + str(majority_count)
            + "/"
            + str(args.runs)
            + "): "
            + status,
        )

        result = {
            "pr": case.pr,
            "title": case.title,
            "from": case.from_ver,
            "to": case.to_ver,
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
            + str(f["verdicts"])
        )

    if args.json_out:
        args.json_out.write_text(json.dumps(results, indent=2))
        print("Wrote " + str(args.json_out))

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
