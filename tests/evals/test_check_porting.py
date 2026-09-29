"""Tests for plugins/aiobotocore-bot/evals/check_porting.py."""

from __future__ import annotations

import importlib
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
        assert porting.override_changes(diff, "abc^") == {
            "aiobotocore/endpoint.py :: AioEndpoint._needs_retry"
        }
