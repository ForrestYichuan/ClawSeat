from pathlib import Path
from types import SimpleNamespace
import os
import sys

import pytest


_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from core.engine import instantiate_seat
from core.scripts.agent_admin_config import CODEX_API_PROVIDER_CONFIGS, DEFAULT_TOOL_ARGS, TOOL_BINARIES
from core.scripts.agent_admin_info import InfoHandlers, InfoHooks
from core.scripts.agent_admin_runtime import write_codex_api_config
from core.scripts.agent_admin_store import StoreHandlers, StoreHooks
from core.scripts.agent_admin_switch import SwitchHandlers, SwitchHooks


def _store_handlers(tmp_path: Path) -> StoreHandlers:
    return StoreHandlers(
        StoreHooks(
            error_cls=RuntimeError,
            project_cls=SimpleNamespace,
            engineer_cls=SimpleNamespace,
            session_record_cls=SimpleNamespace,
            projects_root=tmp_path / "projects",
            engineers_root=tmp_path / "engineers",
            sessions_root=tmp_path / "sessions",
            workspaces_root=tmp_path / "workspaces",
            current_project_path=tmp_path / "state" / "current_project",
            templates_root=tmp_path / "templates",
            repo_templates_root=tmp_path / "repo-templates",
            tool_binaries=TOOL_BINARIES,
            default_tool_args=DEFAULT_TOOL_ARGS,
            normalize_name=lambda value: value,
            ensure_dir=lambda path: path.mkdir(parents=True, exist_ok=True),
            write_text=lambda *args, **kwargs: None,
            load_toml=lambda path: {},
            q=lambda value: repr(value),
            q_array=lambda values: repr(values),
            identity_name=lambda tool, mode, provider, engineer_id, project: (
                f"{tool}.{mode}.{provider}.{project}.{engineer_id}"
            ),
            runtime_dir_for_identity=lambda tool, mode, identity: tmp_path / "runtime" / identity,
            secret_file_for=lambda tool, provider, engineer_id: tmp_path / "secrets" / tool / provider / f"{engineer_id}.env",
            session_name_for=lambda project, engineer_id, tool: f"{project}-{engineer_id}-{tool}",
        )
    )


def _switch_handlers(
    tmp_path: Path,
    writes: list[tuple[str, list[str], str]] | None = None,
    applies: list[tuple[str, str, str]] | None = None,
    old_session: SimpleNamespace | None = None,
) -> SwitchHandlers:
    def record_write(session):
        if writes is not None:
            writes.append((session.engineer_id, list(session.launch_args), session.bin_path))

    def record_apply_template(session, project):
        if applies is not None:
            applies.append((session.engineer_id, session.tool, project.name))

    return SwitchHandlers(
        SwitchHooks(
            error_cls=RuntimeError,
            legacy_secrets_root=tmp_path / "legacy-secrets",
            tool_binaries=TOOL_BINARIES,
            default_tool_args=DEFAULT_TOOL_ARGS,
            identity_name=lambda tool, mode, provider, engineer_id, project: (
                f"{tool}.{mode}.{provider}.{project}.{engineer_id}"
            ),
            runtime_dir_for_identity=lambda tool, mode, identity: tmp_path / "runtime" / identity,
            secret_file_for=lambda tool, provider, engineer_id: tmp_path / "secrets" / tool / provider / f"{engineer_id}.env",
            session_name_for=lambda project, engineer_id, tool: f"{project}-{engineer_id}-{tool}",
            ensure_dir=lambda path: path.mkdir(parents=True, exist_ok=True),
            ensure_secret_permissions=lambda path: None,
            write_env_file=lambda *args, **kwargs: None,
            parse_env_file=lambda path: {},
            load_project=lambda name: SimpleNamespace(name=name),
            load_project_or_current=lambda name: SimpleNamespace(name=name or "install"),
            load_session=lambda project, engineer: old_session,
            write_session=record_write,
            apply_template=record_apply_template,
            session_stop_engineer=lambda session: None,
            session_record_cls=SimpleNamespace,
            normalize_name=lambda value: value,
        )
    )


