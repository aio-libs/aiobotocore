"""Tests for plugins/aiobotocore-bot/evals/_common.py.

Covers the pure-logic helpers used by all three eval scripts. Subprocess-
dependent helpers (derive_versions, aiobotocore_port_happened, list_sync_prs)
are tested via mocked check_output so tests don't hit git or the gh API.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import _common


def test_load_command_body_strips_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "cmd.md"
    p.write_text(
        "---\n"
        "allowed-tools: Bash(ls:*)\n"
        "description: foo\n"
        "---\n"
        "\n"
        "Body content here.\n",
    )
    assert _common.load_command_body(p) == "Body content here."


def test_load_command_body_no_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "cmd.md"
    p.write_text("Plain body.\n")
    assert _common.load_command_body(p) == "Plain body.\n"


def test_decrement_patch() -> None:
    assert _common.decrement_patch("1.42.89") == "1.42.88"
    assert _common.decrement_patch("3.0.0") == "3.0.0"  # Clamped at 0


def test_overridden_paths_covers_nested() -> None:
    """Regression: earlier version used glob, missed retries/*.py."""
    paths = _common.overridden_paths()
    assert "client.py" in paths
    assert "retries/adaptive.py" in paths
    assert "retries/special.py" in paths


def test_parse_scenarios_yaml_basic(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(
        "---\n"
        "scenarios:\n"
        "- pr: 1202\n"
        '  title: "Bump botocore"\n'
        "  expected: port-required\n"
        '  from: "1.35.7"\n'
        '  to: "1.35.16"\n'
        "\n"
        "- pr: 1192\n"
        '  title: "Relax botocore"\n'
        "  expected: no-port\n"
        '  from: "1.35.4"\n'
        '  to: "1.35.7"\n',
    )
    rows = _common.parse_scenarios_yaml(p, {"title", "expected", "from", "to"})
    assert len(rows) == 2
    assert rows[0] == {
        "pr": "1202",
        "title": "Bump botocore",
        "expected": "port-required",
        "from": "1.35.7",
        "to": "1.35.16",
    }
    assert rows[1]["pr"] == "1192"


def test_parse_scenarios_yaml_block_scalar_skipped(tmp_path: Path) -> None:
    """Block-scalar bodies (rationale|notes) are consumed but not captured."""
    p = tmp_path / "s.yaml"
    p.write_text(
        "scenarios:\n"
        "- pr: 1234\n"
        "  expected: no-port\n"
        "  rationale: |\n"
        "    First line of rationale.\n"
        "    Second line.\n"
        "  notes: null\n"
        "- pr: 5678\n"
        "  expected: port-required\n",
    )
    rows = _common.parse_scenarios_yaml(p, {"expected"})
    assert len(rows) == 2
    assert rows[0] == {"pr": "1234", "expected": "no-port"}
    assert rows[1] == {"pr": "5678", "expected": "port-required"}


def test_parse_scenarios_yaml_ignores_unknown_keys(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(
        "- pr: 1\n"
        '  title: "foo"\n'
        "  expected: no-port\n"
        '  merge_commit: "abc"\n'
        '  aiobotocore_version: "3.4.1"\n',
    )
    rows = _common.parse_scenarios_yaml(p, {"title", "expected"})
    assert rows[0] == {"pr": "1", "title": "foo", "expected": "no-port"}


def test_parse_scenarios_yaml_missing_file(tmp_path: Path) -> None:
    assert _common.parse_scenarios_yaml(tmp_path / "nope.yaml", {"pr"}) == []


def test_parse_scenarios_yaml_skips_comments_and_doc_marker(
    tmp_path: Path,
) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(
        "---\n# This is a comment\nscenarios:\n- pr: 1\n  expected: no-port\n",
    )
    rows = _common.parse_scenarios_yaml(p, {"expected"})
    assert rows == [{"pr": "1", "expected": "no-port"}]


def test_derive_versions_detects_lower_changed() -> None:
    """Lower bound moving is the port-required pyproject signal."""
    before = '"botocore >= 1.35.0, < 1.35.10"\nfoo'
    after = '"botocore >= 1.35.5, < 1.35.20"\nfoo'
    with patch(
        "_common.subprocess.check_output",
        side_effect=[before, after],
    ):
        result = _common.derive_versions("deadbeef")
    assert result == ("1.35.9", "1.35.19", True)


def test_derive_versions_upper_only_is_no_port() -> None:
    before = '"botocore >= 1.35.0, < 1.35.10"\nfoo'
    after = '"botocore >= 1.35.0, < 1.35.20"\nfoo'
    with patch(
        "_common.subprocess.check_output",
        side_effect=[before, after],
    ):
        result = _common.derive_versions("deadbeef")
    assert result == ("1.35.9", "1.35.19", False)


def test_derive_versions_handles_missing_pyproject() -> None:
    import subprocess

    with patch(
        "_common.subprocess.check_output",
        side_effect=subprocess.CalledProcessError(1, "git"),
    ):
        assert _common.derive_versions("deadbeef") is None


def test_aiobotocore_port_happened_detects_override_change() -> None:
    paths = [
        "aiobotocore/__init__.py",
        "aiobotocore/endpoint.py",
        "tests/test_patches.py",
    ]
    with patch(
        "_common.subprocess.check_output",
        return_value=json.dumps(paths),
    ):
        assert _common.aiobotocore_port_happened(1202) is True


def test_aiobotocore_port_happened_ignores_housekeeping_only() -> None:
    paths = [
        "aiobotocore/__init__.py",
        "pyproject.toml",
        "CHANGES.rst",
        "uv.lock",
        "tests/test_patches.py",
    ]
    with patch(
        "_common.subprocess.check_output",
        return_value=json.dumps(paths),
    ):
        assert _common.aiobotocore_port_happened(1428) is False


def test_aiobotocore_port_happened_nested_subdir() -> None:
    """Changes to aiobotocore/retries/*.py count as ports."""
    paths = [
        "aiobotocore/__init__.py",
        "aiobotocore/retries/adaptive.py",
    ]
    with patch(
        "_common.subprocess.check_output",
        return_value=json.dumps(paths),
    ):
        assert _common.aiobotocore_port_happened(9999) is True


def test_aiobotocore_port_happened_handles_fetch_failure() -> None:
    import subprocess

    with patch(
        "_common.subprocess.check_output",
        side_effect=subprocess.CalledProcessError(1, "gh"),
    ):
        assert _common.aiobotocore_port_happened(1) is None


def test_upper_re_matches_project_dep_spec() -> None:
    spec = '"botocore >= 1.40.79, < 1.42.90"'
    assert _common.UPPER_RE.search(spec).group(1) == "1.42.90"


def test_lower_re_matches_project_dep_spec() -> None:
    spec = '"botocore >= 1.40.79, < 1.42.90"'
    assert _common.LOWER_RE.search(spec).group(1) == "1.40.79"


def test_committed_scenarios_yaml_parses() -> None:
    """Smoke test: the real committed file parses cleanly with the real schema."""
    repo_root = Path(__file__).resolve().parents[2]
    scenarios = repo_root / "plugins/aiobotocore-bot/evals/scenarios.yaml"
    rows = _common.parse_scenarios_yaml(
        scenarios,
        {"title", "expected", "from", "to"},
    )
    assert len(rows) >= 32
    assert all("pr" in r and "expected" in r for r in rows)
    assert all(r["expected"] in {"no-port", "port-required"} for r in rows)


def test_invoke_and_parse_verdict_regex_shape() -> None:
    """The regex contract: group(1) captures the verdict token."""
    verdict_re = re.compile(r"^CLASSIFICATION:\s*(\S+)", re.MULTILINE)
    raw = "Some preamble\nCLASSIFICATION: no-port\nMore text"
    m = verdict_re.search(raw)
    assert m is not None
    assert m.group(1) == "no-port"
