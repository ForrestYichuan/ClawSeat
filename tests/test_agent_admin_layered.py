"""P1 tests: core/scripts/agent_admin_layered.py.

Covers the four new layered-model subcommands:
  agent-admin project koder-bind
  agent-admin machine memory show
  agent-admin project seat list
  agent-admin project validate

Tests sandbox ~/.agents and ~/.openclaw via monkeypatched
real_user_home + explicit home=/workspaces_root overrides so nothing
escapes tmp_path.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "core" / "scripts"))
sys.path.insert(0, str(_REPO / "core" / "lib"))

import agent_admin_layered as layered  # noqa: E402
import project_binding  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

V2_PROFILE = """\
version = 2
profile_name = "install"
project_name = "install"
template_name = "gstack-harness"
repo_root = "{CLAWSEAT_ROOT}"
workspace_root = "~/.agents/workspaces/install"
seats = ["ancestor", "planner", "builder", "reviewer"]
machine_services = ["memory"]
openclaw_frontstage_agent = "yu"

[seat_roles]
ancestor = "ancestor"
planner = "planner-dispatcher"
builder = "builder"
reviewer = "reviewer"

[seat_overrides.ancestor]
tool = "claude"
auth_mode = "oauth_token"
provider = "anthropic"

[seat_overrides.planner]
tool = "claude"
auth_mode = "oauth_token"
provider = "anthropic"

[seat_overrides.builder]
tool = "claude"
auth_mode = "oauth_token"
provider = "anthropic"
parallel_instances = 3

[seat_overrides.reviewer]
tool = "codex"
auth_mode = "api"
provider = "xcode-best"
parallel_instances = 1
"""

V3_MULTI_PROFILE = """\
project_name = "multi"
seats = ["memory", "front-planner", "front-builder"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
front = { seats = ["front-planner", "front-builder"], team_type = "subteam", planner_mode = "delivery", notify_policy = "queue_drained_only" }

[seat_roles]
memory = "project-memory"
front-planner = "planner"
front-builder = "builder"
"""


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Redirect real_user_home to tmp_path for both layered + project_binding."""
    monkeypatch.setattr(layered, "real_user_home", lambda: tmp_path)
    monkeypatch.setattr(project_binding, "real_user_home", lambda: tmp_path)
    (tmp_path / ".agents").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".openclaw").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _make_tenant_workspace(home: Path, tenant: str, project: str | None = None) -> Path:
    ws = home / ".openclaw" / f"workspace-{tenant}"
    ws.mkdir(parents=True, exist_ok=True)
    if project is not None:
        (ws / "WORKSPACE_CONTRACT.toml").write_text(
            f'project = "{project}"\n', encoding="utf-8",
        )
    return ws


def _write_profile(home: Path, project: str, body: str) -> Path:
    p = home / ".agents" / "profiles" / f"{project}-profile-dynamic.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _fake_machine_cfg(home: Path, tenants: dict[str, str]) -> SimpleNamespace:
    """Build a stand-in for MachineConfig that matches the attributes we use."""
    def _validate(cfg, name):
        if name not in cfg.tenants:
            return (False, f"unknown tenant {name!r}")
        ws = cfg.tenants[name].workspace
        ws = Path(os.path.expanduser(str(ws)))
        if not ws.is_dir():
            return (False, f"workspace missing: {ws}")
        if not (ws / "WORKSPACE_CONTRACT.toml").is_file():
            return (False, f"WORKSPACE_CONTRACT.toml missing in {ws}")
        return (True, "")
    tenant_objs = {
        name: SimpleNamespace(
            name=name,
            workspace=str(home / ".openclaw" / f"workspace-{name}"),
            description="",
        )
        for name in tenants
    }
    memory = SimpleNamespace(
        role="memory-oracle", tool="claude", auth_mode="api",
        provider="minimax", model="MiniMax-M2.7-highspeed",
        runtime_dir="~/.agents/runtime/memory",
        storage_root="~/.agents/memory",
        monitor=True, launch_args=[],
    )
    return SimpleNamespace(
        version=1,
        memory=memory,
        tenants=tenant_objs,
        source_path=home / ".clawseat" / "machine.toml",
    ), _validate


# ---------------------------------------------------------------------------
# project koder-bind
# ---------------------------------------------------------------------------

