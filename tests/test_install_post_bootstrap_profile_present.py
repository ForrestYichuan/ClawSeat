from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_INSTALL = _REPO / "scripts" / "install.sh"
_HELPERS_PATH = Path(__file__).with_name("test_install_isolation.py")
_HELPERS_SPEC = importlib.util.spec_from_file_location("test_install_isolation_helpers_ll", _HELPERS_PATH)
assert _HELPERS_SPEC is not None and _HELPERS_SPEC.loader is not None
_HELPERS = importlib.util.module_from_spec(_HELPERS_SPEC)
_HELPERS_SPEC.loader.exec_module(_HELPERS)

_fake_install_root = _HELPERS._fake_install_root
_write_executable = _HELPERS._write_executable


def test_install_dry_run_skips_profile_presence_assertion(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = subprocess.run(
        ["bash", str(_INSTALL), "--project", "ll-dry", "--template", "clawseat-solo", "--dry-run"],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "CLAWSEAT_REAL_HOME": str(home), "PYTHON_BIN": sys.executable},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "PROFILE_RENDER_MISSING" not in result.stderr
    assert not (home / ".agents" / "profiles" / "ll-dry-profile-dynamic.toml").exists()


def _run_fake_install(root: Path, home: Path, launcher_log: Path, tmux_log: Path, py_stubs: Path, project: str):
    return subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install.sh"),
            "--project",
            project,
            "--template",
            "clawseat-engineering",
            "--provider",
            "1",
        ],
        input="\n",
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
            "CLAWSEAT_TRUST_PROMPT_SLEEP_SECONDS": "0",
        },
        check=False,
    )


def test_install_real_run_leaves_bootstrap_profile_present(tmp_path: Path) -> None:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)

    result = _run_fake_install(root, home, launcher_log, tmux_log, py_stubs, "ll-real")

    assert result.returncode == 0, result.stderr
    profile = home / ".agents" / "profiles" / "ll-real-profile-dynamic.toml"
    text = profile.read_text(encoding="utf-8")
    assert 'profile_name = "ll-real"' in text
    assert 'seats = ["memory", "planner", "builder", "patrol", "designer"]' in text


def test_install_fails_fast_when_bootstrap_does_not_render_profile(tmp_path: Path) -> None:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    _write_executable(
        root / "core" / "scripts" / "agent_admin.py",
        """#!/usr/bin/env python3
raise SystemExit(0)
""",
    )

    result = _run_fake_install(root, home, launcher_log, tmux_log, py_stubs, "ll-missing")

    assert result.returncode == 31
    assert "PROFILE_RENDER_MISSING" in result.stderr
    assert "profile-dynamic.toml is required by dispatch_task.py" in result.stderr
