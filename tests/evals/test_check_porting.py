"""Tests for plugins/aiobotocore-bot/evals/check_porting.py."""

from __future__ import annotations

import importlib
import json
import subprocess
from unittest.mock import patch

import pytest

pytest.importorskip('anthropic')
porting = importlib.import_module("check_porting")


def test_render_prompt_substitutes_longest_names_first() -> None:
    template = (
        "to $LATEST_BOTOCORE from $LAST_SUPPORTED, verdict $CLASSIFIER_VERDICT"
    )
    assert porting.render_prompt(
        template,
        {
            "LATEST_BOTOCORE": "1.43.98",
            "LAST_SUPPORTED": "1.43.93",
            "CLASSIFIER_VERDICT": "port-required",
        },
    ) == ("to 1.43.98 from 1.43.93, verdict port-required")


def test_sync_prompt_placeholders_are_all_supplied() -> None:
    text = porting.SYNC_PROMPT_PATH.read_text()
    supplied = {
        "LATEST_BOTOCORE",
        "LAST_SUPPORTED",
        "CURRENT_UPPER",
        "CURRENT_LOWER",
        "CLASSIFIER_VERDICT",
        "CLASSIFIER_SUMMARY",
        "CLASSIFIER_RATIONALE",
        "AFFECTED_AIOBOTOCORE_FILES",
    }
    rendered = porting.render_prompt(text, {name: "x" for name in supplied})
    for name in supplied:
        assert f"${name}" not in rendered


def test_override_changes_names_changed_defs() -> None:
    base = (
        "class AioEndpoint:\n"
        "    async def _needs_retry(self):\n"
        "        return False\n"
    )
    diff = (
        "diff --git a/aiobotocore/endpoint.py b/aiobotocore/endpoint.py\n"
        "--- a/aiobotocore/endpoint.py\n"
        "+++ b/aiobotocore/endpoint.py\n"
        "@@ -3,1 +3,1 @@\n"
        "-        return False\n"
        "+        return True\n"
        "diff --git a/tests/test_x.py b/tests/test_x.py\n"
        "--- a/tests/test_x.py\n"
        "+++ b/tests/test_x.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-a\n"
        "+b\n"
    )
    with patch.object(porting, "_git_show", return_value=base):
        assert porting.override_changes(diff, "abc^", pytest.fail) == {
            "aiobotocore/endpoint.py :: AioEndpoint._needs_retry"
        }


def test_override_changes_names_defs_in_new_files() -> None:
    new = "class AioFoo:\n    async def bar(self):\n        return 1\n"
    diff = (
        "diff --git a/aiobotocore/foo.py b/aiobotocore/foo.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/aiobotocore/foo.py\n"
        "@@ -0,0 +1,3 @@\n"
        + "".join(f"+{line}\n" for line in new.splitlines())
    )
    missing = subprocess.CalledProcessError(128, "git show")
    with patch.object(porting, "_git_show", side_effect=missing):
        assert porting.override_changes(diff, "abc^", lambda path: new) == {
            "aiobotocore/foo.py :: AioFoo",
            "aiobotocore/foo.py :: AioFoo.bar",
        }


def test_run_agent_returns_the_result_event(tmp_path) -> None:
    stream = tmp_path / "stream.jsonl"
    events = [
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    }
                ]
            },
        },
        {"type": "result", "is_error": False, "num_turns": 2},
    ]
    stream.write_text("not json\n" + "\n".join(map(json.dumps, events)) + "\n")
    assert porting.run_agent(["cat", str(stream)], tmp_path, 30) == events[-1]


def test_run_agent_reports_a_missing_result(tmp_path) -> None:
    assert porting.run_agent(["true"], tmp_path, 30) == {
        "is_error": True,
        "result": "exited without a result",
    }


def test_run_agent_times_out(tmp_path) -> None:
    assert porting.run_agent(["sleep", "30"], tmp_path, 1) == {
        "is_error": True,
        "result": "timed out after 1s",
    }
