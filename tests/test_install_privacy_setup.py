from __future__ import annotations

import os
import stat
import subprocess
import sys
import importlib.util
from pathlib import Path

import pytest

_ISOLATION_HELPERS = Path(__file__).with_name("test_install_isolation.py")
_isolation_spec = importlib.util.spec_from_file_location("_h3_install_isolation", _ISOLATION_HELPERS)
assert _isolation_spec is not None
assert _isolation_spec.loader is not None
_isolation = importlib.util.module_from_spec(_isolation_spec)
_isolation_spec.loader.exec_module(_isolation)

_fake_install_root = _isolation._fake_install_root
_write_executable = _isolation._write_executable


def _prepare_h3_fake_root(tmp_path: Path) -> tuple[Path, Path, Path]:
    root, home, _launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    _write_executable(
        root / "core" / "scripts" / "privacy-check.sh",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    for skill in (
        "clawseat-memory",
        "clawseat-decision-escalation",
        "clawseat-koder",
        "clawseat-privacy",
        "clawseat-memory-reporting",
    ):
        skill_dir = root / "core" / "skills" / skill
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {skill}\n", encoding="utf-8")
    return root, home, py_stubs


def _run_install(
    root: Path,
    home: Path,
    py_stubs: Path,
    *,
    project: str = "h3privacy",
    repo_root: Path | None = None,
    memory_tool: str = "codex",
    load_all_skills: bool = False,
) -> subprocess.CompletedProcess[str]:
    args = [
        "bash",
        str(root / "scripts" / "install.sh"),
        "--project",
        project,
        "--template",
        "clawseat-creative",
        "--memory-tool",
        memory_tool,
        "--provider",
        "minimax",
    ]
    if repo_root is not None:
        args.extend(["--repo-root", str(repo_root)])
    if load_all_skills:
        args.append("--load-all-skills")
    return subprocess.run(
        args,
        input="\n",
        text=True,
        capture_output=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PATH": f"{root.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": f"{py_stubs}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PYTHON_BIN": sys.executable,
            "LOG_FILE": str(root.parent / "launcher.jsonl"),
            "TMUX_LOG_FILE": str(root.parent / "tmux.log"),
        },
        check=False,
    )


@pytest.fixture(scope="module")
def default_privacy_install(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root, home, py_stubs = _prepare_h3_fake_root(tmp_path_factory.mktemp("h3privacy-default"))
    result = _run_install(root, home, py_stubs)
    assert result.returncode == 0, result.stderr
    return root, home


def test_install_creates_privacy_template(default_privacy_install: tuple[Path, Path]) -> None:
    _root, home = default_privacy_install
    privacy = home / ".agents" / "memory" / "machine" / "privacy.md"
    assert privacy.is_file()
    text = privacy.read_text(encoding="utf-8")
    assert "# Privacy KB" in text
    assert "BLOCK: sk-" in text
    assert "BLOCK: ghp_" in text


def test_install_privacy_template_mode_0600(default_privacy_install: tuple[Path, Path]) -> None:
    _root, home = default_privacy_install
    privacy = home / ".agents" / "memory" / "machine" / "privacy.md"
    assert stat.S_IMODE(privacy.stat().st_mode) == 0o600


def test_install_does_not_overwrite_existing_privacy_template(tmp_path: Path) -> None:
    root, home, py_stubs = _prepare_h3_fake_root(tmp_path)
    privacy = home / ".agents" / "memory" / "machine" / "privacy.md"
    privacy.parent.mkdir(parents=True, exist_ok=True)
    privacy.write_text("BLOCK: keep-me\n", encoding="utf-8")
    privacy.chmod(0o600)
    result = _run_install(root, home, py_stubs)
    assert result.returncode == 0, result.stderr
    assert privacy.read_text(encoding="utf-8") == "BLOCK: keep-me\n"


def test_install_creates_deepseek_secret_template(default_privacy_install: tuple[Path, Path]) -> None:
    _root, home = default_privacy_install
    secret = home / ".agent-runtime" / "secrets" / "claude" / "deepseek.env"
    assert secret.is_file()
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert secret.read_text(encoding="utf-8") == (
        "ANTHROPIC_AUTH_TOKEN=<set-by-operator>\n"
        "ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic\n"
        "ANTHROPIC_MODEL=deepseek-v4-pro[1M]\n"
    )


def test_install_does_not_overwrite_existing_deepseek_secret(tmp_path: Path) -> None:
    root, home, py_stubs = _prepare_h3_fake_root(tmp_path)
    secret = home / ".agent-runtime" / "secrets" / "claude" / "deepseek.env"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("ANTHROPIC_AUTH_TOKEN=real-token\n", encoding="utf-8")
    secret.chmod(0o600)
    result = _run_install(root, home, py_stubs)
    assert result.returncode == 0, result.stderr
    assert secret.read_text(encoding="utf-8") == "ANTHROPIC_AUTH_TOKEN=real-token\n"
