from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "core" / "lib", REPO / "core" / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agent_admin_provider import ProviderHandlers
from providers import (
    Provider,
    ProviderSecretMissingError,
    add_provider,
    build_env_overlay,
    get_provider,
    list_providers,
    migrate_legacy_provider_secrets,
    provider_secret_file_path,
    read_providers,
    rename_provider,
)


def _set_home(monkeypatch: object, home: Path) -> None:
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))


def _write_session_toml(path: Path, provider: str, secret_file: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f'provider = "{provider}"',
                f'secret_file = "{secret_file}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_provider_cli_add_get_list_redacts_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    _set_home(monkeypatch, home)

    handlers = ProviderHandlers(RuntimeError)
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk-secret-value\n"))

    rc = handlers.add(
        SimpleNamespace(
            name="anthropic-console",
            tool="claude",
            kind="api_key",
            family="anthropic",
            base_url="",
            model="",
            secret_stdin=True,
            json=True,
        )
    )
    assert rc == 0

    output = capsys.readouterr()
    assert "sk-secret-value" not in output.out
    assert "sk-secret-value" not in output.err

    payload = json.loads(output.out)
    provider = payload["provider"]
    secret_file = home / ".agents" / "secrets" / "claude" / "anthropic-console.env"
    assert provider["secret_file"] == str(secret_file)
    assert provider["base_url"] == "https://api.anthropic.com"
    assert provider["has_secret"] is True
    assert secret_file.read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=sk-secret-value\n"
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600

    get_rc = handlers.get(SimpleNamespace(name="anthropic-console", json=True))
    assert get_rc == 0
    get_output = capsys.readouterr()
    assert "sk-secret-value" not in get_output.out
    assert "sk-secret-value" not in get_output.err
    get_payload = json.loads(get_output.out)
    assert get_payload["provider"]["name"] == "anthropic-console"
    assert get_payload["provider"]["has_secret"] is True

    list_rc = handlers.list(SimpleNamespace(tool="claude", json=True))
    assert list_rc == 0
    list_output = capsys.readouterr()
    assert "sk-secret-value" not in list_output.out
    assert "sk-secret-value" not in list_output.err
    list_payload = json.loads(list_output.out)
    assert [item["name"] for item in list_payload["providers"]] == ["anthropic-console"]


def test_xcode_best_provider_family_maps_claude_sdk_and_codex_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _set_home(monkeypatch, home)

    result = add_provider(
        Provider(
            name="xcode-best",
            tool="claude",
            kind="api_key",
            family="xcode-best",
            secret_file="",
            base_url="",
            model="",
        ),
        "sk-xcode-secret\n",
    )

    assert result.provider.family == "xcode-best"
    assert result.provider.base_url == "https://xcode.best"
    assert result.provider.model == "gpt-5.5"
    assert Path(result.provider.secret_file).read_text(encoding="utf-8") == "XCODE_BEST_API_KEY=sk-xcode-secret\n"

    overlay = build_env_overlay(
        "xcode-best",
        {"XCODE_BEST_API_KEY": "sk-xcode-secret"},
        result.provider.base_url,
        result.provider.model,
    )
    assert overlay["ANTHROPIC_AUTH_TOKEN"] == "sk-xcode-secret"
    assert overlay["ANTHROPIC_BASE_URL"] == "https://xcode.best"
    assert overlay["ANTHROPIC_MODEL"] == "gpt-5.5"
    assert overlay["OPENAI_API_KEY"] == "sk-xcode-secret"
    assert overlay["OPENAI_MODEL"] == "gpt-5.5"


def test_deepseek_provider_family_maps_to_claude_auth_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _set_home(monkeypatch, home)

    result = add_provider(
        Provider(
            name="deepseek",
            tool="claude",
            kind="api_key",
            family="deepseek",
            secret_file="",
            base_url="",
            model="",
        ),
        "sk-deepseek-secret\n",
    )

    assert result.provider.family == "deepseek"
    assert result.provider.base_url == "https://api.deepseek.com/anthropic"
    assert result.provider.model == "deepseek-v4-pro[1M]"
    assert Path(result.provider.secret_file).read_text(encoding="utf-8") == "DEEPSEEK_API_KEY=sk-deepseek-secret\n"

    overlay = build_env_overlay(
        "deepseek",
        {"DEEPSEEK_API_KEY": "sk-deepseek-secret"},
        result.provider.base_url,
        result.provider.model,
    )
    assert overlay["ANTHROPIC_AUTH_TOKEN"] == "sk-deepseek-secret"
    assert overlay["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert overlay["ANTHROPIC_MODEL"] == "deepseek-v4-pro[1M]"
    assert overlay["DEEPSEEK_API_KEY"] == "sk-deepseek-secret"


def test_deepseek_placeholder_secret_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _set_home(monkeypatch, home)

    with pytest.raises(ProviderSecretMissingError):
        add_provider(
            Provider(
                name="deepseek",
                tool="claude",
                kind="api_key",
                family="deepseek",
                secret_file="",
                base_url="",
                model="",
            ),
            "DEEPSEEK_API_KEY=<set-by-operator>\nDEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic\n",
        )


def test_legacy_provider_migration_renames_per_engineer_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _set_home(monkeypatch, home)

    legacy_dir = home / ".agents" / "secrets" / "claude" / "minimax"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    first = legacy_dir / "engineer-a.env"
    second = legacy_dir / "engineer-b.env"
    first.write_text(
        "ANTHROPIC_AUTH_TOKEN=minimax-a\nMINIMAX_API_HOST=https://api.minimaxi.com/anthropic\n",
        encoding="utf-8",
    )
    expected_secret = (
        "ANTHROPIC_AUTH_TOKEN=minimax-b\nMINIMAX_API_HOST=https://api.minimaxi.com/anthropic\n"
    )
    second.write_text(expected_secret, encoding="utf-8")
    os.utime(first, (1_700_000_000, 1_700_000_000))
    os.utime(second, (1_700_000_100, 1_700_000_100))

    results = migrate_legacy_provider_secrets(home=home)
    assert results

    providers = read_providers(home / ".agents" / "providers.toml")
    provider = providers.providers["minimax"]
    assert provider.tool == "claude"
    assert provider.family == "minimax"
    assert provider.kind == "api_key"
    assert provider.secret_file == str(home / ".agents" / "secrets" / "claude" / "minimax.env")
    assert provider.has_secret is True

    secret_file = home / ".agents" / "secrets" / "claude" / "minimax.env"
    assert secret_file.read_text(encoding="utf-8") == expected_secret
    migrated = sorted(legacy_dir.glob("*.migrated-*"))
    assert len(migrated) == 2
    assert all(path.suffixes[-1].startswith(".migrated-") or ".migrated-" in path.name for path in migrated)


def test_legacy_deepseek_provider_migration_preserves_deepseek_family(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _set_home(monkeypatch, home)

    legacy_dir = home / ".agents" / "secrets" / "claude" / "deepseek"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_secret = (
        "DEEPSEEK_API_KEY=sk-deepseek-real\n"
        "DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic\n"
        "DEEPSEEK_MODEL=deepseek-v4-pro[1M]\n"
    )
    (legacy_dir / "planner.env").write_text(legacy_secret, encoding="utf-8")

    results = migrate_legacy_provider_secrets(home=home)
    assert results

    providers = read_providers(home / ".agents" / "providers.toml")
    provider = providers.providers["deepseek"]
    assert provider.tool == "claude"
    assert provider.family == "deepseek"
    assert provider.kind == "api_key"
    assert provider.base_url == "https://api.deepseek.com/anthropic"
    assert provider.model == "deepseek-v4-pro[1M]"
    assert provider.secret_file == str(home / ".agents" / "secrets" / "claude" / "deepseek.env")
    assert Path(provider.secret_file).read_text(encoding="utf-8") == legacy_secret


def test_rename_provider_updates_session_reference_and_secret_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _set_home(monkeypatch, home)

    result = add_provider(
        Provider(
            name="anthropic-console",
            tool="claude",
            kind="api_key",
            family="anthropic",
            secret_file="",
            base_url="",
            model="",
        ),
        "sk-rename-secret\n",
    )
    assert result.provider.secret_file.endswith("anthropic-console.env")

    session_toml = home / ".agents" / "sessions" / "demo" / "seat-a" / "session.toml"
    _write_session_toml(session_toml, "anthropic-console", Path(result.provider.secret_file))

    rename_result = rename_provider("anthropic-console", "anthropic-console-v2")
    assert rename_result.provider.name == "anthropic-console-v2"
    assert rename_result.provider.secret_file.endswith("anthropic-console-v2.env")

    assert not (home / ".agents" / "secrets" / "claude" / "anthropic-console.env").exists()
    assert (home / ".agents" / "secrets" / "claude" / "anthropic-console-v2.env").exists()
    session_text = session_toml.read_text(encoding="utf-8")
    assert 'provider = "anthropic-console-v2"' in session_text
    assert 'secret_file = "' in session_text
    assert "anthropic-console-v2.env" in session_text
