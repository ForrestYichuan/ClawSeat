from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


HELPERS_PATH = Path(__file__).with_name("test_install_isolation.py")
HELPERS_SPEC = importlib.util.spec_from_file_location("test_install_migrate_helpers", HELPERS_PATH)
assert HELPERS_SPEC is not None and HELPERS_SPEC.loader is not None
HELPERS = importlib.util.module_from_spec(HELPERS_SPEC)
HELPERS_SPEC.loader.exec_module(HELPERS)

_REPO = Path(__file__).resolve().parents[1]
_fake_install_root = HELPERS._fake_install_root


def _run_existing_project(
    tmp_path: Path,
    project_toml: str,
    *,
    template: str = "clawseat-engineering",
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    project_dir = home / ".agents" / "projects" / "migratecase"
    project_dir.mkdir(parents=True)
    project_path = project_dir / "project.toml"
    project_path.write_text(project_toml, encoding="utf-8")
    agent_admin_log = tmp_path / "agent-admin.jsonl"
    result = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install.sh"),
            "--project",
            "migratecase",
            "--template",
            template,
            "--provider",
            "minimax",
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
            "AGENT_ADMIN_LOG": str(agent_admin_log),
            "CLAWSEAT_QA_PROFILE_MIGRATE": "y",
        },
        check=False,
    )
    return result, project_path, agent_admin_log


def test_migrate_template_driven_fills_missing_overrides(tmp_path: Path) -> None:
    result, project_path, _agent_admin_log = _run_existing_project(
        tmp_path,
        "\n".join(
            [
                'name = "migratecase"',
                'template_name = "clawseat-engineering"',
                'engineers = ["memory", "planner", "builder"]',
                'monitor_engineers = ["planner", "builder"]',
                "monitor_max_panes = 3",
                "",
                "[seat_overrides.builder]",
                'provider = "operator-keep"',
                "",
            ]
        ),
    )
    assert result.returncode == 0, result.stderr
    data = tomllib.loads(project_path.read_text(encoding="utf-8"))
    assert data["engineers"] == ["memory", "planner", "builder", "reviewer", "patrol"]
    assert data["monitor_max_panes"] == 5
    assert data["seat_overrides"]["planner"]["provider"] == "deepseek"
    assert data["seat_overrides"]["patrol"]["model"] == "MiniMax-M2.7-highspeed"
    assert data["seat_overrides"]["builder"]["provider"] == "operator-keep"


def test_migrate_template_driven_skips_complete_profile(tmp_path: Path) -> None:
    complete = "\n".join(
        [
            'name = "migratecase"',
            'template_name = "clawseat-engineering"',
            'engineers = ["memory", "planner", "builder", "reviewer", "patrol", "designer"]',
            'monitor_engineers = ["memory", "planner", "builder", "reviewer", "patrol", "designer"]',
            "monitor_max_panes = 6",
            "",
            "[seat_overrides.memory]",
            'tool = "claude"',
            'auth_mode = "oauth"',
            'provider = "anthropic"',
            "",
            "[seat_overrides.planner]",
            'tool = "claude"',
            'auth_mode = "api"',
            'provider = "deepseek"',
            'model = "deepseek-v4-pro[1M]"',
            "",
            "[seat_overrides.builder]",
            'tool = "codex"',
            'auth_mode = "oauth"',
            'provider = "openai"',
            "",
            "[seat_overrides.reviewer]",
            'tool = "claude"',
            'auth_mode = "oauth"',
            'provider = "anthropic"',
            "",
            "[seat_overrides.patrol]",
            'tool = "claude"',
            'auth_mode = "api"',
            'provider = "minimax"',
            'model = "MiniMax-M2.7-highspeed"',
            "",
            "[seat_overrides.designer]",
            'tool = "gemini"',
            'auth_mode = "oauth"',
            'provider = "google"',
            "",
        ]
    )
    result, project_path, _agent_admin_log = _run_existing_project(tmp_path, complete)
    assert result.returncode == 0, result.stderr
    assert project_path.read_text(encoding="utf-8") == complete


def test_template_switch_replaces_old_roster_and_regenerates_workspaces(tmp_path: Path) -> None:
    result, project_path, agent_admin_log = _run_existing_project(
        tmp_path,
        "\n".join(
            [
                'name = "migratecase"',
                'template_name = "clawseat-creative"',
                'engineers = ["memory", "planner", "builder", "patrol", "designer"]',
                'monitor_engineers = ["memory", "planner", "builder", "patrol", "designer"]',
                "monitor_max_panes = 5",
                "",
                "[seat_overrides.memory]",
                'tool = "claude"',
                'auth_mode = "oauth"',
                'provider = "anthropic"',
                "",
            ]
        ),
        template="clawseat-engineering",
    )

    assert result.returncode == 0, result.stderr
    data = tomllib.loads(project_path.read_text(encoding="utf-8"))
    assert data["template_name"] == "clawseat-engineering"
    assert data["engineers"] == ["memory", "planner", "builder", "reviewer", "patrol"]
    assert data["monitor_engineers"] == ["memory", "planner", "builder", "reviewer", "patrol"]
    assert data["monitor_max_panes"] == 5
    assert "designer" not in data["seat_overrides"]
    assert "reviewer" in data["seat_overrides"]

    calls = [
        json.loads(line)["argv"]
        for line in agent_admin_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert ["engineer", "regenerate-workspace", "--project", "migratecase", "--all-seats", "--yes"] in calls


def test_install_renders_brief_after_profile_correction_steps() -> None:
    text = (_REPO / "scripts" / "install.sh").read_text(encoding="utf-8")

    bootstrap_index = text.index("  bootstrap_project_profile\n")
    migrate_index = text.index("  migrate_project_profile_to_v2\n")
    render_index = text.index("  render_brief\n")
    assert bootstrap_index < render_index
    assert migrate_index < render_index
