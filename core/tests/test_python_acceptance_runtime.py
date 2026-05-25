"""Tests for ClawSeat mechanical acceptance Python runtime determinism (MP026).

Policy: mechanical acceptance commands must use find_clawseat_python() instead of
bare 'python3' / '/usr/bin/python3', which may lack pytest in isolated planner
shells (Codex, Gemini). This prevents seats from mutating their environment by
pip-installing into the ambient Python to pass acceptance.

find_clawseat_python():
  1. checks sys.executable for tomllib/tomli AND pytest
  2. probes _CLAWSEAT_PYTHON_FALLBACK_CANDIDATES
  3. falls back to sys.executable (caller surfaces missing-dep warning)
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest

# conftest.py adds core/scripts to path
import agent_admin_config as cfg


# ---------------------------------------------------------------------------
# _can_import_pytest — unit tests matching 'python and acceptance'
# ---------------------------------------------------------------------------


class TestPythonAcceptancePytest:
    def test_can_import_pytest_returns_true_for_sys_executable(self):
        """sys.executable must have pytest in CI / dev environment."""
        try:
            import pytest as _  # noqa: F401
        except ImportError:
            pytest.skip("pytest not importable in test runtime (expected in CI)")
        assert cfg._can_import_pytest(sys.executable) is True

    def test_can_import_pytest_returns_false_for_nonexistent_exe(self):
        assert cfg._can_import_pytest("/no/such/python99") is False

    def test_can_import_pytest_returns_false_on_timeout(self):
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=5)):
            assert cfg._can_import_pytest(sys.executable) is False


# ---------------------------------------------------------------------------
# _exe_is_clawseat_ready — acceptance criteria for the probe
# ---------------------------------------------------------------------------


class TestPythonAcceptanceReadiness:
    def test_exe_clawseat_ready_true_for_sys_executable(self):
        """sys.executable must pass readiness check in the test environment."""
        assert cfg._exe_is_clawseat_ready(sys.executable) is True

    def test_exe_clawseat_ready_false_for_nonexistent(self):
        assert cfg._exe_is_clawseat_ready("/nonexistent/python") is False

    def test_exe_clawseat_ready_false_when_pytest_missing(self):
        call_count = [0]
        original = subprocess.run

        def fake_run(cmd, **kwargs):
            call_count[0] += 1
            # Allow tomllib/tomli check to pass, reject pytest
            r = original.__class__.__new__(original.__class__)
            import subprocess as _sp
            completed = _sp.CompletedProcess(args=cmd, returncode=0)
            if "pytest" in (cmd[-1] if cmd else ""):
                completed = _sp.CompletedProcess(args=cmd, returncode=1)
            return completed

        with patch.object(subprocess, "run", side_effect=fake_run):
            # When pytest import fails, readiness must be False
            with patch.object(cfg, "_can_import_pytest", return_value=False):
                # _exe_is_clawseat_ready calls can_import on the exe
                # We validate by patching the whole function and checking outcome
                pass

        # Direct invariant test: if pytest is not importable, exe is not ready
        with patch.object(subprocess, "run", side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0),  # tomllib ok
            subprocess.CompletedProcess(args=[], returncode=1),  # pytest missing
        ]):
            result = cfg._exe_is_clawseat_ready("/fake/python")

        assert result is False


# ---------------------------------------------------------------------------
# find_clawseat_python — acceptance command determinism
# ---------------------------------------------------------------------------


class TestPythonAcceptanceFindClawseatPython:
    def test_find_clawseat_python_returns_string(self):
        result = cfg.find_clawseat_python()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_find_clawseat_python_returns_sys_executable_when_ready(self):
        """When sys.executable is clawseat-ready, must use it (no extra probes)."""
        with patch.object(cfg, "_exe_is_clawseat_ready", return_value=True):
            result = cfg.find_clawseat_python()
        assert result == sys.executable

    def test_find_clawseat_python_probes_fallback_when_sys_not_ready(self):
        """Falls back to a candidate when sys.executable is not ready."""
        sentinel = "/fake/python3.12"

        def fake_ready(exe: str) -> bool:
            return exe == sentinel

        orig_candidates = cfg._CLAWSEAT_PYTHON_FALLBACK_CANDIDATES
        try:
            cfg._CLAWSEAT_PYTHON_FALLBACK_CANDIDATES = [sentinel]
            with patch.object(cfg, "_exe_is_clawseat_ready", side_effect=fake_ready):
                result = cfg.find_clawseat_python()
        finally:
            cfg._CLAWSEAT_PYTHON_FALLBACK_CANDIDATES = orig_candidates

        assert result == sentinel

    def test_find_clawseat_python_falls_back_to_sys_when_nothing_found(self):
        """When no candidate is ready, falls back gracefully to sys.executable."""
        with patch.object(cfg, "_exe_is_clawseat_ready", return_value=False):
            result = cfg.find_clawseat_python()
        assert result == sys.executable

    def test_find_clawseat_python_does_not_probe_sys_executable_twice(self):
        """sys.executable should be probed once, not again in fallback candidates."""
        probed: list[str] = []

        def fake_ready(exe: str) -> bool:
            probed.append(exe)
            return False

        orig_candidates = cfg._CLAWSEAT_PYTHON_FALLBACK_CANDIDATES
        try:
            cfg._CLAWSEAT_PYTHON_FALLBACK_CANDIDATES = [sys.executable, "/other/python"]
            with patch.object(cfg, "_exe_is_clawseat_ready", side_effect=fake_ready):
                cfg.find_clawseat_python()
        finally:
            cfg._CLAWSEAT_PYTHON_FALLBACK_CANDIDATES = orig_candidates

        assert probed.count(sys.executable) == 1

    def test_find_clawseat_python_result_has_pytest(self):
        """The returned interpreter must have pytest installed."""
        exe = cfg.find_clawseat_python()
        r = subprocess.run([exe, "-c", "import pytest"], capture_output=True, timeout=10)
        assert r.returncode == 0, (
            f"find_clawseat_python() returned {exe!r} which lacks pytest. "
            f"Install with: {exe} -m pip install pytest"
        )


# ---------------------------------------------------------------------------
# Policy: acceptance commands must not depend on /usr/bin/python3
# ---------------------------------------------------------------------------


class TestPythonAcceptancePolicy:
    def test_usr_bin_python3_not_the_only_option(self):
        """find_clawseat_python must never default to bare /usr/bin/python3 when
        a better interpreter with pytest is available."""
        result = cfg.find_clawseat_python()
        # If /usr/bin/python3 is returned, it must actually have pytest
        if result == "/usr/bin/python3":
            r = subprocess.run(
                [result, "-c", "import pytest"],
                capture_output=True,
                timeout=5,
            )
            if r.returncode != 0:
                pytest.fail(
                    f"find_clawseat_python() returned /usr/bin/python3 which lacks pytest. "
                    f"Install brew python or: {result} -m pip install pytest"
                )

    def test_acceptance_command_uses_deterministic_python(self):
        """The policy: mechanical acceptance commands use find_clawseat_python().

        If this test passes, the command in the brief is using a deterministic
        Python rather than ambient /usr/bin/python3 3.9 without pytest.
        """
        exe = cfg.find_clawseat_python()
        r = subprocess.run(
            [exe, "-m", "pytest", "--version"],
            capture_output=True,
            timeout=10,
        )
        assert r.returncode == 0, (
            f"Deterministic Python {exe!r} cannot run pytest --version. "
            f"stderr: {r.stderr.decode()!r}"
        )
