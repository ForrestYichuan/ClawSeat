"""FR-1: seat_harness_memory save/load/reset roundtrip tests."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from seat_harness_memory import (  # noqa: E402
    load_last_harness,
    reset_all_harness_memory,
    save_last_harness,
)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    save_last_harness("planner", "claude", "oauth", "anthropic", model="", home=tmp_path)
    result = load_last_harness("planner", home=tmp_path)
    assert result is not None
    assert result["tool"] == "claude"
    assert result["auth_mode"] == "oauth"
    assert result["provider"] == "anthropic"
    assert "model" not in result


def test_save_with_model_roundtrip(tmp_path: Path) -> None:
    save_last_harness("builder", "codex", "oauth", "openai", model="gpt-5.4", home=tmp_path)
    result = load_last_harness("builder", home=tmp_path)
    assert result is not None
    assert result["tool"] == "codex"
    assert result["model"] == "gpt-5.4"


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_last_harness("qa", home=tmp_path) is None


def test_file_permissions_are_600(tmp_path: Path) -> None:
    save_last_harness("reviewer", "gemini", "oauth", "google", home=tmp_path)
    path = tmp_path / ".agents" / "engineers" / "reviewer" / "last-harness.toml"
    assert path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_reset_removes_all_harness_files(tmp_path: Path) -> None:
    save_last_harness("planner", "claude", "oauth", "anthropic", home=tmp_path)
    save_last_harness("builder", "codex", "oauth", "openai", home=tmp_path)
    removed = reset_all_harness_memory(home=tmp_path)
    assert set(removed) == {"planner", "builder"}
    assert load_last_harness("planner", home=tmp_path) is None
    assert load_last_harness("builder", home=tmp_path) is None


def test_reset_returns_empty_when_nothing_to_remove(tmp_path: Path) -> None:
    removed = reset_all_harness_memory(home=tmp_path)
    assert removed == []


def test_overwrite_updates_choice(tmp_path: Path) -> None:
    save_last_harness("planner", "claude", "oauth", "anthropic", home=tmp_path)
    save_last_harness("planner", "codex", "api", "xcode-best", model="gpt-5.4", home=tmp_path)
    result = load_last_harness("planner", home=tmp_path)
    assert result is not None
    assert result["tool"] == "codex"
    assert result["auth_mode"] == "api"
    assert result["provider"] == "xcode-best"


def test_load_uses_fallback_when_toml_modules_are_unavailable(tmp_path: Path, monkeypatch) -> None:
    save_last_harness("planner", "codex", "api", "openai", model="gpt-5.4", home=tmp_path)
    monkeypatch.setitem(sys.modules, "tomllib", None)
    monkeypatch.setitem(sys.modules, "tomli", None)

    result = load_last_harness("planner", home=tmp_path)

    assert result is not None
    assert result["tool"] == "codex"
    assert result["auth_mode"] == "api"
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-5.4"
