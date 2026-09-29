#!/usr/bin/env python3
"""Evaluate the check-override-drift skill against labeled aiobotocore diffs.

Each case in drift_scenarios.yaml is a committed diff (drift_fixtures/), the
aiobotocore commit it applies to, and the botocore version its overrides should
mirror. The model gets the check-override-drift skill as system prompt, the
diff, the override registry as of the base commit, and the botocore source of
every function the diff touches, then its top-line verdict (`clean` |
`cosmetic-drift` | `behavioral-drift`) is compared against the label.

Pre-computed inputs skip the skill's tool-orchestration layer so the eval
isolates classification quality.

Run:

    uv run python plugins/aiobotocore-bot/evals/check_override_drift.py --runs 3

Env:

    ANTHROPIC_API_KEY — required
    BOTOCORE_CLONE    — optional, default /tmp/botocore (bare clone of boto/botocore)

Exits 0 if every case passes the majority vote, 1 otherwise.
"""

from __future__ import annotations

import argparse
import ast
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
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    EFFORT_LEVELS,
    REPO_ROOT,
    classify_output_schema,
    invoke_and_classify,
    load_skill_body,
    new_client,
    overridden_symbols,
    parse_scenarios_yaml,
    require_env,
    run_cases_concurrent,
    usage_summary,
)

SKILL_PATH = (
    REPO_ROOT / "plugins/aiobotocore-bot/skills/check-override-drift/SKILL.md"
)
SCENARIOS_PATH = (
    REPO_ROOT / "plugins/aiobotocore-bot/evals/drift_scenarios.yaml"
)
FIXTURES_DIR = REPO_ROOT / "plugins/aiobotocore-bot/evals/drift_fixtures"
BOTOCORE_CLONE = Path(os.environ.get("BOTOCORE_CLONE", "/tmp/botocore"))

VALID_VERDICTS = {"clean", "cosmetic-drift", "behavioral-drift"}
CLASSIFY_SCHEMA = classify_output_schema(
    verdict_enum=sorted(VALID_VERDICTS),
)

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? ")
_ADDED_DEF_RE = re.compile(r"^\+[ \t]*(?:async[ \t]+)?def (\w+)", re.MULTILINE)


@dataclass
class Case:
    id: str
    title: str
    expected: str
    fixture: str
    base_commit: str
    botocore_version: str


def load_scenarios(path: Path) -> list[Case]:
    rows = parse_scenarios_yaml(
        path,
        {"title", "expected", "fixture", "base_commit", "botocore_version"},
        row_key="id",
    )
    return [
        Case(
            id=r["id"],
            title=r.get("title", ""),
            expected=r["expected"],
            fixture=r["fixture"],
            base_commit=r["base_commit"],
            botocore_version=r["botocore_version"],
        )
        for r in rows
    ]


def touched_old_lines(diff: str) -> dict[str, set[int]]:
    """Old-file line numbers each file's hunks change, keyed by repo path.

    An added line is attributed to the old line just before it, so a docstring
    inserted after a `def` lands inside that def.
    """
    touched: dict[str, set[int]] = {}
    path: str | None = None
    old_line = 0
    for line in diff.splitlines():
        if line.startswith("--- "):
            continue
        if line.startswith("+++ "):
            path = line.removeprefix("+++ b/").strip()
            touched.setdefault(path, set())
            continue
        if path is None:
            continue
        if m := _HUNK_RE.match(line):
            old_line = int(m.group(1))
            continue
        if line.startswith("-"):
            touched[path].add(old_line)
            old_line += 1
        elif line.startswith("+"):
            touched[path].add(max(old_line - 1, 1))
        elif line.startswith(" ") or not line:
            old_line += 1
    return touched