def _info_handlers(session) -> InfoHandlers:
    return InfoHandlers(
        InfoHooks(
            error_cls=RuntimeError,
            load_projects=lambda: {},
            load_project_or_current=lambda project: None,
            load_project=lambda project: None,
            load_engineers=lambda: {},
            load_sessions=lambda: {},
            project_template_context=lambda project: None,
            resolve_engineer=lambda name, engineers=None: None,
            resolve_engineer_session=lambda engineer, project_name=None: session,
            resolve_session=lambda name, project_name=None, prefer_current_project=True: name,
            display_label=lambda engineer, fallback: fallback,
            session_status=lambda current_session: "running",
            build_runtime=lambda current_session: (current_session.bin_path, {"HOME": "/tmp/home"}),
            default_launch_args=lambda current_session: list(DEFAULT_TOOL_ARGS.get(current_session.tool, [])),
        )
    )


def test_create_session_record_persists_default_codex_launch_args(tmp_path):
    handlers = _store_handlers(tmp_path)
    project = SimpleNamespace(name="install")

    session = handlers.create_session_record(
        engineer_id="reviewer-1",
        project=project,
        tool="codex",
        auth_mode="api",
        provider="xcode-best",
    )

    assert session.launch_args == DEFAULT_TOOL_ARGS["codex"]


def test_switch_harness_rewrites_launch_args_for_new_tool(tmp_path):
    handlers = _switch_handlers(tmp_path)
    old_session = SimpleNamespace(
        engineer_id="patrol-1",
        project="install",
        tool="claude",
        auth_mode="api",
        provider="minimax",
        identity="claude.api.minimax.install.patrol-1",
        workspace=str(tmp_path / "workspaces" / "install" / "patrol-1"),
        runtime_dir=str(tmp_path / "runtime" / "claude"),
        session="install-patrol-1-claude",
        bin_path=TOOL_BINARIES["claude"],
        monitor=True,
        legacy_sessions=[],
        launch_args=["--dangerously-skip-permissions"],
        secret_file=str(tmp_path / "old.env"),
        wrapper="",
    )

    new_session = handlers.build_switched_session(old_session, SimpleNamespace(name="install"), "codex", "api", "xcode-best")

    assert new_session.launch_args == DEFAULT_TOOL_ARGS["codex"]
    assert new_session.bin_path == TOOL_BINARIES["codex"]


@pytest.mark.parametrize(
    ("tool", "mode", "provider"),
    [
        ("claude", "oauth", "anthropic"),
        ("codex", "oauth", "openai"),
        ("gemini", "oauth", "google"),
    ],
)
def test_switch_harness_rerenders_workspace_for_target_tool(tmp_path, tool, mode, provider):
    applies: list[tuple[str, str, str]] = []
    old_session = SimpleNamespace(
        engineer_id="memory",
        project="install",
        tool="claude",
        auth_mode="api",
        provider="minimax",
        identity="claude.api.minimax.install.memory",
        workspace=str(tmp_path / "workspaces" / "install" / "memory"),
        runtime_dir=str(tmp_path / "runtime" / "claude"),
        session="install-memory-claude",
        bin_path=TOOL_BINARIES["claude"],
        monitor=True,
        legacy_sessions=[],
        launch_args=["--dangerously-skip-permissions"],
        secret_file=str(tmp_path / "old.env"),
        wrapper="",
    )
    handlers = _switch_handlers(tmp_path, applies=applies, old_session=old_session)

    result = handlers.session_switch_harness(
        SimpleNamespace(
            project="install",
            engineer="memory",
            tool=tool,
            mode=mode,
            provider=provider,
            model="",
        )
    )

    assert result == 0
    assert applies == [("memory", tool, "install")]


def test_reconcile_session_runtime_repairs_stale_codex_launch_args(tmp_path):
    writes: list[tuple[str, list[str], str]] = []
    handlers = _switch_handlers(tmp_path, writes=writes)
    session = SimpleNamespace(
        engineer_id="reviewer-1",
        project="install",
        tool="codex",
        auth_mode="api",
        provider="xcode-best",
        identity="stale.identity",
        workspace=str(tmp_path / "workspaces" / "install" / "reviewer-1"),
        runtime_dir=str(tmp_path / "runtime" / "stale"),
        session="install-reviewer-1-codex",
        bin_path=TOOL_BINARIES["claude"],
        monitor=True,
        legacy_sessions=[],
        launch_args=["--dangerously-skip-permissions"],
        secret_file=str(tmp_path / "secret.env"),
        wrapper="",
    )

    reconciled = handlers.reconcile_session_runtime(session)

    assert reconciled.launch_args == DEFAULT_TOOL_ARGS["codex"]
    assert reconciled.bin_path == TOOL_BINARIES["codex"]
    assert writes == [("reviewer-1", DEFAULT_TOOL_ARGS["codex"], TOOL_BINARIES["codex"])]


