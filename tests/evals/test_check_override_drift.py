"""Tests for plugins/aiobotocore-bot/evals/check_override_drift.py."""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip('anthropic')
drift = importlib.import_module("check_override_drift")

_BASE = '''\
import asyncio


class AioEndpoint:
    async def _needs_retry(self, attempts):
        handler_response = None
        return handler_response

    async def _sleep(self, amount):
        await asyncio.sleep(amount)
'''


def test_touched_old_lines_attributes_additions_and_removals() -> None:
    diff = (
        "diff --git a/aiobotocore/endpoint.py b/aiobotocore/endpoint.py\n"
        "--- a/aiobotocore/endpoint.py\n"
        "+++ b/aiobotocore/endpoint.py\n"
        "@@ -5,3 +5,4 @@ class AioEndpoint:\n"
        "     async def _needs_retry(self, attempts):\n"
        "         handler_response = None\n"
        "+        if attempts >= 10:\n"
        "         return handler_response\n"
        "@@ -10,1 +11,1 @@\n"
        "-        await asyncio.sleep(amount)\n"
        "+        await asyncio.sleep(amount * 2)\n"
    )
    assert drift.touched_old_lines(diff) == {
        "aiobotocore/endpoint.py": {6, 10}
    }


def test_changed_definitions_picks_innermost_def() -> None:
    assert drift.changed_definitions(_BASE, {6, 10}) == {
        "AioEndpoint._needs_retry",
        "AioEndpoint._sleep",
    }
    assert drift.changed_definitions(_BASE, {1}) == set()


@pytest.mark.parametrize(
    ("aio_name", "botocore"),
    [
        ("AioEndpoint._needs_retry", "Endpoint._needs_retry"),
        ("AsyncClientRateLimiter", "ClientRateLimiter"),
        ("convert_to_response_dict", "convert_to_response_dict"),
        ("Aiohttp", "Aiohttp"),
    ],
)
def test_botocore_name(aio_name: str, botocore: str) -> None:
    assert drift.botocore_name(aio_name) == botocore


def test_committed_scenarios_are_well_formed() -> None:
    cases = drift.load_scenarios(drift.SCENARIOS_PATH)
    assert {c.expected for c in cases} == drift.VALID_VERDICTS
    assert len({c.id for c in cases}) == len(cases)
    for case in cases:
        assert (drift.FIXTURES_DIR / case.fixture).is_file(), case.id
        assert len(case.base_commit) == 40, case.id


def test_added_def_regex_does_not_cross_added_blank_lines() -> None:
    section = (
        "+\n"
        "     def unchanged(self):\n"
        "+    @property\n"
        "+    def checksum(self):\n"
        "+    async def read(self):\n"
    )
    assert drift._ADDED_DEF_RE.findall(section) == ["checksum", "read"]