def _definitions(source: str) -> list[tuple[str, int, int, ast.AST]]:
    """(qualified name, first line, last line, node) for every def and class."""
    defs: list[tuple[str, int, int, ast.AST]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                name = f"{prefix}{child.name}"
                first = min(
                    [child.lineno]
                    + [d.lineno for d in getattr(child, "decorator_list", [])]
                )
                defs.append((name, first, child.end_lineno or first, child))
                walk(child, f"{name}.")

    walk(ast.parse(source), "")
    return defs


def changed_definitions(source: str, lines: set[int]) -> set[str]:
    """Innermost def/class enclosing each changed line; module-level lines are skipped."""
    defs = _definitions(source)
    names: set[str] = set()
    for line in lines:
        enclosing = [d for d in defs if d[1] <= line <= d[2]]
        if enclosing:
            names.add(max(enclosing, key=lambda d: d[1])[0])
    return names


def botocore_name(aio_name: str) -> str:
    """`AioEndpoint._needs_retry` -> `Endpoint._needs_retry` (also `Async*`)."""
    parts = []
    for part in aio_name.split("."):
        for prefix in ("Aio", "Async"):
            rest = part.removeprefix(prefix)
            if rest != part and rest[:1].isupper():
                part = rest
                break
        parts.append(part)
    return ".".join(parts)


def _file_sections(diff: str) -> dict[str, str]:
    """Split a diff into per-file sections keyed by the new path."""
    sections: dict[str, str] = {}
    for part in re.split(r"(?m)^(?=diff --git )", diff):
        if m := re.search(r"(?m)^\+\+\+ b/(\S+)", part):
            sections[m.group(1)] = part
    return sections


def botocore_sources(
    case: Case, diff: str
) -> list[tuple[str, str, str | None]]:
    """(aiobotocore path :: name, botocore path :: name, botocore source) per touched def.

    Looks each def up by its de-Aio'd qualified name in the matching botocore
    file; a method of an aiobotocore-only mixin falls back to any botocore def
    with the same method name in that file.
    """
    out: list[tuple[str, str, str | None]] = []
    for path, lines in sorted(touched_old_lines(diff).items()):
        rel = path.removeprefix("aiobotocore/")
        try:
            base = subprocess.check_output(
                ["git", "show", f"{case.base_commit}:{path}"],
                cwd=REPO_ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            continue
        try:
            botocore_src = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(BOTOCORE_CLONE),
                    "show",
                    f"{case.botocore_version}:botocore/{rel}",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            botocore_src = None
        bdefs = _definitions(botocore_src) if botocore_src else []
        added = set(_ADDED_DEF_RE.findall(_file_sections(diff).get(path, "")))
        seen: set[str] = set()
        classes = {
            n
            for n, _, _, node in _definitions(base)
            if isinstance(node, ast.ClassDef)
        }
        for aio_name in sorted(changed_definitions(base, lines)):
            want = botocore_name(aio_name)
            match = [d for d in bdefs if d[0] == want] or [
                d
                for d in bdefs
                if d[0].rsplit(".", 1)[-1] == want.rsplit(".", 1)[-1]
            ]
            if not match:
                # a class-level hit is usually an insertion point; its methods are matched on their own
                if aio_name in classes:
                    continue
                out.append(
                    (
                        f"{path} :: {aio_name}",
                        f"botocore/{rel} :: {want}",
                        None,
                    )
                )
                continue
            for name, _, _, node in match[:3]:
                seen.add(name)
                out.append(
                    (
                        f"{path} :: {aio_name}",
                        f"botocore/{rel} :: {name}",
                        ast.get_source_segment(botocore_src, node),
                    )
                )
        # defs the diff adds don't exist in the base file, so match them by name
        for new_def in sorted(added):
            for name, _, _, node in [
                d for d in bdefs if d[0].rsplit(".", 1)[-1] == new_def
            ][:3]:
                if name not in seen:
                    seen.add(name)
                    out.append(
                        (
                            f"{path} :: {new_def} (added)",
                            f"botocore/{rel} :: {name}",
                            ast.get_source_segment(botocore_src, node),
                        )
                    )
    return out


def build_user_message(
    case: Case,
    diff: str,
    overrides: set[str],
    sources: list[tuple[str, str, str | None]],
) -> str:
    overrides_block = "\n".join(f"- {s}" for s in sorted(overrides))
    sources_block = "\n\n".join(
        f"### {bname} (for {aname})\n\n```python\n{src}\n```"
        if src
        else f"### {aname}\n\nNo botocore counterpart named `{bname}`."
        for aname, bname, src in sources
    )
    return (
        textwrap.dedent(
            """
        Run the override-drift classifier on this change ({title}).

        The diff below is the complete aiobotocore change. Apply your
        classification rules from the system prompt, comparing each
        added/changed line against the matching botocore function shown
        below (botocore {botocore_version}). For `aiobotocore/` files without
        a botocore mirror (e.g. httpxsession.py), mark the file as
        out-of-scope and skip.

        ## Authoritative aiobotocore override registry

        These are the botocore symbols aiobotocore overrides (from
        `tests/test_patches.py`). Use this to distinguish a tracked
        override that must mirror botocore from new aiobotocore-only
        code that isn't under drift-check scope.

        {overrides_block}

        ```diff
        {diff}
        ```

        ## Matching botocore source (botocore {botocore_version})

        {sources_block}

        Output protocol:

        1. Reason through each changed function.
        2. Your response is JSON with your final `verdict` (one of
           `clean`, `cosmetic-drift`, `behavioral-drift`) and a
           `rationale` containing the per-function breakdown plus a
           roll-up summary. It is the authoritative output — do not
           emit an OVERRIDE_DRIFT label.
        """,
        )
        .format(
            title=case.title,
            botocore_version=case.botocore_version,
            diff=diff,
            overrides_block=overrides_block,
            sources_block=sources_block,
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
        action="append",
        help="Only evaluate these scenario ids (repeatable)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Anthropic model to use (default: %(default)s)",
    )
    parser.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default=DEFAULT_EFFORT,
        help="Effort level (default: %(default)s)",
    )
    parser.add_argument(
        "--json-out", type=Path, help="Write full per-run results here"
    )
    args = parser.parse_args()

    # upload-artifact needs a file even if the run aborts mid-way
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps({"error": "not-started", "results": []}, indent=2)
        )

    if not BOTOCORE_CLONE.exists():
        sys.stderr.write(
            f"Bare botocore clone not found at {BOTOCORE_CLONE}.\n"
            f"    git clone --bare <botocore-repo-url> {BOTOCORE_CLONE}\n"
            f"    git -C {BOTOCORE_CLONE} fetch --tags\n",
        )
        return 2
    require_env("ANTHROPIC_API_KEY")

    skill_body = load_skill_body(SKILL_PATH)
    cases = load_scenarios(SCENARIOS_PATH)
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c.id in wanted]

    if not cases:
        sys.stderr.write("No cases to evaluate.\n")
        return 2

    print(
        f"Evaluating {len(cases)} case(s) x {args.runs} run(s) with {args.model} @ effort={args.effort}"
    )

    client = new_client()
    messages: dict[str, str] = {}
    for case in cases:
        diff = (FIXTURES_DIR / case.fixture).read_text()
        messages[case.id] = build_user_message(
            case,
            diff,
            overridden_symbols(case.base_commit),
            botocore_sources(case, diff),
        )

    async def invoke_one(case: Case) -> str:
        verdict, _raw, _parsed = await invoke_and_classify(
            client,
            skill_body,
            messages[case.id],
            args.model,
            args.effort,
            CLASSIFY_SCHEMA,
        )
        if verdict not in VALID_VERDICTS and verdict != "parse-error":
            verdict = f"unknown:{verdict}"
        return verdict

    per_case_verdicts = await run_cases_concurrent(
        cases, args.runs, invoke_one
    )

    results: list[dict] = []
    failures: list[dict] = []
    for case, verdicts in zip(cases, per_case_verdicts, strict=True):
        print(f"\n{case.id} [{case.expected}] {case.title}")
        for i, v in enumerate(verdicts, 1):
            ok = "PASS" if v == case.expected else "FAIL"
            print(f"  run {i}: {v}  {ok}")
        majority, count = Counter(verdicts).most_common(1)[0]
        passed = majority == case.expected and count > args.runs // 2
        status = "PASS" if passed else "FAIL"
        print(f"  majority {majority} ({count}/{args.runs}): {status}")
        result = {
            "id": case.id,
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
    for label in ("behavioral-drift", "cosmetic-drift", "clean"):
        rows = [r for r in results if r["expected"] == label]
        right = sum(r["passed"] for r in rows)
        print(f"  {label}: {right}/{len(rows)} cases by majority")
    missed = [
        r["id"]
        for r in results
        if r["expected"] == "behavioral-drift"
        and r["majority"] in ("clean", "cosmetic-drift")
    ]
    print(f"  behavioral drift missed: {len(missed)} {missed or ''}".rstrip())
    for f in failures:
        print(
            f"  FAIL {f['id']}: expected {f['expected']}, got {f['verdicts']}"
        )

    if args.json_out:
        args.json_out.write_text(json.dumps(results, indent=2))
        print(f"Wrote {args.json_out}")

    if summary := usage_summary():
        print(summary)

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
