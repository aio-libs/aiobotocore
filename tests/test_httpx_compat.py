"""Tests for the httpx / httpx2 backend resolution (aiobotocore._httpx).

The httpx backend prefers httpx2 and falls back to the legacy httpx package,
which is deprecated. Mirrors authlib's httpx compat tests.
"""

import importlib
import sys
import warnings

import pytest

from aiobotocore import _httpx
from aiobotocore.httpxsession import HttpxSession

pytestmark = pytest.mark.skipif(
    _httpx.httpx is None,
    reason="requires an httpx backend (httpx2 or httpx) to be installed",
)


def test_prefers_httpx2_when_available():
    httpx2 = pytest.importorskip("httpx2")
    reloaded = importlib.reload(_httpx)
    try:
        assert reloaded.httpx is httpx2
        assert reloaded.HTTPX_IS_LEGACY is False
    finally:
        importlib.reload(_httpx)


def test_falls_back_to_legacy_httpx(monkeypatch):
    legacy_httpx = pytest.importorskip("httpx")
    # Setting the entry to None makes ``import httpx2`` raise ImportError,
    # forcing the shim down its legacy-httpx fallback branch.
    monkeypatch.setitem(sys.modules, "httpx2", None)
    try:
        reloaded = importlib.reload(_httpx)
        assert reloaded.httpx is legacy_httpx
        assert reloaded.HTTPX_IS_LEGACY is True
    finally:
        monkeypatch.undo()
        importlib.reload(_httpx)


def test_httpxsession_warns_when_using_legacy_httpx(monkeypatch):
    monkeypatch.setattr("aiobotocore.httpxsession.HTTPX_IS_LEGACY", True)
    monkeypatch.setattr("aiobotocore.httpxsession._LEGACY_HTTPX_WARNED", False)
    with pytest.warns(DeprecationWarning, match="httpx2"):
        HttpxSession()


def test_httpxsession_warns_only_once_for_many_instances(monkeypatch):
    monkeypatch.setattr("aiobotocore.httpxsession.HTTPX_IS_LEGACY", True)
    monkeypatch.setattr("aiobotocore.httpxsession._LEGACY_HTTPX_WARNED", False)
    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always")
        for _ in range(3):
            HttpxSession()
    deprecations = [
        r for r in records if issubclass(r.category, DeprecationWarning)
    ]
    assert len(deprecations) == 1


def test_httpxsession_does_not_warn_on_httpx2(monkeypatch, recwarn):
    monkeypatch.setattr("aiobotocore.httpxsession.HTTPX_IS_LEGACY", False)
    HttpxSession()
    assert not any(
        issubclass(w.category, DeprecationWarning) for w in recwarn.list
    )