def test_koder_bind_happy_path(fake_home, monkeypatch):
    ws = _make_tenant_workspace(fake_home, "yu", project="(unbound)")
    cfg, validate = _fake_machine_cfg(fake_home, {"yu": "install"})
    monkeypatch.setattr(layered, "validate_tenant", validate)

    result = layered.do_koder_bind("install", "yu", machine_cfg=cfg)
    assert result["project"] == "install"
    assert result["tenant"] == "yu"
    # Workspace contract rewritten.
    contract_body = (ws / "WORKSPACE_CONTRACT.toml").read_text()
    assert 'project = "install"' in contract_body
    # Binding written with extras.
    binding = project_binding.load_binding("install")
    assert binding is not None
    assert binding.extras.get("openclaw_frontstage_tenant") == "yu"
    assert binding.extras.get("bound_via") == "agent-admin project koder-bind"


def test_koder_bind_rejects_invalid_tenant_name(fake_home):
    cfg, validate = _fake_machine_cfg(fake_home, {})
    with pytest.raises(layered.KoderBindError, match="invalid tenant name"):
        layered.do_koder_bind("install", "Invalid_Name!", machine_cfg=cfg)


def test_koder_bind_rejects_unknown_tenant(fake_home, monkeypatch):
    cfg, validate = _fake_machine_cfg(fake_home, {})
    monkeypatch.setattr(layered, "validate_tenant", validate)
    with pytest.raises(layered.KoderBindError, match="not registered"):
        layered.do_koder_bind("install", "unknown-tenant", machine_cfg=cfg)


def test_koder_bind_overwrites_previous_binding(fake_home, monkeypatch):
    _make_tenant_workspace(fake_home, "yu", project="install")
    _make_tenant_workspace(fake_home, "mor", project="install")
    cfg, validate = _fake_machine_cfg(fake_home, {"yu": "install", "mor": "install"})
    monkeypatch.setattr(layered, "validate_tenant", validate)

    layered.do_koder_bind("install", "yu", machine_cfg=cfg)
    result = layered.do_koder_bind("install", "mor", machine_cfg=cfg)
    assert result["previous_tenant"] == "yu"

    binding = project_binding.load_binding("install")
    assert binding.extras["openclaw_frontstage_tenant"] == "mor"


def test_koder_bind_creates_workspace_contract_when_missing(fake_home, monkeypatch):
    # No WORKSPACE_CONTRACT.toml yet.
    _make_tenant_workspace(fake_home, "yu", project=None)
    # validate_tenant would reject without the contract; stub to pass for setup.
    cfg = SimpleNamespace(
        tenants={"yu": SimpleNamespace(
            name="yu",
            workspace=str(fake_home / ".openclaw" / "workspace-yu"),
            description="",
        )},
    )
    monkeypatch.setattr(layered, "validate_tenant", lambda *_: (True, ""))
    layered.do_koder_bind("install", "yu", machine_cfg=cfg)
    contract = fake_home / ".openclaw" / "workspace-yu" / "WORKSPACE_CONTRACT.toml"
    assert contract.is_file()
    assert 'project = "install"' in contract.read_text()


