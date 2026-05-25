"""Tests for core/lib/seat_resolver.py — 3 kinds × 2 cases + edge cases."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from core.lib import seat_resolver as seat_resolver_mod
from core.lib.seat_resolver import (
    SeatResolution,
    SeatResolutionError,
    resolve_seat,
    resolve_seat_from_profile,
)


_SANDBOX_HOME_LOOKING = (
    "/tmp/fake/.agents/runtime/identities/claude/oauth/main/home"
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_openclaw_home(tmp_path):
    """Temp OpenClaw home dir for workspace isolation."""
    oc_root = tmp_path / ".openclaw"
    oc_root.mkdir()
    yield oc_root


@pytest.fixture
def tmp_agents_root(tmp_path, monkeypatch):
    """Temp HOME / .agents dir for session.toml isolation.

    seat_resolver._home() now delegates to real_user_home(), which ignores
    plain HOME monkeypatching — set CLAWSEAT_REAL_HOME so the helper's
    explicit-override branch redirects to tmp_path.
    """
    agents = tmp_path / ".agents"
    agents.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    yield agents


@pytest.fixture
def profile_like(tmp_path):
    """Minimal profile-like object."""
    class P:
        seats = ["memory", "koder", "planner", "builder-1", "reviewer-1"]
        project_name = "hardening-b"
        handoff_dir = tmp_path / "handoffs"
        profile_path = tmp_path / "profile.toml"
    p = P()
    p.handoff_dir.mkdir()
    return p


# ── Kind: tmux ────────────────────────────────────────────────────────────────

class TestSeatResolverTmux:
    def test_tmux_seat_in_profile_with_session(self, tmp_agents_root, tmp_openclaw_home):
        """target is a declared seat with session.toml → kind=tmux."""
        session_dir = tmp_agents_root / "sessions" / "hardening-b" / "planner"
        session_dir.mkdir(parents=True)
        (session_dir / "session.toml").write_text('session = "hardening-b-planner-claude"\n')

        result = resolve_seat(
            target="planner",
            profile_seats=["memory", "koder", "planner", "builder-1"],
            profile_project_name="hardening-b",
            profile_handoff_dir=tmp_agents_root / "handoffs",
            _openclaw_home=tmp_openclaw_home,
        )
        assert result.kind == "tmux"
        assert result.transport == "tmux-send-keys"
        assert result.session_name == "hardening-b-planner-claude"
        assert result.target == "planner"

    def test_tmux_session_toml_fallback_when_toml_modules_are_unavailable(
        self,
        tmp_agents_root,
        tmp_openclaw_home,
        monkeypatch,
    ):
        """session.toml resolution must work in Python runtimes without tomllib/tomli."""
        session_dir = tmp_agents_root / "sessions" / "hardening-b" / "planner"
        session_dir.mkdir(parents=True)
        (session_dir / "session.toml").write_text('session = "hardening-b-planner-claude"\n')
        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", None)

        result = resolve_seat(
            target="planner",
            profile_seats=["planner"],
            profile_project_name="hardening-b",
            profile_handoff_dir=tmp_agents_root / "handoffs",
            _openclaw_home=tmp_openclaw_home,
        )

        assert result.kind == "tmux"
        assert result.session_name == "hardening-b-planner-claude"

    def test_tmux_seat_no_session_toml(self, tmp_agents_root, tmp_openclaw_home):
        """target in seats but no session.toml → session_name None (still tmux kind)."""
        result = resolve_seat(
            target="builder-1",
            profile_seats=["builder-1"],
            profile_project_name="hardening-b",
            profile_handoff_dir=tmp_agents_root / "handoffs",
            _openclaw_home=tmp_openclaw_home,
        )
        assert result.kind == "tmux"
        assert result.transport == "tmux-send-keys"
        assert result.session_name is None


# ── Kind: openclaw ───────────────────────────────────────────────────────────

class TestSeatResolverOpenClaw:
    def test_openclaw_with_feishu_group_id(self, tmp_openclaw_home, tmp_agents_root):
        """Workspace contract exists with feishu_group_id → kind=openclaw."""
        contract = tmp_openclaw_home / "workspace-mor" / "WORKSPACE_CONTRACT.toml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            'feishu_group_id = "oc_00aaf2adefbe62ba1bd560bd375a33f5"\n'
            'seat_id = "koder"\n'
            'project = "hardening-b"\n'
        )

        result = resolve_seat(
            target="mor",
            profile_seats=["memory", "planner"],
            profile_project_name="hardening-b",
            profile_handoff_dir=tmp_agents_root / "handoffs",
            _openclaw_home=tmp_openclaw_home,
        )
        assert result.kind == "openclaw"
        assert result.transport == "feishu-oc-v1"
        assert result.group_id == "oc_00aaf2adefbe62ba1bd560bd375a33f5"
        assert result.agent_name == "mor"
        assert result.session_name is None

    def test_openclaw_agent_not_in_profile_seats(self, tmp_openclaw_home, tmp_agents_root):
        """Target is not in profile seats but has OpenClaw contract → still openclaw."""
        contract = tmp_openclaw_home / "workspace-mor" / "WORKSPACE_CONTRACT.toml"
        contract.parent.mkdir(parents=True)
        contract.write_text('feishu_group_id = "oc_test"\n')

        result = resolve_seat(
            target="mor",
            profile_seats=["planner", "builder-1"],
            profile_project_name="hardening-b",
            profile_handoff_dir=tmp_agents_root / "handoffs",
            _openclaw_home=tmp_openclaw_home,
        )
        assert result.kind == "openclaw"
        assert result.group_id == "oc_test"

    def test_openclaw_frontstage_beats_runtime_seat_membership(self, tmp_openclaw_home, tmp_agents_root):
        """Explicit heartbeat_transport=openclaw must override legacy runtime seat lists."""
        contract = tmp_openclaw_home / "workspace-cartooner" / "WORKSPACE_CONTRACT.toml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            'seat_id = "koder"\n'
            'project = "hardening-b"\n'
            'feishu_group_id = "oc_frontstage"\n'
        )

        result = resolve_seat(
            target="koder",
            profile_seats=["memory", "koder", "planner"],
            profile_runtime_seats=["memory", "koder", "planner"],
            profile_project_name="hardening-b",
            profile_handoff_dir=tmp_agents_root / "handoffs",
            profile_heartbeat_owner="koder",
            profile_heartbeat_transport="openclaw",
            _openclaw_home=tmp_openclaw_home,
        )

        assert result.kind == "openclaw"
        assert result.group_id == "oc_frontstage"
        assert result.agent_name == "cartooner"

    def test_openclaw_frontstage_missing_contract_does_not_fall_back_to_tmux(
        self,
        tmp_openclaw_home,
        tmp_agents_root,
    ):
        """If the profile says frontstage=openclaw, missing contract should not route through tmux."""
        result = resolve_seat(
            target="koder",
            profile_seats=["koder", "planner"],
            profile_runtime_seats=["koder", "planner"],
            profile_project_name="hardening-b",
            profile_handoff_dir=tmp_agents_root / "handoffs",
            profile_heartbeat_owner="koder",
            profile_heartbeat_transport="openclaw",
            _openclaw_home=tmp_openclaw_home,
        )

        assert result.kind == "file-only"
        assert "OpenClaw workspace contract" in (result.error or "")


# ── Kind: file-only (edge cases) ───────────────────────────────────────────

class TestSeatResolverFileOnly:
    def test_workspace_exists_but_no_feishu_group_id(self, tmp_openclaw_home, tmp_agents_root):
        """Enhancement 3: workspace contract exists but feishu_group_id missing → file-only."""
        contract = tmp_openclaw_home / "workspace-mor" / "WORKSPACE_CONTRACT.toml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            'seat_id = "koder"\nproject = "hardening-b"\nfeishu_group_id = ""\n'
        )

        result = resolve_seat(
            target="mor",
            profile_seats=["planner"],
            profile_project_name="hardening-b",
            profile_handoff_dir=tmp_agents_root / "handoffs",
            _openclaw_home=tmp_openclaw_home,
        )
        assert result.kind == "file-only"
        assert result.transport == "patrol-handoff-dir"
        assert result.handoff_path is not None
        assert "feishu_group_id is missing" in (result.error or "")

    def test_not_in_seats_no_workspace(self, tmp_openclaw_home, tmp_agents_root):
        """target not in seats, no workspace contract → file-only fallback."""
        result = resolve_seat(
            target="z-unknown-seat-xyz",
            profile_seats=["planner", "builder-1"],
            profile_project_name="hardening-b",
            profile_handoff_dir=tmp_agents_root / "handoffs",
            _openclaw_home=tmp_openclaw_home,
        )
        assert result.kind == "file-only", f"expected file-only, got {result.kind}: {result.error}"
        assert result.transport == "patrol-handoff-dir"
        assert result.handoff_path is not None
        assert result.target == "z-unknown-seat-xyz"


# ── Error kind + strict mode ────────────────────────────────────────────────

class TestSeatResolverStrict:
    def test_file_only_returned_when_not_strict(self, tmp_openclaw_home, tmp_agents_root):
        """strict=False → returns kind=file-only (not error) so caller can still write handoff."""
        result = resolve_seat(
            target="phantom",
            profile_seats=[],
            profile_project_name="hardening-b",
            profile_handoff_dir=tmp_agents_root / "handoffs",
            strict=False,
            _openclaw_home=tmp_openclaw_home,
        )
        assert result.kind == "file-only"
        assert result.transport == "patrol-handoff-dir"
        assert result.handoff_path is not None

    def test_strict_raises_seat_resolution_error(self, tmp_openclaw_home, tmp_agents_root):
        """strict=True → raises SeatResolutionError."""
        with pytest.raises(SeatResolutionError) as exc_info:
            resolve_seat(
                target="phantom",
                profile_seats=[],
                profile_project_name="hardening-b",
                profile_handoff_dir=tmp_agents_root / "handoffs",
                strict=True,
                _openclaw_home=tmp_openclaw_home,
            )
        assert "phantom" in str(exc_info.value)


# ── Post-init validation ─────────────────────────────────────────────────────

class TestSeatResolutionPostInit:
    def test_tmux_without_session_name_is_valid(self):
        """tmux with session_name=None is valid (seat exists but no session.toml yet)."""
        r = SeatResolution(kind="tmux", transport="tmux-send-keys", target="planner", session_name=None)
        assert r.is_tmux
        assert r.session_name is None

    def test_openclaw_without_group_id_raises(self):
        with pytest.raises(ValueError) as exc_info:
            SeatResolution(kind="openclaw", transport="feishu-oc-v1", target="mor", agent_name="mor")
        assert "group_id is required" in str(exc_info.value)

    def test_openclaw_without_agent_name_raises(self):
        with pytest.raises(ValueError) as exc_info:
            SeatResolution(kind="openclaw", transport="feishu-oc-v1", target="mor", group_id="oc_test")
        assert "agent_name is required" in str(exc_info.value)

    def test_error_without_error_message_raises(self):
        with pytest.raises(ValueError) as exc_info:
            SeatResolution(kind="error", transport="unresolved", target="x")
        assert "error message is required" in str(exc_info.value)

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError) as exc_info:
            SeatResolution(kind="unknown", transport="?", target="x")
        assert "Unknown SeatResolution kind" in str(exc_info.value)

    def test_valid_tmux_resolution(self):
        r = SeatResolution(kind="tmux", transport="tmux-send-keys", target="planner", session_name="test")
        assert r.is_tmux
        assert not r.is_openclaw
        assert not r.is_file_only

    def test_valid_openclaw_resolution(self):
        r = SeatResolution(
            kind="openclaw", transport="feishu-oc-v1", target="mor",
            group_id="oc_test", agent_name="mor",
        )
        assert r.is_openclaw
        assert not r.is_tmux


# ── dispatch_error_message ───────────────────────────────────────────────────

class TestDispatchErrorMessage:
    def test_dispatch_error_message_for_openclaw_target(self):
        r = SeatResolution(
            kind="openclaw", transport="feishu-oc-v1", target="mor",
            group_id="oc_test", agent_name="mor",
        )
        msg = r.dispatch_error_message()
        assert "mor" in msg
        assert "kind=openclaw" in msg
        assert "complete_handoff.py" in msg
        assert "AUTO_ADVANCE" in msg


# ── into_error ───────────────────────────────────────────────────────────────

class TestIntoError:
    def test_into_error_non_error_returns_self(self):
        r = SeatResolution(kind="tmux", transport="tmux-send-keys", target="p", session_name="s")
        assert r.into_error(strict=False) is r

    def test_into_error_kind_error_strict_raises(self):
        r = SeatResolution(kind="error", transport="unresolved", target="x", error="bad thing")
        with pytest.raises(SeatResolutionError) as exc_info:
            r.into_error(strict=True)
        assert "bad thing" in str(exc_info.value)

    def test_into_error_kind_error_non_strict_returns_error_resolution(self):
        r = SeatResolution(kind="error", transport="unresolved", target="x", error="bad thing")
        result = r.into_error(strict=False)
        assert result.kind == "error"


# ── resolve_seat_from_profile ───────────────────────────────────────────────

class TestResolveSeatFromProfile:
    def test_resolve_from_profile_object(self, tmp_agents_root, profile_like, tmp_openclaw_home):
        session_dir = tmp_agents_root / "sessions" / "hardening-b" / "builder-1"
        session_dir.mkdir(parents=True)
        (session_dir / "session.toml").write_text('session = "hardening-b-builder-1"\n')

        result = resolve_seat_from_profile("builder-1", profile_like)
        assert result.kind == "tmux"
        # Session name comes from session.toml as written
        assert result.session_name == "hardening-b-builder-1"


# ── real_user_home migration regression tests ────────────────────────────


class TestSeatResolverHomeMigration:
    def test_home_resolves_via_real_user_home_under_sandbox(
        self, tmp_path, monkeypatch
    ):
        """L24-25 fix: when a seat runs under a sandbox HOME, _home() and
        _agents_root() must still resolve the operator's real ~/.agents/
        (via CLAWSEAT_REAL_HOME → real_user_home()), not the sandbox path.

        Without the migration, session.toml lookups, openclaw resolution,
        and handoff path expansion all silently miss because $HOME inside
        the seat points at /...sandbox.../identities/.../home/."""
        real_home = tmp_path / "real-operator-home"
        real_home.mkdir()
        monkeypatch.setenv("HOME", _SANDBOX_HOME_LOOKING)
        monkeypatch.delenv("CLAWSEAT_SANDBOX_HOME_STRICT", raising=False)
        monkeypatch.delenv("AGENT_HOME", raising=False)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(real_home))

        assert seat_resolver_mod._home() == real_home
        assert seat_resolver_mod._agents_root() == real_home / ".agents"
        # OpenClaw resolution should also pivot off real HOME, not sandbox
        monkeypatch.delenv("OPENCLAW_HOME", raising=False)
        assert seat_resolver_mod._openclaw_home_resolved() == real_home / ".openclaw"
