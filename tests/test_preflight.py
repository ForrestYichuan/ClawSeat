"""Tests for bootstrap-aware preflight phase routing and CLI exit codes."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from core import preflight as pf


def _item(name: str, status: pf.PreflightStatus = pf.PreflightStatus.PASS, message: str = "ok"):
    return pf.PreflightItem(name=name, status=status, message=message)


def _passthrough_check(name: str):
    return lambda *args, **kwargs: _item(name)


def test_preflight_default_runtime_checks_runtime_only_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "_check_clawseat_root", _passthrough_check("CLAWSEAT_ROOT"))
    monkeypatch.setattr(pf, "_check_python", _passthrough_check("python3"))
    monkeypatch.setattr(pf, "_check_tomllib", _passthrough_check("tomllib"))
    monkeypatch.setattr(
        pf,
        "_check_tmux",
        lambda: (_item("tmux"), _item("tmux_server")),
    )
    monkeypatch.setattr(pf, "_resolve_clawseat_root_from_env", lambda: tmp_path)
    monkeypatch.setattr(pf, "_check_repo_integrity", lambda root: _item("repo_integrity"))
    monkeypatch.setattr(pf, "_check_dynamic_profile", _passthrough_check("dynamic_profile"))
    monkeypatch.setattr(pf, "_check_session_binding_dir", _passthrough_check("session_binding_dir"))
    monkeypatch.setattr(pf, "_check_skills", lambda active_roles=None: [_item("skill_demo")])
    monkeypatch.setattr(
        pf,
        "_check_optional_cli",
        lambda binary, label, install_hint: _item(binary),
    )
    monkeypatch.setattr(pf, "_check_iterm2", lambda: pytest.fail("bootstrap-only check should not run"))
    monkeypatch.setattr(
        pf,
        "_check_iterm2_python_module",
        lambda: pytest.fail("bootstrap-only check should not run"),
    )
    monkeypatch.setattr(
        pf,
        "_check_claude_required",
        lambda: pytest.fail("bootstrap-only check should not run"),
    )

    result = pf.preflight_check("demo")

    names = [item.name for item in result.items]
    assert names[:5] == ["CLAWSEAT_ROOT", "python3", "tomllib", "tmux", "tmux_server"]
    assert "dynamic_profile" in names
    assert "session_binding_dir" in names
    assert "skill_demo" in names
    assert "claude" in names
    assert "codex" in names
    assert "lark-cli" in names
    assert "iterm2" not in names
    assert "iterm2_python" not in names
    assert "claude_required" not in names


def test_preflight_bootstrap_skips_runtime_only_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "_check_clawseat_root", _passthrough_check("CLAWSEAT_ROOT"))
    monkeypatch.setattr(pf, "_check_python", _passthrough_check("python3"))
    monkeypatch.setattr(pf, "_check_tomllib", _passthrough_check("tomllib"))
    monkeypatch.setattr(
        pf,
        "_check_tmux",
        lambda: (_item("tmux"), _item("tmux_server")),
    )
    monkeypatch.setattr(pf, "_resolve_clawseat_root_from_env", lambda: tmp_path)
    monkeypatch.setattr(pf, "_check_repo_integrity", lambda root: _item("repo_integrity"))
    monkeypatch.setattr(
        pf,
        "_check_dynamic_profile",
        lambda *args, **kwargs: pytest.fail("runtime-only check should not run"),
    )
    monkeypatch.setattr(
        pf,
        "_check_session_binding_dir",
        lambda *args, **kwargs: pytest.fail("runtime-only check should not run"),
    )
    monkeypatch.setattr(
        pf,
        "_check_skills",
        lambda active_roles=None: pytest.fail("runtime-only check should not run"),
    )
    monkeypatch.setattr(
        pf,
        "_check_optional_cli",
        lambda binary, label, install_hint: _item(binary),
    )
    monkeypatch.setattr(pf, "_check_iterm2", _passthrough_check("iterm2"))
    monkeypatch.setattr(pf, "_check_iterm2_python_module", _passthrough_check("iterm2_python"))
    monkeypatch.setattr(pf, "_check_claude_required", _passthrough_check("claude_required"))

    result = pf.preflight_check("demo", phase="bootstrap")

    names = [item.name for item in result.items]
    assert names[:5] == ["CLAWSEAT_ROOT", "python3", "tomllib", "tmux", "tmux_server"]
    assert "iterm2" in names
    assert "iterm2_python" in names
    assert "claude_required" in names
    assert "dynamic_profile" not in names
    assert "session_binding_dir" not in names
    assert "skill_demo" not in names
    assert "claude" not in names
    assert "codex" in names
    assert "lark-cli" in names


def test_preflight_bootstrap_no_window_skips_native_window_checks(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "_check_clawseat_root", _passthrough_check("CLAWSEAT_ROOT"))
    monkeypatch.setattr(pf, "_check_python", _passthrough_check("python3"))
    monkeypatch.setattr(pf, "_check_tomllib", _passthrough_check("tomllib"))
    monkeypatch.setattr(
        pf,
        "_check_tmux",
        lambda: (_item("tmux"), _item("tmux_server")),
    )
    monkeypatch.setattr(pf, "_resolve_clawseat_root_from_env", lambda: tmp_path)
    monkeypatch.setattr(pf, "_check_repo_integrity", lambda root: _item("repo_integrity"))
    monkeypatch.setattr(pf, "_check_iterm2", lambda: pytest.fail("--no-window should skip iTerm2 app check"))
    monkeypatch.setattr(
        pf,
        "_check_iterm2_python_module",
        lambda: pytest.fail("--no-window should skip iTerm2 Python module check"),
    )
    monkeypatch.setattr(pf, "_check_claude_required", _passthrough_check("claude_required"))
    monkeypatch.setattr(
        pf,
        "_check_optional_cli",
        lambda binary, label, install_hint: _item(binary),
    )

    result = pf.preflight_check("demo", phase="bootstrap", skip_native_windows=True)

    by_name = {item.name: item for item in result.items}
    assert by_name["iterm2"].status == pf.PreflightStatus.PASS
    assert by_name["iterm2"].message == "native iTerm window checks skipped (--no-window)"
    assert by_name["iterm2_python"].status == pf.PreflightStatus.PASS
    assert by_name["iterm2_python"].message == "iTerm2 Python module check skipped (--no-window)"
    assert "claude_required" in by_name


def test_preflight_rejects_invalid_phase(monkeypatch):
    with pytest.raises(ValueError, match="unsupported preflight phase"):
        pf.preflight_check("demo", phase="invalid")


def test_iterm2_python_module_uses_real_home_in_subprocess_env(monkeypatch, tmp_path):
    sandbox_home = str(tmp_path / ".agent-runtime" / "identities" / "test" / "home")
    real_home = tmp_path / "real-user-home"

    monkeypatch.setattr(pf.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("HOME", sandbox_home)
    monkeypatch.setattr(pf, "real_user_home", lambda: real_home)

    recorded: dict[str, object] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, capture_output, text, timeout, check, env):
        recorded["cmd"] = cmd
        recorded["env"] = env
        return _Result()

    monkeypatch.setattr(pf.subprocess, "run", _fake_run)

    item = pf._check_iterm2_python_module()

    assert item.status == pf.PreflightStatus.PASS
    assert recorded["cmd"] == [sys.executable, "-c", "import iterm2"]
    assert isinstance(recorded["env"], dict)
    assert recorded["env"]["HOME"] == str(real_home)
    assert recorded["env"]["HOME"] != sandbox_home
    assert recorded["env"]["PATH"] == os.environ["PATH"]


def test_check_python_uses_current_interpreter_before_bare_python3(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}
    chosen_python = tmp_path / "python3.12"
    chosen_python.write_text("", encoding="utf-8")

    class _Result:
        stdout = "(3, 12)\n"

    monkeypatch.setattr(pf.sys, "executable", str(chosen_python))
    monkeypatch.setattr(
        pf.shutil,
        "which",
        lambda name: "/usr/bin/python3" if name == "python3" else None,
    )

    def _fake_run(cmd, text, capture_output, check):
        recorded["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(pf.subprocess, "run", _fake_run)

    item = pf._check_python()

    assert item.status == pf.PreflightStatus.PASS
    assert recorded["cmd"] == [str(chosen_python), "-c", "import sys; print(sys.version_info[:2])"]
    assert "3.12" in item.message


def test_check_python_falls_back_to_python_bin_env_when_sys_executable_missing(monkeypatch):
    recorded: dict[str, object] = {}

    class _Result:
        stdout = "(3, 11)\n"

    monkeypatch.setattr(pf.sys, "executable", "")
    monkeypatch.setenv("PYTHON_BIN", "/opt/homebrew/bin/python3.11")
    monkeypatch.setattr(pf.shutil, "which", lambda name: None)

    def _fake_run(cmd, text, capture_output, check):
        recorded["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(pf.subprocess, "run", _fake_run)

    item = pf._check_python()

    assert item.status == pf.PreflightStatus.PASS
    assert recorded["cmd"] == ["/opt/homebrew/bin/python3.11", "-c", "import sys; print(sys.version_info[:2])"]
    assert "/opt/homebrew/bin/python3.11" in item.message


def test_main_returns_nonzero_when_hard_blocked(monkeypatch, capsys):
    result = pf.PreflightResult(
        all_pass=False,
        has_hard_blocked=True,
        has_retryable=False,
        items=[_item("blocked", pf.PreflightStatus.HARD_BLOCKED, "boom")],
        hard_blocked_items=[_item("blocked", pf.PreflightStatus.HARD_BLOCKED, "boom")],
    )
    observed: dict[str, object] = {}

    def _fake_preflight(project, phase="runtime", *, skip_native_windows=False):
        observed["project"] = project
        observed["phase"] = phase
        observed["skip_native_windows"] = skip_native_windows
        return result

    monkeypatch.setattr(pf, "preflight_check", _fake_preflight)
    monkeypatch.setattr(
        sys,
        "argv",
        ["preflight.py", "--project", "demo", "--phase", "bootstrap", "--no-window"],
    )

    rc = pf.main()
    out = capsys.readouterr().out

    assert rc == 2
    assert observed == {"project": "demo", "phase": "bootstrap", "skip_native_windows": True}
    assert "preflight_check: FAIL [demo]" in out
    assert "[✗] blocked: boom" in out
