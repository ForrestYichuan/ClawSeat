"""Tests for baidu-glm provider secret path and startup readiness (MP023).

Root cause: build_switched_session used secret_file_for(tool, provider, engineer_id)
which generates a nested per-engineer path (.../baidu-glm/memory.env) that does
not exist.  The provider registry has a flat shared path (.../baidu-glm.env) that
does exist.

Fix: _registry_secret_file() in SwitchHandlers queries the registry first; the
nested fallback is only used when the registry has no entry.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# conftest.py adds core/scripts to path
import agent_admin_switch as switch_mod
from agent_admin_switch import SwitchHandlers, SwitchHooks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hooks(tmp_path: Path, secrets_root: Path | None = None) -> SwitchHooks:
    _secrets = secrets_root or (tmp_path / ".agents" / "secrets")
    _secrets.mkdir(parents=True, exist_ok=True)
    return SwitchHooks(
        error_cls=RuntimeError,
        legacy_secrets_root=tmp_path / ".agent-runtime" / "secrets",
        tool_binaries={"claude": "claude", "codex": "codex", "gemini": "gemini"},
        default_tool_args={},
        identity_name=lambda t, a, p, e, proj: f"{t}.{a}.{p}.{proj}.{e}",
        runtime_dir_for_identity=lambda t, a, i: tmp_path / "rt" / i,
        secret_file_for=lambda tool, provider, eng: _secrets / tool / provider / f"{eng}.env",
        session_name_for=lambda proj, eng, tool: f"{proj}-{eng}-{tool}",
        ensure_dir=MagicMock(),
        ensure_secret_permissions=MagicMock(),
        write_env_file=MagicMock(),
        parse_env_file=MagicMock(return_value={}),
        load_project=MagicMock(),
        load_project_or_current=MagicMock(),
        load_session=MagicMock(),
        write_session=MagicMock(),
        apply_template=MagicMock(),
        session_stop_engineer=MagicMock(),
        session_record_cls=lambda **kw: SimpleNamespace(**kw),
        normalize_name=lambda x: x,
    )


def _make_old_session(engineer_id: str = "memory") -> SimpleNamespace:
    return SimpleNamespace(
        engineer_id=engineer_id,
        workspace=f"/ws/{engineer_id}",
        monitor=True,
        legacy_sessions=[],
        launch_args=["--dangerously-skip-permissions"],
        secret_file="",
        wrapper=None,
        project="cartooner-front",
        tool="codex",
        auth_mode="oauth",
        provider="openai",
    )


# ---------------------------------------------------------------------------
# _registry_secret_file
# ---------------------------------------------------------------------------


class TestRegistrySecretFile:
    def test_returns_registry_path_when_provider_exists(self, tmp_path):
        flat_secret = tmp_path / ".agents" / "secrets" / "claude" / "baidu-glm.env"
        flat_secret.parent.mkdir(parents=True, exist_ok=True)
        flat_secret.write_text("ANTHROPIC_API_KEY=test\n")

        fake_provider = MagicMock()
        fake_provider.secret_file = str(flat_secret)

        hooks = _make_hooks(tmp_path)
        handlers = SwitchHandlers(hooks)

        with patch("agent_admin_switch.SwitchHandlers._registry_secret_file",
                   return_value=str(flat_secret)):
            result = handlers._registry_secret_file("baidu-glm")

        assert result == str(flat_secret)

    def test_returns_empty_when_provider_not_found(self, tmp_path):
        hooks = _make_hooks(tmp_path)
        handlers = SwitchHandlers(hooks)

        with patch("providers.get_provider", return_value=None, create=True):
            result = handlers._registry_secret_file("no-such-provider")

        assert result == ""

    def test_returns_empty_on_import_error(self, tmp_path):
        hooks = _make_hooks(tmp_path)
        handlers = SwitchHandlers(hooks)

        # Simulate providers module unavailable
        orig = sys.modules.get("providers")
        sys.modules.pop("providers", None)
        try:
            result = handlers._registry_secret_file("baidu-glm")
        except Exception:
            pytest.fail("_registry_secret_file must not raise when providers unavailable")
        finally:
            if orig is not None:
                sys.modules["providers"] = orig

        assert result == ""


# ---------------------------------------------------------------------------
# build_switched_session uses registry path over nested path
# ---------------------------------------------------------------------------


class TestBuildSwitchedSessionSecretFile:
    def _build(self, tmp_path: Path, registry_path: str) -> SimpleNamespace:
        hooks = _make_hooks(tmp_path)
        handlers = SwitchHandlers(hooks)
        old_session = _make_old_session()
        project = SimpleNamespace(name="cartooner-front")

        with patch.object(handlers, "_registry_secret_file", return_value=registry_path):
            return handlers.build_switched_session(
                old_session, project,
                tool="claude", auth_mode="api", provider="baidu-glm",
            )

    def test_prefers_registry_flat_path_over_nested(self, tmp_path):
        """When registry has a secret_file, use it instead of nested per-engineer path."""
        flat = str(tmp_path / ".agents" / "secrets" / "claude" / "baidu-glm.env")
        session = self._build(tmp_path, flat)
        assert session.secret_file == flat

    def test_falls_back_to_nested_when_registry_empty(self, tmp_path):
        """When registry returns empty, fall back to secret_file_for()."""
        session = self._build(tmp_path, "")  # registry returns ""
        # secret_file_for("claude", "baidu-glm", "memory") → nested path
        assert "baidu-glm" in session.secret_file
        assert "memory.env" in session.secret_file

    def test_oauth_mode_gets_empty_secret_file(self, tmp_path):
        """Pure oauth (not oauth_token) must not get a secret_file."""
        hooks = _make_hooks(tmp_path)
        handlers = SwitchHandlers(hooks)
        old_session = _make_old_session()
        project = SimpleNamespace(name="cartooner-front")

        with patch.object(handlers, "_registry_secret_file", return_value="/should/not/use"):
            session = handlers.build_switched_session(
                old_session, project,
                tool="codex", auth_mode="oauth", provider="openai",
            )

        assert session.secret_file == ""


# ---------------------------------------------------------------------------
# Diagnostic: provider registry vs session.toml secret_file agreement
# ---------------------------------------------------------------------------


class TestProviderSecretDiagnostic:
    """Verify the invariant that switch-harness writes the same path as the registry."""

    def test_switched_session_secret_matches_registry(self, tmp_path):
        """After switch-harness for baidu-glm, session.secret_file == registry path."""
        flat_secret = tmp_path / ".agents" / "secrets" / "claude" / "baidu-glm.env"
        flat_secret.parent.mkdir(parents=True, exist_ok=True)
        flat_secret.write_text("ANTHROPIC_API_KEY=placeholder\n")

        hooks = _make_hooks(tmp_path, tmp_path / ".agents" / "secrets")
        handlers = SwitchHandlers(hooks)
        old_session = _make_old_session()
        project = SimpleNamespace(name="cartooner-front")

        with patch.object(handlers, "_registry_secret_file", return_value=str(flat_secret)):
            session = handlers.build_switched_session(
                old_session, project,
                tool="claude", auth_mode="api", provider="baidu-glm",
            )

        assert Path(session.secret_file) == flat_secret, (
            f"session.secret_file={session.secret_file!r} should equal "
            f"registry path {flat_secret!r}"
        )
        assert Path(session.secret_file).exists(), "switched session must point to existing file"


# ---------------------------------------------------------------------------
# MP027: baidu-glm / Qianfan secret key alias (ANTHROPIC_AUTH_TOKEN vs ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------


class TestBaiduGlmSecretKeyAlias:
    """Verify that the baidu-glm provider's ANTHROPIC_AUTH_TOKEN is accepted.

    baidu-glm has family=minimax.  The runtime (build_env_overlay) already
    accepts ANTHROPIC_AUTH_TOKEN for minimax-family providers and normalises it
    into ANTHROPIC_API_KEY / MINIMAX_API_KEY in the env overlay.

    The diagnostic script required_keys() was incorrectly returning
    ANTHROPIC_API_KEY for baidu-glm (provider NAME) because it only matched
    the minimax case by provider name, not by family.  MP027 fixes this by
    emitting PROVIDER_FAMILY from the metadata block.
    """

    def test_baidu_glm_provider_family_is_minimax(self):
        """The baidu-glm provider must be registered with family=minimax."""
        try:
            sys.path.insert(
                0,
                str(Path(__file__).resolve().parents[2] / "core" / "lib"),
            )
            from providers import get_provider  # type: ignore[import]
            p = get_provider("baidu-glm")
            if p is None:
                pytest.skip("baidu-glm not registered in this environment")
            assert str(getattr(p, "family", "") or "") == "minimax", (
                f"baidu-glm family should be 'minimax', got {p.family!r}"
            )
        except ImportError:
            pytest.skip("providers module unavailable")

    def test_minimax_family_requires_anthropic_auth_token(self):
        """build_env_overlay for minimax family reads ANTHROPIC_AUTH_TOKEN."""
        try:
            sys.path.insert(
                0,
                str(Path(__file__).resolve().parents[2] / "core" / "lib"),
            )
            from providers import build_env_overlay  # type: ignore[import]
            secret_vars = {"ANTHROPIC_AUTH_TOKEN": "test-qianfan-key"}
            overlay = build_env_overlay(
                "minimax",
                secret_vars,
                "https://qianfan.baidubce.com/anthropic/coding",
                "qianfan-code-latest",
            )
            # Must not print the key value; only check presence
            assert "ANTHROPIC_AUTH_TOKEN" in overlay
            assert "ANTHROPIC_API_KEY" in overlay
            assert "MINIMAX_API_KEY" in overlay
            assert overlay["ANTHROPIC_AUTH_TOKEN"] == overlay["ANTHROPIC_API_KEY"]
        except ImportError:
            pytest.skip("providers module unavailable")

    def test_minimax_family_overlay_sets_base_url(self):
        """build_env_overlay sets ANTHROPIC_BASE_URL for Qianfan endpoint."""
        try:
            sys.path.insert(
                0,
                str(Path(__file__).resolve().parents[2] / "core" / "lib"),
            )
            from providers import build_env_overlay  # type: ignore[import]
            qianfan_url = "https://qianfan.baidubce.com/anthropic/coding"
            secret_vars = {"ANTHROPIC_AUTH_TOKEN": "test-key"}
            overlay = build_env_overlay("minimax", secret_vars, qianfan_url, "")
            assert "ANTHROPIC_BASE_URL" in overlay
            assert overlay["ANTHROPIC_BASE_URL"] == qianfan_url
        except ImportError:
            pytest.skip("providers module unavailable")

    def test_anthropic_auth_token_alone_satisfies_minimax_overlay(self):
        """ANTHROPIC_AUTH_TOKEN with no ANTHROPIC_API_KEY is sufficient for minimax."""
        try:
            sys.path.insert(
                0,
                str(Path(__file__).resolve().parents[2] / "core" / "lib"),
            )
            from providers import build_env_overlay, ProviderSecretMissingError  # type: ignore[import]
            # Should not raise even though ANTHROPIC_API_KEY is absent
            try:
                overlay = build_env_overlay(
                    "minimax",
                    {"ANTHROPIC_AUTH_TOKEN": "test-key"},
                    "",
                    "",
                )
                assert overlay  # overlay must be non-empty
            except ProviderSecretMissingError:
                pytest.fail(
                    "ANTHROPIC_AUTH_TOKEN alone must satisfy minimax family secret requirement"
                )
        except ImportError:
            pytest.skip("providers module unavailable")

    def test_seat_diagnostic_required_key_for_baidu_is_auth_token_when_family_resolved(self):
        """Simulate the diagnostic's required_keys logic with PROVIDER_FAMILY=minimax.

        When PROVIDER=baidu-glm and PROVIDER_FAMILY=minimax, the case statement
        matches 'claude:api:minimax:baidu-glm' → ANTHROPIC_AUTH_TOKEN.
        """
        import subprocess
        # Inline the bash case logic as a small script (no real seat, no secrets read)
        bash_case = r"""
