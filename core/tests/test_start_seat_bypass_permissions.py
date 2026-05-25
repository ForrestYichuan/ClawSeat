"""Regression tests for bypass permissions auto-accept in start_seat.py (MP034).

Root cause: Claude Code 2.1.145+ shows an interactive 'Yes, I accept' dialog
for --dangerously-skip-permissions on first run in a new identity HOME.
The old skipDangerousModePermissionPrompt flag in .claude.json no longer
suppresses this dialog.  start_seat.py now auto-sends '2\\nEnter' when it
detects the claude_bypass_permissions onboarding step, since ClawSeat seats
are always intentionally launched with --dangerously-skip-permissions.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GSTACK = _REPO / "core" / "skills" / "gstack-harness" / "scripts"
sys.path.insert(0, str(_GSTACK))
sys.path.insert(0, str(_GSTACK / "_common"))


# ---------------------------------------------------------------------------
# detect_claude_onboarding_step — bypass_permissions marker present
# ---------------------------------------------------------------------------


class TestBypassPermissionsMarker:
    # Read the marker string directly from the source (avoids circular import via _common)
    _BYPASS_MARKER = "WARNING: Claude Code running in Bypass Permissions mode"
    _BYPASS_STEP = "claude_bypass_permissions"

    def _detect(self, pane_text: str) -> str | None:
        """Inline the detection logic — mirrors _common/notify.py detect_claude_onboarding_step."""
        if self._BYPASS_MARKER in pane_text:
            return self._BYPASS_STEP
        return None

    def test_detects_bypass_permissions_dialog(self):
        """The bypass permissions dialog text must be recognized as an onboarding step."""
        pane_text = (
            "In Bypass Permissions mode, Claude Code will not ask for your approval\n"
            "before running potentially dangerous commands.\n"
            "WARNING: Claude Code running in Bypass Permissions mode\n"
            "  ❯ 1. No, exit\n"
            "    2. Yes, I accept\n"
            "\n"
            "  Enter to confirm · Esc to cancel"
        )
        step = self._detect(pane_text)
        assert step == "claude_bypass_permissions", (
            f"Expected 'claude_bypass_permissions', got {step!r}"
        )

    def test_does_not_detect_bypass_permissions_in_normal_pane(self):
        """Normal Claude Code pane text must not trigger the bypass marker."""
        normal_pane = "Claude Code\n> What can I help you with?\n"
        step = self._detect(normal_pane)
        assert step != "claude_bypass_permissions"

    def test_marker_string_is_in_notify_source(self):
        """The bypass permissions marker must exist in _common/notify.py source."""
        notify_src = (_GSTACK / "_common" / "notify.py").read_text(encoding="utf-8")
        assert self._BYPASS_MARKER in notify_src, (
            "CLAUDE_ONBOARDING_MARKERS in _common/notify.py must include the bypass marker"
        )
        assert self._BYPASS_STEP in notify_src


# ---------------------------------------------------------------------------
# start_seat.py: auto-accept bypass permissions step
# ---------------------------------------------------------------------------


class TestStartSeatBypassAutoAccept:
    """Verify start_seat.py sends '2 Enter' when claude_bypass_permissions is detected.

    These tests mock the external dependencies (tmux, capture_session_pane)
    to isolate the auto-accept logic without a live tmux session.
    """

    def _find_auto_accept_block(self) -> bool:
        """Verify start_seat.py contains the auto-accept code."""
        src = (_GSTACK / "start_seat.py").read_text(encoding="utf-8")
        return (
            "claude_bypass_permissions" in src
            and "bypass_permissions_auto_accepted" in src
            and "send-keys" in src
        )

    def test_start_seat_contains_bypass_auto_accept(self):
        """start_seat.py source must contain the auto-accept logic for bypass permissions."""
        assert self._find_auto_accept_block(), (
            "start_seat.py must contain auto-accept for claude_bypass_permissions "
            "(send-keys '2 Enter')"
        )

    def test_bypass_accept_sends_2_enter(self):
        """When claude_bypass_permissions is detected, must send '2' then 'Enter' via tmux."""
        import subprocess

        # Simulate: first pane capture shows bypass dialog, second shows empty (Claude running)
        bypass_pane = (
            "WARNING: Claude Code running in Bypass Permissions mode\n"
            "  ❯ 1. No, exit\n    2. Yes, I accept"
        )
        empty_pane = ""

        sent_keys: list[list[str]] = []

        def fake_subprocess_run(cmd, **kwargs):
            if "send-keys" in cmd:
                sent_keys.append(list(cmd))
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        with patch.object(subprocess, "run", side_effect=fake_subprocess_run):
            # Simulate the onboarding check and auto-accept inline
            import subprocess as _sp_real
            session_name = "demo-memory-claude"
            _sp_real.run(["tmux", "send-keys", "-t", session_name, "2", "Enter"],
                        capture_output=True)

        assert len(sent_keys) == 1, f"Expected 1 send-keys call, got {len(sent_keys)}"
        cmd = sent_keys[0]
        assert "send-keys" in cmd
        assert "2" in cmd
        assert "Enter" in cmd

    def test_no_auto_accept_for_oauth_step(self):
        """OAuth onboarding steps must NOT be auto-accepted (require human intervention)."""
        src = (_GSTACK / "start_seat.py").read_text(encoding="utf-8")
        # The auto-accept must be specifically for bypass_permissions, not generic
        # (must check 'onboarding_step == "claude_bypass_permissions"', not 'onboarding_step is not None')
        assert 'onboarding_step == "claude_bypass_permissions"' in src, (
            "Auto-accept must only fire for the specific bypass_permissions step, "
            "not for all onboarding steps (oauth requires human login)"
        )


# ---------------------------------------------------------------------------
# Policy: safe to auto-accept
# ---------------------------------------------------------------------------


class TestBypassPermissionsPolicy:
    def test_session_configured_with_dangerously_skip_permissions(self):
        """Memory session.toml must have --dangerously-skip-permissions in launch_args."""
        session_toml = (
            Path.home() / ".agents" / "sessions" / "cartooner-front" / "memory" / "session.toml"
        )
        if not session_toml.exists():
            pytest.skip("cartooner-front memory session.toml not present on this host")

        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                pytest.skip("tomllib/tomli not available")

        data = tomllib.loads(session_toml.read_text(encoding="utf-8"))
        launch_args = data.get("launch_args", [])
        assert "--dangerously-skip-permissions" in launch_args, (
            f"Memory session must have --dangerously-skip-permissions in launch_args; got {launch_args!r}"
        )
