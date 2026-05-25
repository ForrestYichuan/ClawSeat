"""Tests for queue-drained relay Python runtime hardening (MP021).

Root cause: _do_relay_complete_handoff used sys.executable unconditionally.
System Python 3.9 on macOS lacks tomllib (stdlib 3.11+ only) and may lack
tomli (third-party backport).  complete_handoff.py imports _common/profile.py
which does a hard import tomllib/tomli, crashing the relay subprocess.

Fix: _find_relay_python() probes sys.executable first, then falls back to
known Homebrew Python paths that have tomllib available.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# conftest.py adds core/scripts to path; import here after that bootstrap
import agent_admin_brief as brief_mod


# ---------------------------------------------------------------------------
# _can_import_toml_exe
# ---------------------------------------------------------------------------


class TestCanImportTomlExe:
    def test_returns_true_when_tomllib_available(self):
        """sys.executable on Python >= 3.11 must pass tomllib check."""
        if sys.version_info < (3, 11):
            pytest.skip("tomllib is stdlib only in Python 3.11+")
        assert brief_mod._can_import_toml_exe(sys.executable) is True

    def test_returns_true_when_tomli_available(self):
        """When tomllib is absent but tomli is installed, must return True."""
        try:
            import tomli  # noqa: F401
        except ImportError:
            pytest.skip("tomli not installed in this environment")
        # Simulate tomllib absent, tomli present
        orig = sys.modules.get("tomllib")
        sys.modules["tomllib"] = None  # type: ignore[assignment]
        try:
            assert brief_mod._can_import_toml_exe(sys.executable) is True
        finally:
            if orig is None:
                sys.modules.pop("tomllib", None)
            else:
                sys.modules["tomllib"] = orig

    def test_returns_false_for_nonexistent_exe(self):
        assert brief_mod._can_import_toml_exe("/nonexistent/python999") is False

    def test_returns_false_when_subprocess_times_out(self):
        fake = MagicMock(side_effect=subprocess.TimeoutExpired(cmd=[], timeout=5))
        with patch.object(subprocess, "run", fake):
            result = brief_mod._can_import_toml_exe(sys.executable)
        assert result is False


# ---------------------------------------------------------------------------
# _find_relay_python
# ---------------------------------------------------------------------------


class TestFindRelayPython:
    def test_returns_sys_executable_when_toml_available(self):
        """If sys.executable can import toml, use it without probing fallbacks."""
        with patch.object(brief_mod, "_can_import_toml_exe", return_value=True):
            result = brief_mod._find_relay_python()
        assert result == sys.executable

    def test_probes_fallback_when_sys_executable_fails(self):
        """When sys.executable can't import toml, probe fallback candidates."""
        sentinel = "/fake/python3.12"

        def fake_can(exe: str) -> bool:
            return exe == sentinel

        orig_candidates = brief_mod._RELAY_PYTHON_FALLBACK_CANDIDATES
        try:
            brief_mod._RELAY_PYTHON_FALLBACK_CANDIDATES = [sentinel]
            with patch.object(brief_mod, "_can_import_toml_exe", side_effect=fake_can):
                result = brief_mod._find_relay_python()
        finally:
            brief_mod._RELAY_PYTHON_FALLBACK_CANDIDATES = orig_candidates

        assert result == sentinel

    def test_falls_back_to_sys_executable_when_nothing_found(self):
        """If no suitable Python found, return sys.executable rather than raising."""
        with patch.object(brief_mod, "_can_import_toml_exe", return_value=False):
            result = brief_mod._find_relay_python()
        assert result == sys.executable

    def test_skips_candidate_equal_to_sys_executable(self):
        """Do not probe sys.executable twice (already checked as first step)."""
        probed: list[str] = []

        def fake_can(exe: str) -> bool:
            probed.append(exe)
            return False

        orig_candidates = brief_mod._RELAY_PYTHON_FALLBACK_CANDIDATES
        try:
            brief_mod._RELAY_PYTHON_FALLBACK_CANDIDATES = [sys.executable, "/other/python"]
            with patch.object(brief_mod, "_can_import_toml_exe", side_effect=fake_can):
                brief_mod._find_relay_python()
        finally:
            brief_mod._RELAY_PYTHON_FALLBACK_CANDIDATES = orig_candidates

        # sys.executable should appear exactly once (first check), not again in fallbacks
        assert probed.count(sys.executable) == 1


# ---------------------------------------------------------------------------
# _do_relay_complete_handoff uses _find_relay_python
# ---------------------------------------------------------------------------


class TestRelayUsesFoundPython:
    def _fake_process(self) -> subprocess.CompletedProcess:
        r: subprocess.CompletedProcess = subprocess.CompletedProcess(args=[], returncode=0)
        r.stdout = "completed ok"
        r.stderr = ""
        return r

    def test_relay_subprocess_first_arg_is_find_relay_python_result(self, tmp_path, monkeypatch):
        """_do_relay_complete_handoff must use _find_relay_python() as interpreter."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        fake_python = "/opt/homebrew/opt/python@3.12/bin/python3.12"
        captured: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            return self._fake_process()

        with patch.object(brief_mod, "_find_relay_python", return_value=fake_python):
            monkeypatch.setattr(subprocess, "run", fake_run)
            brief_mod._do_relay_complete_handoff("p", "t", "T1", "p-planner")

        assert len(captured) == 1
        assert captured[0][0] == fake_python

    def test_relay_still_passes_no_notify(self, tmp_path, monkeypatch):
        """MP017 --no-notify contract must be preserved after MP021 changes."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        captured: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            return self._fake_process()

        with patch.object(brief_mod, "_find_relay_python", return_value=sys.executable):
            monkeypatch.setattr(subprocess, "run", fake_run)
            brief_mod._do_relay_complete_handoff("p", "t", "T1", "p-planner")

        assert "--no-notify" in captured[0]

    def test_relay_still_passes_user_summary(self, tmp_path, monkeypatch):
        """MP017 --user-summary contract must be preserved after MP021 changes."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        captured: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            return self._fake_process()

        with patch.object(brief_mod, "_find_relay_python", return_value=sys.executable):
            monkeypatch.setattr(subprocess, "run", fake_run)
            brief_mod._do_relay_complete_handoff("p", "t", "T1", "p-planner")

        assert "--user-summary" in captured[0]
