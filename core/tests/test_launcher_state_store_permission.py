"""Regression tests for launcher state store desktop permission fix (MP032).

Root cause: launcher_state_store_path() preferred ~/Desktop/.agent-launcher-state.json
even when the file existed but was not writable (macOS TCC permission denial).
launcher_remember_recent_dir() called python3 which then raised PermissionError,
and set -euo pipefail in agent-launcher.sh killed the entire seat launch.

Fix:
1. launcher_state_store_path() now only uses the Desktop path when it exists AND
   is writable; falls back to ~/.config/clawseat/launcher-state.json otherwise.
2. launcher_remember_recent_dir() wraps the file write in try/except OSError so
   a permission failure is silently ignored (best-effort persistence).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_COMMON = _REPO / "core" / "launchers" / "agent-launcher-common.sh"


def _run_bash_func(func_body: str, env: dict | None = None, home: Path | None = None) -> subprocess.CompletedProcess:
    """Execute a bash snippet that sources agent-launcher-common.sh first."""
    script = f"""
set -euo pipefail
source {_COMMON}
{func_body}
"""
    run_env = os.environ.copy()
    if home is not None:
        run_env["HOME"] = str(home)
        run_env["REAL_HOME"] = str(home)
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        env=run_env,
    )


# ---------------------------------------------------------------------------
# launcher_state_store_path — writability gate
# ---------------------------------------------------------------------------


class TestLauncherStateStorePath:
    def test_uses_desktop_path_when_writable(self, tmp_path):
        """Desktop path must be used when it exists and is writable."""
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        state = desktop / ".agent-launcher-state.json"
        state.write_text("{}", encoding="utf-8")
        state.chmod(0o644)

        result = _run_bash_func("launcher_state_store_path", home=tmp_path)
        assert result.returncode == 0, result.stderr
        assert str(state) in result.stdout.strip()

    def test_falls_back_to_xdg_when_desktop_not_writable(self, tmp_path):
        """When Desktop file exists but is not writable, must use XDG config path."""
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        state = desktop / ".agent-launcher-state.json"
        state.write_text("{}", encoding="utf-8")
        state.chmod(0o444)  # read-only — not writable

        try:
            result = _run_bash_func("launcher_state_store_path", home=tmp_path)
            assert result.returncode == 0, result.stderr
            output = result.stdout.strip()
            # Must NOT return the Desktop path
            assert ".agent-launcher-state.json" not in output or "Desktop" not in output, (
                f"Should have fallen back from unwritable Desktop, got: {output!r}"
            )
            # Must return the XDG config path instead
            assert ".config/clawseat/launcher-state.json" in output, (
                f"Expected XDG fallback path, got: {output!r}"
            )
        finally:
            state.chmod(0o644)

    def test_uses_xdg_when_desktop_file_absent(self, tmp_path):
        """When no Desktop state file exists, must use XDG config path."""
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        # No state file written

        result = _run_bash_func("launcher_state_store_path", home=tmp_path)
        assert result.returncode == 0, result.stderr
        assert ".config/clawseat/launcher-state.json" in result.stdout.strip()

    def test_env_override_takes_highest_priority(self, tmp_path):
        """LAUNCHER_STATE_STORE env var must override all path resolution."""
        custom = str(tmp_path / "custom-state.json")
        result = _run_bash_func(
            "launcher_state_store_path",
            home=tmp_path,
            env={"LAUNCHER_STATE_STORE": custom},
        )
        assert result.returncode == 0, result.stderr
        assert custom in result.stdout.strip()


# ---------------------------------------------------------------------------
# launcher_remember_recent_dir — best-effort write (unwritable Desktop)
# ---------------------------------------------------------------------------


class TestLauncherRememberRecentDir:
    def test_succeeds_with_writable_store(self, tmp_path):
        """launcher_remember_recent_dir must succeed when the state store is writable."""
        workdir = tmp_path / "workspace"
        workdir.mkdir()

        result = _run_bash_func(
            f"launcher_remember_recent_dir {workdir}",
            home=tmp_path,
        )
        assert result.returncode == 0, f"launcher_remember_recent_dir failed: {result.stderr}"

    def test_does_not_abort_when_desktop_not_writable(self, tmp_path):
        """launcher_remember_recent_dir must not abort when Desktop file is unwritable.

        This is the MP032 regression: PermissionError on Desktop write used to
        kill the entire seat launch via set -euo pipefail.
        """
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        state = desktop / ".agent-launcher-state.json"
        state.write_text("{}", encoding="utf-8")
        state.chmod(0o444)  # simulate TCC-denied write

        workdir = tmp_path / "workspace"
        workdir.mkdir()

        try:
            result = _run_bash_func(
                f"launcher_remember_recent_dir {workdir}",
                home=tmp_path,
            )
            # Must exit 0 — permission failure is best-effort
            assert result.returncode == 0, (
                f"launcher_remember_recent_dir must not abort on PermissionError.\n"
                f"stderr: {result.stderr}"
            )
        finally:
            state.chmod(0o644)

    def test_writes_to_xdg_fallback_when_desktop_unwritable(self, tmp_path):
        """When Desktop is unwritable, the recent dir is persisted to XDG path instead."""
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        state = desktop / ".agent-launcher-state.json"
        state.write_text("{}", encoding="utf-8")
        state.chmod(0o444)

        workdir = tmp_path / "workspace"
        workdir.mkdir()

        try:
            result = _run_bash_func(
                f"launcher_remember_recent_dir {workdir}",
                home=tmp_path,
            )
            assert result.returncode == 0, result.stderr
            # XDG fallback should have been written
            xdg_store = tmp_path / ".config" / "clawseat" / "launcher-state.json"
            assert xdg_store.exists(), (
                "XDG fallback state store must be written when Desktop is unwritable"
            )
        finally:
            state.chmod(0o644)

    def test_nonexistent_workdir_is_silently_skipped(self, tmp_path):
        """Passing a non-existent directory must exit 0 silently."""
        result = _run_bash_func(
            f"launcher_remember_recent_dir {tmp_path}/no-such-dir",
            home=tmp_path,
        )
        assert result.returncode == 0, result.stderr