def test_cmd_project_koder_bind_rc(fake_home, monkeypatch, capsys):
    _make_tenant_workspace(fake_home, "yu", project="(unbound)")
    cfg, validate = _fake_machine_cfg(fake_home, {"yu": "install"})
    monkeypatch.setattr(layered, "load_machine", lambda *_a, **_kw: cfg)
    monkeypatch.setattr(layered, "validate_tenant", validate)
    monkeypatch.setattr(layered, "_MACHINE_AVAILABLE", True)
    rc = layered.cmd_project_koder_bind(argparse.Namespace(project="install", tenant="yu"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "koder bound" in out or "koder rebound" in out


def test_cmd_project_koder_bind_error_surfaces_rc_2(fake_home, monkeypatch, capsys):
    cfg, validate = _fake_machine_cfg(fake_home, {})
    monkeypatch.setattr(layered, "load_machine", lambda *_a, **_kw: cfg)
    monkeypatch.setattr(layered, "validate_tenant", validate)
    monkeypatch.setattr(layered, "_MACHINE_AVAILABLE", True)
    rc = layered.cmd_project_koder_bind(argparse.Namespace(project="install", tenant="ghost"))
    assert rc == 2
    assert "not registered" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# machine memory show
# ---------------------------------------------------------------------------

def test_describe_memory_lines(fake_home):
    cfg, _ = _fake_machine_cfg(fake_home, {})
    lines = layered.describe_memory(cfg=cfg)
    joined = "\n".join(lines)
    assert "role         = memory-oracle" in joined
    assert "tool         = claude" in joined
    assert "provider     = minimax" in joined
    assert "runtime: tmux session 'install-memory-claude'" in joined


def test_cmd_machine_memory_show_requires_machine_layer(fake_home, monkeypatch, capsys):
    monkeypatch.setattr(layered, "_MACHINE_AVAILABLE", False)
    rc = layered.cmd_machine_memory_show(argparse.Namespace())
    assert rc == 2
    assert "not importable" in capsys.readouterr().err


def test_cmd_machine_memory_show_prints(fake_home, monkeypatch, capsys):
    cfg, _ = _fake_machine_cfg(fake_home, {})
    monkeypatch.setattr(layered, "_MACHINE_AVAILABLE", True)
    monkeypatch.setattr(layered, "load_machine", lambda *_a, **_kw: cfg)
    rc = layered.cmd_machine_memory_show(argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "memory-oracle" in out


# ---------------------------------------------------------------------------
# project seat list (parallel_instances expansion)
# ---------------------------------------------------------------------------

def test_expand_parallel_seats_singleton_keeps_name():
    out = layered.expand_parallel_seats(
        ["ancestor", "planner"],
        {"ancestor": {}, "planner": {"parallel_instances": 1}},
    )
    assert out == ["ancestor", "planner"]


def test_expand_parallel_seats_fans_out_when_n_gt_1():
    out = layered.expand_parallel_seats(
        ["ancestor", "planner", "builder"],
        {"builder": {"parallel_instances": 3}},
    )
    assert out == ["ancestor", "planner", "builder_1", "builder_2", "builder_3"]


def test_expand_parallel_seats_tolerates_non_numeric():
    out = layered.expand_parallel_seats(
        ["builder"],
        {"builder": {"parallel_instances": "not-a-number"}},
    )
    assert out == ["builder"]


def test_cmd_project_seat_list_prints_expanded(fake_home, capsys):
    _write_profile(fake_home, "install", V2_PROFILE)
    rc = layered.cmd_project_seat_list(argparse.Namespace(project="install"))
    assert rc == 0
    out_lines = capsys.readouterr().out.splitlines()
    assert out_lines == [
        "ancestor", "planner",
        "builder_1", "builder_2", "builder_3",
        "reviewer",
    ]


def test_cmd_project_seat_list_missing_profile(fake_home, capsys):
    rc = layered.cmd_project_seat_list(argparse.Namespace(project="nope"))
    assert rc == 1
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# project validate
# ---------------------------------------------------------------------------

def test_cmd_project_validate_requires_validator(fake_home, monkeypatch, capsys):
    _write_profile(fake_home, "install", V2_PROFILE)
    monkeypatch.setattr(layered, "_VALIDATOR_AVAILABLE", False)
    rc = layered.cmd_project_validate(argparse.Namespace(project="install"))
    assert rc == 2
    assert "not importable" in capsys.readouterr().err


def test_cmd_project_validate_ok(fake_home, monkeypatch, capsys):
    _write_profile(fake_home, "install", V2_PROFILE)
    monkeypatch.setattr(layered, "_VALIDATOR_AVAILABLE", True)
    monkeypatch.setattr(
        layered, "validate_profile_v2",
        lambda path: SimpleNamespace(ok=True, errors=[], warnings=[]),
    )
    rc = layered.cmd_project_validate(argparse.Namespace(project="install"))
    assert rc == 0
    assert "ok:" in capsys.readouterr().out


def test_cmd_project_validate_accepts_v3_multi_profile(fake_home, capsys):
    _write_profile(fake_home, "multi", V3_MULTI_PROFILE)

    rc = layered.cmd_project_validate(argparse.Namespace(project="multi"))

    assert rc == 0
    out = capsys.readouterr().out
    assert "v3 multi" in out
    assert "front" in out


def test_cmd_project_validate_errors(fake_home, monkeypatch, capsys):
    _write_profile(fake_home, "install", V2_PROFILE)
    monkeypatch.setattr(layered, "_VALIDATOR_AVAILABLE", True)
    monkeypatch.setattr(
        layered, "validate_profile_v2",
        lambda path: SimpleNamespace(
            ok=False,
            errors=["seats missing 'ancestor'", "parallel_instances > 10"],
            warnings=["designer missing (§10 decision)"],
        ),
    )
    rc = layered.cmd_project_validate(argparse.Namespace(project="install"))
    assert rc == 1
    captured = capsys.readouterr()
    assert "designer missing" in captured.out
    assert "seats missing" in captured.err
    assert "parallel_instances > 10" in captured.err
