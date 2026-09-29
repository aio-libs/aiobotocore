"""Tests for plugins/aiobotocore-bot/evals/check_async_need.py."""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip('anthropic')
check_async_need = importlib.import_module("check_async_need")


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("no-port", "no-port"),
        ("port-required", "escalate"),
        ("ambiguous", "escalate"),
        ("parse-error", "parse-error"),
    ],
)
def test_decision_counts_ambiguous_as_escalation(
    verdict: str, expected: str
) -> None:
    assert check_async_need.decision(verdict) == expected
