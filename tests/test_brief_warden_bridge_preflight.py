"""Tests for warden bridge contract + runtime preflight (MP016).

Warden bridge: operator-overlay test channel — memory writes a timestamped
BLOCKED entry to a discoverable inbox path when it cannot chat normally.
Warden is the human operator, NOT a second memory seat.

Preflight: check_script_deps() (agent_admin_config) detects missing PyYAML /
tomli before the seat claims readiness.  cmd_planner_status emits
[PREFLIGHT_WARN] to stderr when deps are absent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import agent_admin_config as cfg  # noqa: E402
import agent_admin_brief as brief_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Warden inbox path contract
# ---------------------------------------------------------------------------


class TestWardenInboxPath:
    def test_inbox_path_under_agents_tasks(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            path = brief_mod.warden_inbox_path("cartooner-front")
        assert ".agents" in str(path)
        assert "tasks" in str(path)

    def test_inbox_path_contains_project_name(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            path = brief_mod.warden_inbox_path("my-project")
        assert "my-project" in str(path)

    def test_inbox_path_uses_canonical_filename(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            path = brief_mod.warden_inbox_path("p")
        assert path.name == brief_mod.WARDEN_INBOX_FILENAME

    def test_warden_inbox_filename_constant(self):
        assert brief_mod.WARDEN_INBOX_FILENAME == "warden-inbox.md"


# ---------------------------------------------------------------------------
# write_warden_blocked — durable blocked report
# ---------------------------------------------------------------------------


class TestWriteWardenBlocked:
    def test_creates_inbox_file(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            brief_mod.write_warden_blocked("proj", "task-001", "cannot proceed")
            path = brief_mod.warden_inbox_path("proj")
        assert path.exists()

    def test_content_contains_blocked_marker(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            brief_mod.write_warden_blocked("proj", "T1", "msg")
            path = brief_mod.warden_inbox_path("proj")
        assert "BLOCKED" in path.read_text()

    def test_content_contains_task_id(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            brief_mod.write_warden_blocked("proj", "task-XYZ", "blocker")
            path = brief_mod.warden_inbox_path("proj")
        assert "task-XYZ" in path.read_text()

    def test_content_contains_message(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            brief_mod.write_warden_blocked("proj", "T1", "my specific blocker message")
            path = brief_mod.warden_inbox_path("proj")
        assert "my specific blocker message" in path.read_text()

    def test_second_write_appends_not_overwrites(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            brief_mod.write_warden_blocked("proj", "T1", "first block")
            brief_mod.write_warden_blocked("proj", "T2", "second block")
            path = brief_mod.warden_inbox_path("proj")
        content = path.read_text()
        assert "first block" in content
        assert "second block" in content
        assert content.count("BLOCKED") >= 2

    def test_creates_missing_parent_directories(self, tmp_path):
        nested = tmp_path / "no" / "parent"
        with patch.object(brief_mod, "real_user_home", return_value=nested):
            brief_mod.write_warden_blocked("p", "t", "msg")
            path = brief_mod.warden_inbox_path("p")
        assert path.exists()

    def test_entry_contains_iso_timestamp(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            brief_mod.write_warden_blocked("proj", "T1", "msg")
            path = brief_mod.warden_inbox_path("proj")
        content = path.read_text()
        # ISO datetime contains 'T' and '+' or 'Z'
        assert "T" in content and ("+00:00" in content or "Z" in content)


# ---------------------------------------------------------------------------
# check_script_deps — preflight dependency detection
# ---------------------------------------------------------------------------


class TestCheckScriptDeps:
    def test_returns_list(self):
        result = cfg.check_script_deps()
        assert isinstance(result, list)

    def test_all_deps_present_returns_empty(self):
        # On the CI / test runner, PyYAML and tomllib/tomli must be installed
        result = cfg.check_script_deps()
        assert result == [], f"unexpected missing deps: {result}"

    def test_detects_missing_yaml(self):
        orig = sys.modules.get("yaml")
        sys.modules["yaml"] = None  # type: ignore[assignment]  # sentinel → ImportError
        try:
            result = cfg.check_script_deps()
        finally:
            if orig is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = orig
        assert "PyYAML" in result

    def test_detects_missing_tomli_when_neither_tomllib_nor_tomli(self):
        orig_tomllib = sys.modules.get("tomllib")
        orig_tomli = sys.modules.get("tomli")
        sys.modules["tomllib"] = None  # type: ignore[assignment]
        sys.modules["tomli"] = None  # type: ignore[assignment]
        try:
            result = cfg.check_script_deps()
        finally:
            for name, orig in [("tomllib", orig_tomllib), ("tomli", orig_tomli)]:
                if orig is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = orig  # type: ignore[assignment]
        assert "tomli" in result

    def test_tomllib_present_satisfies_toml_requirement(self):
        """stdlib tomllib (3.11+) should prevent tomli from being reported missing."""
        try:
            import tomllib  # noqa: F401
        except ImportError:
            pytest.skip("tomllib not available (Python < 3.11)")
        result = cfg.check_script_deps()
        assert "tomli" not in result

    def test_yaml_present_not_in_missing_list(self):
        result = cfg.check_script_deps()
        assert "PyYAML" not in result


# ---------------------------------------------------------------------------
# cmd_planner_status — preflight warning integration
# ---------------------------------------------------------------------------


class TestPlannerStatusPreflight:
    def test_preflight_warn_emitted_to_stderr_when_deps_missing(self, capsys):
        args = argparse.Namespace(project="nonexistent-proj-xyz", json=False)
        with patch.object(cfg, "check_script_deps", return_value=["PyYAML"]):
            brief_mod.cmd_planner_status(args)
        captured = capsys.readouterr()
        assert "[PREFLIGHT_WARN]" in captured.err

    def test_preflight_warn_names_missing_package(self, capsys):
        args = argparse.Namespace(project="nonexistent-proj-xyz", json=False)
        with patch.object(cfg, "check_script_deps", return_value=["tomli"]):
            brief_mod.cmd_planner_status(args)
        captured = capsys.readouterr()
        assert "tomli" in captured.err

    def test_no_preflight_warn_when_deps_ok(self, capsys):
        args = argparse.Namespace(project="nonexistent-proj-xyz", json=False)
        with patch.object(cfg, "check_script_deps", return_value=[]):
            brief_mod.cmd_planner_status(args)
        captured = capsys.readouterr()
        assert "[PREFLIGHT_WARN]" not in captured.err

    def test_preflight_warn_includes_pip_install_hint(self, capsys):
        args = argparse.Namespace(project="nonexistent-proj-xyz", json=False)
        with patch.object(cfg, "check_script_deps", return_value=["PyYAML"]):
            brief_mod.cmd_planner_status(args)
        captured = capsys.readouterr()
        assert "pip3 install" in captured.err
