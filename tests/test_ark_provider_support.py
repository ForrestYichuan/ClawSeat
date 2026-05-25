from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import agent_admin_session as aas
from agent_admin_config import is_supported_runtime_combo
from agent_admin_switch import SwitchHandlers


_HELPERS_PATH = Path(__file__).with_name("test_install_isolation.py")
_HELPERS_SPEC = importlib.util.spec_from_file_location("test_install_isolation_helpers", _HELPERS_PATH)
assert _HELPERS_SPEC is not None and _HELPERS_SPEC.loader is not None
_HELPERS = importlib.util.module_from_spec(_HELPERS_SPEC)
_HELPERS_SPEC.loader.exec_module(_HELPERS)

_fake_install_root = _HELPERS._fake_install_root
_read_jsonl = _HELPERS._read_jsonl
_write_executable = _HELPERS._write_executable


class _SessionRecord(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def _write_ark_scan_script(root: Path, *, api_key: str = "sk-ark-detected") -> None:
    _write_executable(
        root / "core" / "skills" / "memory-oracle" / "scripts" / "scan_environment.py",
        f"""#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
machine = Path(args.output) / "machine"
machine.mkdir(parents=True, exist_ok=True)
(machine / "credentials.json").write_text(json.dumps({{
    "keys": {{
        "ARK_API_KEY": {{"value": "{api_key}"}},
        "ARK_BASE_URL": {{"value": "https://ark.cn-beijing.volces.com/api/coding"}},
    }},
    "oauth": {{"has_any": False}},
}}), encoding="utf-8")
for name in ("network", "openclaw", "github", "current_context"):
    (machine / f"{{name}}.json").write_text("{{}}", encoding="utf-8")
""",
    )


def _make_switch_handlers(fake_home: Path, old_session: SimpleNamespace) -> tuple[SwitchHandlers, list[SimpleNamespace]]:
    writes: list[SimpleNamespace] = []
    hooks = SimpleNamespace(
        error_cls=RuntimeError,
        legacy_secrets_root=fake_home / ".agent-runtime" / "secrets",
        tool_binaries={"claude": "claude"},
        default_tool_args={"claude": []},
        identity_name=lambda tool, auth_mode, provider, engineer_id, project: (
            f"{tool}.{auth_mode}.{provider}.{project}.{engineer_id}"
        ),
        runtime_dir_for_identity=lambda tool, auth_mode, identity: (
            fake_home / ".agents" / "runtime" / "identities" / tool / auth_mode / identity
        ),
        secret_file_for=lambda tool, provider, engineer_id: (
            fake_home / ".agents" / "secrets" / tool / provider / f"{engineer_id}.env"
        ),
        session_name_for=lambda project, engineer_id, tool: f"{project}-{engineer_id}-{tool}",
        ensure_dir=lambda path: path.mkdir(parents=True, exist_ok=True),
        ensure_secret_permissions=lambda path: path.chmod(0o600),
        write_env_file=lambda *args, **kwargs: None,
        parse_env_file=lambda path: {},
        load_project=lambda project: SimpleNamespace(name=project),
        load_project_or_current=lambda project: SimpleNamespace(name=project),
        load_session=lambda project, engineer_id: old_session,
        write_session=lambda session: writes.append(session),
        apply_template=lambda session, project: None,
        session_stop_engineer=lambda session: None,
        session_record_cls=_SessionRecord,
        normalize_name=lambda name: name,
    )
    return SwitchHandlers(hooks), writes


def test_runtime_matrix_accepts_claude_api_ark() -> None:
    assert is_supported_runtime_combo("claude", "api", "ark")


def test_install_detects_ark_candidate_and_applies_default_model(tmp_path: Path) -> None:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    agent_admin_log = tmp_path / "agent_admin.jsonl"
    iterm_payload_log = tmp_path / "iterm_payload.jsonl"
    _write_ark_scan_script(root, api_key="sk-ark-menu")

    result = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install.sh"),
            "--project",
            "arkmenu50",
            "--template",
            "clawseat-creative",
            "--provider",
            "1",
        ],
        input="1\n",
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PATH": f"{root.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": f"{py_stubs}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PYTHON_BIN": sys.executable,
            "LOG_FILE": str(launcher_log),
            "TMUX_LOG_FILE": str(tmux_log),
            "AGENT_ADMIN_LOG": str(agent_admin_log),
            "ITERM_PAYLOAD_LOG": str(iterm_payload_log),
            "CLAWSEAT_TRUST_PROMPT_SLEEP_SECONDS": "0",
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ARK 火山方舟" in result.stdout

    provider_env = (
        home / ".agents" / "tasks" / "arkmenu50" / "memory-provider.env"
    ).read_text(encoding="utf-8")
    assert "ANTHROPIC_AUTH_TOKEN=sk-ark-menu" in provider_env
    assert "ANTHROPIC_BASE_URL=https://ark.cn-beijing.volces.com/api/coding" in provider_env
    assert "ANTHROPIC_MODEL=ark-code-latest" in provider_env

    records = _read_jsonl(launcher_log)
    assert [record["session"] for record in records] == ["arkmenu50-memory-claude"]
    for record in records:
        assert record["custom_api_key_present"] is True
        assert record["custom_base_url"] == "https://ark.cn-beijing.volces.com/api/coding"
        assert record["custom_model"] == "ark-code-latest"


def test_install_provider_ark_with_api_key_auto_fills_base_url_and_model(tmp_path: Path) -> None:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    agent_admin_log = tmp_path / "agent_admin.jsonl"
    iterm_payload_log = tmp_path / "iterm_payload.jsonl"

    result = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install.sh"),
            "--project",
            "arkforce50",
            "--template",
            "clawseat-creative",
            "--provider",
            "ark",
            "--api-key",
            "sk-ark-force",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PATH": f"{root.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": f"{py_stubs}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PYTHON_BIN": sys.executable,
            "LOG_FILE": str(launcher_log),
            "TMUX_LOG_FILE": str(tmux_log),
            "AGENT_ADMIN_LOG": str(agent_admin_log),
            "ITERM_PAYLOAD_LOG": str(iterm_payload_log),
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Using forced provider: ark" in result.stdout

    provider_env = (
        home / ".agents" / "tasks" / "arkforce50" / "memory-provider.env"
    ).read_text(encoding="utf-8")
    assert "ANTHROPIC_AUTH_TOKEN=sk-ark-force" in provider_env
    assert "ANTHROPIC_BASE_URL=https://ark.cn-beijing.volces.com/api/coding" in provider_env
    assert "ANTHROPIC_MODEL=ark-code-latest" in provider_env

    records = _read_jsonl(launcher_log)
    assert [record["session"] for record in records] == ["arkforce50-memory-claude"]
    for record in records:
        assert record["custom_api_key_present"] is True
        assert record["custom_base_url"] == "https://ark.cn-beijing.volces.com/api/coding"
        assert record["custom_model"] == "ark-code-latest"


def test_custom_env_payload_reads_ark_aliases_and_defaults(tmp_path: Path) -> None:
    secret = tmp_path / "ark.env"
    secret.write_text("ARK_API_KEY=sk-ark-payload\n", encoding="utf-8")
    session = SimpleNamespace(
        engineer_id="reviewer-1",
        tool="claude",
        auth_mode="api",
        provider="ark",
        secret_file=str(secret),
    )
    svc = aas.SessionService(MagicMock())

    payload = svc._custom_env_payload(session)

    assert payload == {
        "LAUNCHER_CUSTOM_API_KEY": "sk-ark-payload",
        "LAUNCHER_CUSTOM_BASE_URL": "https://ark.cn-beijing.volces.com/api/coding",
        "LAUNCHER_CUSTOM_MODEL": "ark-code-latest",
    }


def test_switch_harness_ark_seeds_per_engineer_secret_from_shared(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    shared = fake_home / ".agent-runtime" / "secrets" / "claude" / "ark.env"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text("ARK_API_KEY=sk-ark-shared\nARK_MODEL=ark-code-latest\n", encoding="utf-8")

    old_session = SimpleNamespace(
        engineer_id="planner",
        project="install",
        tool="claude",
        auth_mode="api",
        provider="minimax",
        identity="claude.api.minimax.install.planner",
        workspace=str(tmp_path / "workspace" / "planner"),
        runtime_dir=str(tmp_path / "runtime" / "planner"),
        session="install-planner-claude",
        bin_path="claude",
        monitor=True,
        legacy_sessions=[],
        launch_args=[],
        secret_file=str(fake_home / ".agents" / "secrets" / "claude" / "minimax" / "planner.env"),
        wrapper="",
    )
    handlers, writes = _make_switch_handlers(fake_home, old_session)

    result = handlers.session_switch_harness(
        SimpleNamespace(
            project="install",
            engineer="planner",
            tool="claude",
            mode="api",
            provider="ark",
            model="",
        )
    )

    assert result == 0
    target = fake_home / ".agents" / "secrets" / "claude" / "ark" / "planner.env"
    assert target.read_text(encoding="utf-8") == shared.read_text(encoding="utf-8")
    assert writes and writes[0].secret_file == str(target)


def test_switch_harness_deepseek_seeds_per_engineer_secret_from_shared(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    shared = fake_home / ".local" / "share" / "agent-launcher" / "secrets" / "claude" / "deepseek.env"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(
        "DEEPSEEK_API_KEY=sk-deepseek-shared\n"
        "DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic\n"
        "DEEPSEEK_MODEL=deepseek-v4-pro[1M]\n",
        encoding="utf-8",
    )

    old_session = SimpleNamespace(
        engineer_id="planner",
        project="install",
        tool="claude",
        auth_mode="api",
        provider="minimax",
        identity="claude.api.minimax.install.planner",
        workspace=str(tmp_path / "workspace" / "planner"),
        runtime_dir=str(tmp_path / "runtime" / "planner"),
        session="install-planner-claude",
        bin_path="claude",
        monitor=True,
        legacy_sessions=[],
        launch_args=[],
        secret_file=str(fake_home / ".agents" / "secrets" / "claude" / "minimax" / "planner.env"),
        wrapper="",
    )
    handlers, writes = _make_switch_handlers(fake_home, old_session)

    result = handlers.session_switch_harness(
        SimpleNamespace(
            project="install",
            engineer="planner",
            tool="claude",
            mode="api",
            provider="deepseek",
            model="",
        )
    )

    assert result == 0
    target = fake_home / ".agents" / "secrets" / "claude" / "deepseek" / "planner.env"
    assert target.read_text(encoding="utf-8") == shared.read_text(encoding="utf-8")
    assert writes and writes[0].secret_file == str(target)


def test_switch_harness_ark_does_not_overwrite_existing_per_engineer_secret(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    shared = fake_home / ".agent-runtime" / "secrets" / "claude" / "ark.env"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text("ARK_API_KEY=sk-ark-shared\n", encoding="utf-8")
    target = fake_home / ".agents" / "secrets" / "claude" / "ark" / "planner.env"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("ARK_API_KEY=sk-ark-existing\n", encoding="utf-8")

    old_session = SimpleNamespace(
        engineer_id="planner",
        project="install",
        tool="claude",
        auth_mode="api",
        provider="minimax",
        identity="claude.api.minimax.install.planner",
        workspace=str(tmp_path / "workspace" / "planner"),
        runtime_dir=str(tmp_path / "runtime" / "planner"),
        session="install-planner-claude",
        bin_path="claude",
        monitor=True,
        legacy_sessions=[],
        launch_args=[],
        secret_file=str(fake_home / ".agents" / "secrets" / "claude" / "minimax" / "planner.env"),
        wrapper="",
    )
    handlers, _ = _make_switch_handlers(fake_home, old_session)

    result = handlers.session_switch_harness(
        SimpleNamespace(
            project="install",
            engineer="planner",
            tool="claude",
            mode="api",
            provider="ark",
            model="",
        )
    )

    assert result == 0
    assert target.read_text(encoding="utf-8") == "ARK_API_KEY=sk-ark-existing\n"
