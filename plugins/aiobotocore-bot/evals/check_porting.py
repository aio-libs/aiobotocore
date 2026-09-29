#!/usr/bin/env python3
"""Evaluate the botocore-sync porting stage against historical ports.

For each port-required case in scenarios.yaml, check out aiobotocore at the
sync's parent commit, run the async-need classifier the way the classify job
does, then run Claude Code headless with the production sync prompt limited to
the port path (Steps 5-6). The result is graded without an LLM judge:

- the hash test (tests/test_patches.py) passes against the target botocore
- the test suite passes on the default aiohttp backend (httpx is left to CI)
- the override functions the agent changed, compared with the real port's

Each case is one full agent run of up to --max-turns turns, so run a single
case first to measure cost.

Run:

    uv run python plugins/aiobotocore-bot/evals/check_porting.py --case 1744

Env:

    ANTHROPIC_API_KEY — required
    BOTOCORE_CLONE    — optional, default /tmp/botocore (bare clone of boto/botocore)

Needs the `claude` CLI and `rtk` on PATH. Exits 0 if every case's agent run
completes and its hash test and test suite pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import check_async_need as can
from _common import (
    DEFAULT_EFFORT,
    EFFORT_LEVELS,
    LOWER_RE,
    REPO_ROOT,
    UPPER_RE,
    _git_show,
    async_names,
    invoke_and_classify,
    load_skill_body,
    new_client,
    overridden_paths,
    overridden_symbols,
    require_env,
    usage_summary,
)
from check_override_drift import changed_definitions, touched_old_lines

SYNC_PROMPT_PATH = REPO_ROOT / ".github/botocore-sync-prompt.md"
PLUGIN_DIR = REPO_ROOT / "plugins/aiobotocore-bot"
PORT_MODEL = "opus"

EVAL_INSTRUCTIONS = """

## Evaluation run

This run replays a historical botocore sync to evaluate porting. Steps 1-4 are
already resolved: there is no feedback issue and no existing sync PR, and the
classifier verdict above is final. Work only in the current checkout. Complete
the port path (Step 5) and validation (Step 6), then stop and summarize what you
changed. Do not run Step 7 or later: make no commits, pushes, pull requests,
issues or comments.
"""


def render_prompt(template: str, values: dict[str, str]) -> str:
    """Substitute `$NAME` placeholders the way the workflow's envsubst does."""
    for name in sorted(values, key=len, reverse=True):
        template = template.replace(f"${name}", values[name])
    return template


def override_changes(
    diff: str, base_commit: str, read_new: Callable[[str], str]
) -> set[str]:
    """`file :: qualified name` of every aiobotocore def a diff changes or adds."""
    changed: set[str] = set()
    for path, lines in touched_old_lines(diff).items():
        if not (path.startswith("aiobotocore/") and path.endswith(".py")):
            continue
        try:
            source = _git_show(base_commit, path)
        except subprocess.CalledProcessError:
            source = read_new(path)
            lines = set(range(1, len(source.splitlines()) + 1))
        changed |= {
            f"{path} :: {n}" for n in changed_definitions(source, lines)
        }
    return changed


def _progress(message: str) -> None:
    print(f"  {time.strftime('%H:%M:%S')} {message}", flush=True)


