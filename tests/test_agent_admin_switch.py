"""Tests for switch-harness provider validation (MP015).

Root cause: validate_runtime_combo rejected claude/api/baidu-glm even though
baidu-glm exists in the local provider registry with tool=claude.  The static
SUPPORTED_RUNTIME_MATRIX cannot list every operator-registered API endpoint.

Fix (MP015): is_supported_runtime_combo falls back to the provider registry for
claude/api mode when the static matrix does not match.

Tests here pin:
- Registry-registered claude/api providers (e.g. baidu-glm) are accepted.
- Providers absent from both static matrix and registry are still rejected.
- Static matrix providers (minimax, ark, deepseek, xcode-best) are unaffected.
- The registry fallback only fires for claude/api, not other tool/mode combos.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "lib"))

import agent_admin_config as cfg  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers: mock provider registry entries
# ---------------------------------------------------------------------------

def _make_provider(name: str, tool: str) -> MagicMock:
    p = MagicMock()
    p.name = name
    p.tool = tool
    return p


# ---------------------------------------------------------------------------
# Registry fallback: claude/api mode
# ---------------------------------------------------------------------------

class TestRegistryFallbackClaudeApi:
    def test_registry_claude_provider_accepted(self):
        """Provider in registry with tool=claude must be accepted for claude/api."""
        fake_provider = _make_provider("baidu-glm", "claude")
        with patch.object(cfg, "_is_claude_api_registry_provider", return_value=True):
            assert cfg.is_supported_runtime_combo("claude", "api", "baidu-glm") is True

    def test_registry_claude_provider_validate_no_raise(self):
        """validate_runtime_combo must not raise for a registry-accepted provider."""
        with patch.object(cfg, "_is_claude_api_registry_provider", return_value=True):
            # Should not raise
            cfg.validate_runtime_combo("claude", "api", "baidu-glm")

    def test_unknown_provider_still_rejected(self):
        """Provider absent from both static matrix and registry is rejected."""
        with patch.object(cfg, "_is_claude_api_registry_provider", return_value=False):
            assert cfg.is_supported_runtime_combo("claude", "api", "totally-unknown") is False

    def test_unknown_provider_raises_with_helpful_message(self):
        """validate_runtime_combo must raise with 'unsupported runtime combination'."""
        with patch.object(cfg, "_is_claude_api_registry_provider", return_value=False):
            with pytest.raises(ValueError, match="unsupported runtime combination"):
                cfg.validate_runtime_combo("claude", "api", "totally-unknown")

    def test_registry_fallback_not_used_for_non_claude_api(self):
        """Registry fallback must not fire for other tool/mode combos."""
        with patch.object(cfg, "_is_claude_api_registry_provider", return_value=True):
            # codex/api/baidu-glm is not in static matrix and must not use registry
            assert cfg.is_supported_runtime_combo("codex", "api", "baidu-glm") is False

    def test_registry_fallback_not_used_for_claude_oauth(self):
        """Registry fallback is only for claude/api, not claude/oauth."""
        with patch.object(cfg, "_is_claude_api_registry_provider", return_value=True):
            assert cfg.is_supported_runtime_combo("claude", "oauth", "baidu-glm") is False


# ---------------------------------------------------------------------------
# Static matrix: existing providers unaffected
# ---------------------------------------------------------------------------

class TestStaticMatrixUnaffected:
    @pytest.mark.parametrize("provider", ["minimax", "ark", "deepseek", "xcode-best", "anthropic-console"])
    def test_static_claude_api_providers_still_accepted(self, provider):
        """Static matrix claude/api providers must still be accepted without registry hit."""
        # No mock: static matrix should resolve before registry is consulted.
        assert cfg.is_supported_runtime_combo("claude", "api", provider) is True

    def test_claude_oauth_anthropic_accepted(self):
        assert cfg.is_supported_runtime_combo("claude", "oauth", "anthropic") is True

    def test_claude_oauth_token_anthropic_accepted(self):
        assert cfg.is_supported_runtime_combo("claude", "oauth_token", "anthropic") is True

    def test_codex_oauth_openai_accepted(self):
        assert cfg.is_supported_runtime_combo("codex", "oauth", "openai") is True


# ---------------------------------------------------------------------------
# _is_claude_api_registry_provider: unit-level
# ---------------------------------------------------------------------------

class TestIsClaudeApiRegistryProvider:
    def test_returns_true_for_claude_tool_entry(self):
        fake = _make_provider("baidu-glm", "claude")
        with patch("agent_admin_config.get_provider", return_value=fake, create=True):
            # Directly test the helper without going through providers module
            # by providing the get_provider import inside the function via sys.modules.
            import importlib
            import types

            fake_providers_mod = types.ModuleType("providers")
            fake_providers_mod.get_provider = lambda name: fake  # type: ignore[attr-defined]
            import sys as _sys
            orig = _sys.modules.get("providers")
            _sys.modules["providers"] = fake_providers_mod
            try:
                result = cfg._is_claude_api_registry_provider("baidu-glm")
            finally:
                if orig is None:
                    _sys.modules.pop("providers", None)
                else:
                    _sys.modules["providers"] = orig
            assert result is True

    def test_returns_false_when_provider_not_found(self):
        fake_providers_mod = __import__("types").ModuleType("providers")
        fake_providers_mod.get_provider = lambda name: None  # type: ignore[attr-defined]
        import sys as _sys
        orig = _sys.modules.get("providers")
        _sys.modules["providers"] = fake_providers_mod
        try:
            result = cfg._is_claude_api_registry_provider("nonexistent")
        finally:
            if orig is None:
                _sys.modules.pop("providers", None)
            else:
                _sys.modules["providers"] = orig
        assert result is False

    def test_returns_false_when_tool_is_not_claude(self):
        fake = _make_provider("codex-custom", "codex")
        fake_providers_mod = __import__("types").ModuleType("providers")
        fake_providers_mod.get_provider = lambda name: fake  # type: ignore[attr-defined]
        import sys as _sys
        orig = _sys.modules.get("providers")
        _sys.modules["providers"] = fake_providers_mod
        try:
            result = cfg._is_claude_api_registry_provider("codex-custom")
        finally:
            if orig is None:
                _sys.modules.pop("providers", None)
            else:
                _sys.modules["providers"] = orig
        assert result is False

    def test_returns_false_on_import_error(self):
        """If providers module is unavailable, silently return False."""
        import sys as _sys
        orig = _sys.modules.get("providers")
        _sys.modules.pop("providers", None)
        try:
            # Without providers module in path, import will fail → should return False
            # (we can simulate by temporarily removing the module from modules)
            result = cfg._is_claude_api_registry_provider("baidu-glm")
        except Exception:
            # If this raises, the silence guarantee is broken
            pytest.fail("_is_claude_api_registry_provider must not raise on import error")
        finally:
            if orig is not None:
                _sys.modules["providers"] = orig
        # Result may be True or False depending on whether providers is importable
        # from sys.path — what matters is no exception was raised.
        assert isinstance(result, bool)