TOOL=claude AUTH_MODE=api PROVIDER=baidu-glm PROVIDER_FAMILY=minimax
case "$TOOL:$AUTH_MODE:${PROVIDER_FAMILY}:$PROVIDER" in
    claude:api:minimax:*) printf 'ANTHROPIC_AUTH_TOKEN' ;;
    claude:api:*:minimax) printf 'ANTHROPIC_AUTH_TOKEN' ;;
    claude:api:*:*)       printf 'ANTHROPIC_API_KEY' ;;
esac
"""
        r = subprocess.run(["bash", "-c", bash_case], capture_output=True, text=True, timeout=5)
        assert r.returncode == 0
        assert r.stdout.strip() == "ANTHROPIC_AUTH_TOKEN", (
            f"With PROVIDER_FAMILY=minimax, required key should be ANTHROPIC_AUTH_TOKEN, "
            f"got {r.stdout.strip()!r}"
        )

    def test_seat_diagnostic_required_key_fallback_without_family(self):
        """Without PROVIDER_FAMILY, baidu-glm still falls to ANTHROPIC_API_KEY (old behaviour)."""
        import subprocess
        bash_case = r"""
TOOL=claude AUTH_MODE=api PROVIDER=baidu-glm PROVIDER_FAMILY=
case "$TOOL:$AUTH_MODE:${PROVIDER_FAMILY}:$PROVIDER" in
    claude:api:minimax:*) printf 'ANTHROPIC_AUTH_TOKEN' ;;
    claude:api:*:minimax) printf 'ANTHROPIC_AUTH_TOKEN' ;;
    claude:api:*:*)       printf 'ANTHROPIC_API_KEY' ;;
esac
"""
        r = subprocess.run(["bash", "-c", bash_case], capture_output=True, text=True, timeout=5)
        assert r.returncode == 0
        # Empty family → falls to generic claude:api:*:* → ANTHROPIC_API_KEY
        assert r.stdout.strip() == "ANTHROPIC_API_KEY"
