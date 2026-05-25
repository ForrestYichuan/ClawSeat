"""Regression tests for seat_claude_template import path fix (MP030).

Root cause: seat_claude_template.py used 'from core.lib.real_home import real_user_home'
which requires the repo root on sys.path as a package.  sandbox.sh only added
core/scripts to sys.path, so the import failed in the launcher inline Python context,
leaving memory settings.json pointing to the stale launcher-review worktree.

Fix: seat_claude_template.py now adds core/lib to sys.path and imports via the
flat 'from real_home import real_user_home' path (with package import as fallback).
sandbox.sh also adds core/lib and repo root for belt-and-suspenders coverage.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "core" / "scripts"


# ---------------------------------------------------------------------------
# Import from launcher-style sys.path (only core/scripts present initially)
# ---------------------------------------------------------------------------


class TestSeatClaudeTemplateImport:
    def test_importable_with_only_scripts_on_path(self, tmp_path):
        """seat_claude_template must import successfully when sys.path only has
        core/scripts (simulates the sandbox.sh inline Python context)."""
        # Remove any cached import to test from scratch
        for key in list(sys.modules.keys()):
            if "seat_claude_template" in key:
                del sys.modules[key]

        orig_path = sys.path.copy()
        try:
            # Simulate sandbox.sh: only core/scripts on path, NOT repo root or core/lib
            sys.path = [str(_SCRIPTS)] + [p for p in orig_path if "clawseat" not in p]
            mod = importlib.import_module("seat_claude_template")
            # Verify the key function is accessible
            assert hasattr(mod, "copy_seat_claude_template_to_runtime")
            assert hasattr(mod, "render_settings_for_seat")
        finally:
            sys.path[:] = orig_path
            for key in list(sys.modules.keys()):
                if "seat_claude_template" in key:
                    del sys.modules[key]

    def test_real_user_home_callable_after_import(self):
        """After import, real_user_home() must return a valid Path."""
        for key in list(sys.modules.keys()):
            if "seat_claude_template" in key:
                del sys.modules[key]

        sys.path.insert(0, str(_SCRIPTS))
        sys.path.insert(0, str(_REPO / "core" / "lib"))
        try:
            import seat_claude_template as sct
            result = sct.real_user_home()
            assert isinstance(result, Path)
            assert result.is_absolute()
        finally:
            for key in list(sys.modules.keys()):
                if "seat_claude_template" in key:
                    del sys.modules[key]


# ---------------------------------------------------------------------------
# render_settings_for_seat — uses active clawseat_root, not stale launcher path
# ---------------------------------------------------------------------------


class TestRenderSettingsForSeat:
    def _import_sct(self):
        for key in list(sys.modules.keys()):
            if "seat_claude_template" in key:
                del sys.modules[key]
        sys.path.insert(0, str(_SCRIPTS))
        sys.path.insert(0, str(_REPO / "core" / "lib"))
        import seat_claude_template as sct
        return sct

    def test_hook_path_uses_active_clawseat_root(self):
        """render_settings_for_seat must embed the passed clawseat_root, not a
        stale ~/.cartooner-launcher-review path."""
        sct = self._import_sct()
        sentinel_root = Path("/tmp/test-active-clawseat-root")
        settings = sct.render_settings_for_seat("memory", clawseat_root=sentinel_root)
        stop_hooks = settings.get("hooks", {}).get("Stop", [])
        assert stop_hooks, "memory settings must have a Stop hook"
        command = stop_hooks[0]["hooks"][0]["command"]
        assert str(sentinel_root) in command, (
            f"Hook command must use active clawseat_root={sentinel_root}, got: {command!r}"
        )
        assert ".cartooner-launcher-review" not in command, (
            f"Hook command must not reference stale launcher review path: {command!r}"
        )

    def test_hook_path_does_not_contain_launcher_review(self):
        """No settings hook must reference the stale ~/.cartooner-launcher-review path."""
        sct = self._import_sct()
        settings = sct.render_settings_for_seat("memory", clawseat_root=_REPO)
        hook_json = json.dumps(settings)
        assert ".cartooner-launcher-review" not in hook_json, (
            f"settings must not reference stale launcher path: {hook_json!r}"
        )

    def test_default_clawseat_root_is_repo(self):
        """With no explicit clawseat_root, must use REPO_ROOT (repo where script lives)."""
        sct = self._import_sct()
        settings = sct.render_settings_for_seat("memory")
        hook_json = json.dumps(settings)
        assert str(_REPO) in hook_json, (
            f"default clawseat_root must be the active repo {_REPO}, got: {hook_json!r}"
        )

    def test_copy_to_runtime_writes_correct_settings(self, tmp_path):
        """copy_seat_claude_template_to_runtime must write settings.json with active root."""
        sct = self._import_sct()
        engineers_root = tmp_path / "engineers"
        runtime_claude = tmp_path / "runtime" / ".claude"
        runtime_claude.mkdir(parents=True)
        active_root = _REPO

        with patch.object(sct, "real_user_home", return_value=tmp_path):
            sct.copy_seat_claude_template_to_runtime(
                engineers_root,
                "memory",
                runtime_claude,
                clawseat_root=active_root,
            )

        settings_path = runtime_claude / "settings.json"
        assert settings_path.exists(), "settings.json must be written"
        data = json.loads(settings_path.read_text())
        hook_json = json.dumps(data)
        assert str(active_root) in hook_json, (
            f"settings.json must embed active_root={active_root}"
        )
        assert ".cartooner-launcher-review" not in hook_json