def test_run_engineer_uses_default_codex_launch_args_when_session_empty(monkeypatch, tmp_path):
    session = SimpleNamespace(
        engineer_id="reviewer-1",
        project="install",
        tool="codex",
        auth_mode="api",
        provider="xcode-best",
        identity="codex.api.xcode-best.install.reviewer-1",
        workspace=str(tmp_path / "workspaces" / "install" / "reviewer-1"),
        runtime_dir=str(tmp_path / "runtime" / "reviewer-1"),
        session="install-reviewer-1-codex",
        bin_path=TOOL_BINARIES["codex"],
        monitor=True,
        legacy_sessions=[],
        launch_args=[],
        secret_file=str(tmp_path / "secret.env"),
        wrapper="",
    )
    handlers = _info_handlers(session)
    captured: dict[str, object] = {}

    def fake_execvpe(binary, cmd, env):
        captured["binary"] = binary
        captured["cmd"] = cmd
        captured["env"] = env
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(os, "execvpe", fake_execvpe)

    with pytest.raises(RuntimeError, match="stop after capture"):
        handlers.run_engineer(SimpleNamespace(engineer="reviewer-1", project="install", cmd=[]))

    assert captured["binary"] == TOOL_BINARIES["codex"]
    assert captured["cmd"] == [TOOL_BINARIES["codex"], *DEFAULT_TOOL_ARGS["codex"]]


def test_session_effective_launch_reports_default_codex_args(capsys, tmp_path):
    session = SimpleNamespace(
        engineer_id="reviewer-1",
        project="install",
        tool="codex",
        auth_mode="api",
        provider="xcode-best",
        identity="codex.api.xcode-best.install.reviewer-1",
        workspace=str(tmp_path / "workspaces" / "install" / "reviewer-1"),
        runtime_dir=str(tmp_path / "runtime" / "reviewer-1"),
        session="install-reviewer-1-codex",
        bin_path=TOOL_BINARIES["codex"],
        monitor=True,
        legacy_sessions=[],
        launch_args=[],
        secret_file=str(tmp_path / "secret.env"),
        wrapper="",
    )
    handlers = _info_handlers(session)

    handlers.session_effective_launch(SimpleNamespace(engineer="reviewer-1", project="install", cmd=[]))

    out = capsys.readouterr().out
    assert "launch_args_source = default_tool_args" in out
    assert "--dangerously-bypass-approvals-and-sandbox" in out


def test_write_codex_api_config_does_not_hide_full_access_warning(tmp_path):
    captured: dict[str, str] = {}

    def fake_write_text(path: Path, text: str, mode=None):
        captured["path"] = str(path)
        captured["text"] = text

    write_codex_api_config(
        SimpleNamespace(provider="xcode-best"),
        tmp_path / "codex-home",
        tmp_path,
        CODEX_API_PROVIDER_CONFIGS,
        fake_write_text,
    )

    assert captured["path"].endswith("config.toml")
    assert "hide_full_access_warning" not in captured["text"]


def test_instantiate_seat_renderers_keep_launch_args_in_sync(tmp_path):
    template = SimpleNamespace(
        tool="codex",
        auth_mode="api",
        provider="xcode-best",
        monitor=True,
        template_id="reviewer",
        role="reviewer",
        instance_mode="singleton",
    )
    instance = SimpleNamespace(
        project_name="install",
        instance_id="reviewer-1",
        template=template,
        runtime_identity="codex.api.xcode-best.install.reviewer-1",
        workspace=tmp_path / "workspace",
        runtime_dir=tmp_path / "runtime",
        session_name="install-reviewer-1-codex",
        session_path=tmp_path / "session.toml",
        contract_path=tmp_path / "WORKSPACE_CONTRACT.toml",
        repo_root=tmp_path,
        tasks_root=tmp_path / "tasks",
        bin_path=TOOL_BINARIES["codex"],
        launch_args=list(DEFAULT_TOOL_ARGS["codex"]),
        secret_path=tmp_path / "secret.env",
    )

    session_text = instantiate_seat.render_session_record(instance)
    tmux_text = instantiate_seat.render_tmux_config(instance)

    assert 'launch_args = ["--dangerously-bypass-approvals-and-sandbox"]' in session_text
    assert 'launch_args = ["--dangerously-bypass-approvals-and-sandbox"]' in tmux_text
    assert "default_tool_args" not in tmux_text