def _worktree_env() -> dict[str, str]:
    """This process's env minus VIRTUAL_ENV, which would point uv at the eval's venv."""
    return {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


def _run(cmd: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, text=True, capture_output=True, env=_worktree_env(), **kw
    )


def _describe_tool(block: dict) -> str:
    inp = block.get("input") or {}
    detail = next(
        (
            inp[k]
            for k in ("command", "file_path", "pattern", "path")
            if inp.get(k)
        ),
        "",
    )
    first_line = str(detail).splitlines()[0] if detail else ""
    return f"{block['name']} {first_line}".rstrip()[:160]


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def run_agent(cmd: list[str], cwd: Path, timeout: int) -> dict:
    """Run the agent, logging each tool call, then kill anything it left running.

    A backgrounded test run left alongside the grading run crashes pytest-xdist.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=_worktree_env(),
    )
    timer = threading.Timer(timeout, _kill_group, (proc.pid,))
    timer.start()
    result = None
    try:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                result = event
            elif event.get("type") == "assistant":
                for block in event["message"].get("content", []):
                    if block.get("type") == "tool_use":
                        _progress(f"agent: {_describe_tool(block)}")
        proc.wait()
    finally:
        timed_out = not timer.is_alive()
        timer.cancel()
        _kill_group(proc.pid)
        subprocess.run(["pkill", "-9", "-f", str(cwd)])
    if result is None:
        reason = (
            f"timed out after {timeout}s"
            if timed_out
            else "exited without a result"
        )
        result = {"is_error": True, "result": reason}
    return result


async def classify(
    client, case: can.Case, parent: str, effort: str
) -> tuple[str, str]:
    """Run the classify stage for the case: (verdict, per-function rationale)."""
    paths = overridden_paths(parent)
    diff = can.compute_filtered_diff(case, paths)
    user = can.build_user_message(
        case, diff, overridden_symbols(parent), *async_names(parent)
    )
    verdict, _raw, parsed = await invoke_and_classify(
        client,
        load_skill_body(can.SKILL_PATH),
        user,
        "claude-sonnet-5-5",
        effort,
        can.CLASSIFY_SCHEMA,
    )
    return verdict, (parsed or {}).get("rationale") or "(none)"


def affected_files(case: can.Case, parent: str) -> str:
    """The classify job's affected-file list: changed botocore files with a mirror."""
    out = subprocess.check_output(
        [
            "git",
            "-C",
            str(can.BOTOCORE_CLONE),
            "diff",
            "--name-only",
            f"{case.from_ver}..{case.to_ver}",
            "--",
            "botocore/",
        ],
        text=True,
    )
    mirrors = overridden_paths(parent)
    return ",".join(
        f"aiobotocore/{rel}"
        for rel in (p.removeprefix("botocore/") for p in out.splitlines())
        if rel in mirrors
    )


async def run_case(case: can.Case, args, client) -> dict:
    parent = f"{case.merge_commit}^"
    pyproject = _git_show(parent, "pyproject.toml")
    upper = UPPER_RE.search(pyproject).group(1)
    lower = LOWER_RE.search(pyproject).group(1)
    _progress("classifying")
    verdict, rationale = await classify(
        client, case, parent, args.classify_effort
    )
    _progress(f"classified as {verdict}")
    values = {
        "LATEST_BOTOCORE": case.to_ver,
        "LAST_SUPPORTED": case.from_ver,
        "CURRENT_UPPER": upper,
        "CURRENT_LOWER": lower,
        "CLASSIFIER_VERDICT": "port-required",
        "CLASSIFIER_SUMMARY": f"(eval) classifier said {verdict}; the case is a known port",
        "CLASSIFIER_RATIONALE": rationale,
        "AFFECTED_AIOBOTOCORE_FILES": affected_files(case, parent),
    }
    prompt = (
        render_prompt(SYNC_PROMPT_PATH.read_text(), values) + EVAL_INSTRUCTIONS
    )

    with tempfile.TemporaryDirectory(prefix=f"port-{case.pr}-") as td:
        wt = Path(td) / "aiobotocore"
        subprocess.check_call(
            ["git", "worktree", "add", "--detach", str(wt), parent],
            cwd=REPO_ROOT,
        )
        try:
            _progress("preparing worktree")
            frozen = ["--frozen"] if (wt / "uv.lock").exists() else []
            _run(["uv", "sync", *frozen], wt, check=True)
            _run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    ".venv/bin/python",
                    f"botocore=={case.to_ver}",
                ],
                wt,
                check=True,
            )
            _progress(
                f"worktree ready; agent started ({args.model} @ {args.effort})"
            )
            result = run_agent(
                [
                    "claude",
                    "-p",
                    prompt,
                    "--model",
                    args.model,
                    "--effort",
                    args.effort,
                    "--max-turns",
                    str(args.max_turns),
                    "--plugin-dir",
                    str(PLUGIN_DIR),
                    "--output-format",
                    "stream-json",
                    "--verbose",
                    "--dangerously-skip-permissions",
                    "--disallowedTools",
                    "Bash(git commit:*)",
                    "Bash(git push:*)",
                    "Bash(gh:*)",
                ],
                wt,
                args.timeout,
            )
            _progress(
                f"agent finished: turns={result.get('num_turns')} "
                f"cost=${result.get('total_cost_usd')}; running hash test"
            )
            _run(["git", "add", "--intent-to-add", "--", "aiobotocore/"], wt)
            agent_diff = _run(["git", "diff", "--", "aiobotocore/"], wt).stdout
            got = override_changes(
                agent_diff, parent, lambda path: (wt / path).read_text()
            )
            hashes = _run(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "tests/test_patches.py",
                ],
                wt,
            )
            _progress(
                f"hash test {'passed' if hashes.returncode == 0 else 'failed'}; "
                "running test suite"
            )
            tests = _run(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-m",
                    "not localonly",
                    "-n",
                    "auto",
                    "--dist",
                    "worksteal",
                    "--http-backend=aiohttp",
                ],
                wt,
            )
            summary = (tests.stdout.strip().splitlines() or ["no output"])[-1]
            _progress(f"test suite: {summary}")
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt)],
                cwd=REPO_ROOT,
            )

    real_diff = subprocess.check_output(
        ["git", "diff", parent, case.merge_commit, "--", "aiobotocore/"],
        cwd=REPO_ROOT,
        text=True,
    )
    real = override_changes(
        real_diff, parent, lambda path: _git_show(case.merge_commit, path)
    )
    return {
        "pr": case.pr,
        "from": case.from_ver,
        "to": case.to_ver,
        "classifier_verdict": verdict,
        "hashes_pass": hashes.returncode == 0,
        "tests_pass": tests.returncode == 0,
        "tests_tail": tests.stdout[-1500:],
        "real_changes": sorted(real),
        "agent_changes": sorted(got),
        "recall": len(real & got) / len(real) if real else None,
        "precision": len(real & got) / len(got) if got else None,
        "agent_error": bool(result.get("is_error")),
        "agent_turns": result.get("num_turns"),
        "agent_cost_usd": result.get("total_cost_usd"),
        "agent_summary": (result.get("result") or "")[-3000:],
        "agent_diff": agent_diff,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        type=int,
        action="append",
        required=True,
        help="Port-required scenario PR numbers to replay (repeatable)",
    )
    parser.add_argument(
        "--model",
        default=PORT_MODEL,
        help="Claude Code model for the port (default: %(default)s, as botocore-sync uses)",
    )
    parser.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default="high",
        help="Effort for the port (default: %(default)s, as botocore-sync uses)",
    )
    parser.add_argument(
        "--classify-effort",
        choices=EFFORT_LEVELS,
        default=DEFAULT_EFFORT,
        help="Effort for the classify stage (default: %(default)s)",
    )
    parser.add_argument("--max-turns", type=int, default=150)
    parser.add_argument(
        "--timeout",
        type=int,
        default=3300,
        help="Per-case agent timeout, seconds",
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps({"error": "not-started"}))
    if not can.BOTOCORE_CLONE.exists():
        sys.stderr.write(
            f"Bare botocore clone not found at {can.BOTOCORE_CLONE}.\n"
        )
        return 2
    require_env("ANTHROPIC_API_KEY")

    by_pr = {c.pr: c for c in can.load_scenarios_yaml(can.SCENARIOS_PATH)}
    cases = []
    for pr in args.case:
        case = by_pr.get(pr)
        if case is None or case.expected != "port-required":
            sys.stderr.write(f"#{pr} is not a port-required scenario.\n")
            return 2
        cases.append(case)

    client = new_client()
    results = []
    for case in cases:
        print(
            f"\n#{case.pr} {case.from_ver} -> {case.to_ver} with {args.model} @ effort={args.effort}"
        )
        r = await run_case(case, args, client)
        results.append(r)
        print(f"  classifier: {r['classifier_verdict']}")
        print(
            f"  agent: turns={r['agent_turns']} cost=${r['agent_cost_usd']} error={r['agent_error']}"
        )
        print(
            f"  hashes pass: {r['hashes_pass']}  tests pass: {r['tests_pass']}"
        )
        print(f"  override recall: {r['recall']}  precision: {r['precision']}")
        print(f"  real:  {r['real_changes']}")
        print(f"  agent: {r['agent_changes']}")
        if args.json_out:
            args.json_out.write_text(json.dumps(results, indent=2))

    passed = sum(
        r["hashes_pass"] and r["tests_pass"] and not r["agent_error"]
        for r in results
    )
    print(
        f"\n== Summary: {passed}/{len(results)} ports completed and pass hashes and tests =="
    )
    cost = sum(r["agent_cost_usd"] or 0 for r in results)
    print(f"  agent cost: ${cost:.2f}")
    if summary := usage_summary():
        print(summary)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
