from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_AGENT_ADMIN = _REPO / "core" / "scripts" / "agent_admin.py"
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from seat_claude_template import ensure_seat_claude_template


def _bootstrap_project(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    local_toml = tmp_path / "local.toml"
    local_toml.write_text(
        "\n".join(
            [
                'project_name = "spawn49"',
                f'repo_root = "{_REPO}"',
                "",
                "[[overrides]]",
                'id = "planner"',
                'session_name = "spawn49-planner"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_AGENT_ADMIN),
            "project",
            "bootstrap",
            "--template",
            "clawseat-engineering",
            "--local",
            str(local_toml),
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CLAWSEAT_REAL_HOME": str(home),
        },
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return home


def test_project_bootstrap_populates_seat_claude_templates(tmp_path: Path) -> None:
    home = _bootstrap_project(tmp_path)
    engineers_root = home / ".agents" / "engineers"

    planner_template = engineers_root / "planner" / ".claude-template"
    builder_template = engineers_root / "builder" / ".claude-template"
    reviewer_template = engineers_root / "reviewer" / ".claude-template"
    patrol_template = engineers_root / "patrol" / ".claude-template"
    memory_template = engineers_root / "memory" / ".claude-template"

    assert planner_template.is_dir()
    assert builder_template.is_dir()
    assert reviewer_template.is_dir()
    assert patrol_template.is_dir()
    assert memory_template.is_dir()

    assert {path.name for path in (planner_template / "skills").iterdir()} == {
        "planner",
        "clawseat",
        "gstack-harness",
        "tmux-basics",
    }
    assert {path.name for path in (builder_template / "skills").iterdir()} == {
        "builder",
        "clawseat",
        "gstack-harness",
        "tmux-basics",
    }
    assert {path.name for path in (reviewer_template / "skills").iterdir()} == {
        "reviewer",
        "clawseat",
        "gstack-harness",
        "tmux-basics",
    }
    assert {path.name for path in (patrol_template / "skills").iterdir()} == {
        "patrol",
        "clawseat",
        "gstack-harness",
        "tmux-basics",
    }
    assert {path.name for path in (memory_template / "skills").iterdir()} == {
        "memory-oracle",
        "clawseat",
        "gstack-harness",
        "tmux-basics",
    }

    planner_settings = json.loads((planner_template / "settings.json").read_text(encoding="utf-8"))
    assert planner_settings["hooks"] == {}
    assert planner_settings["permissions"] == {"defaultMode": "bypassPermissions"}
    assert planner_settings["skipDangerousModePermissionPrompt"] is True


def test_memory_template_contains_role_plus_shared_skills_and_stop_hook(tmp_path: Path) -> None:
    engineers_root = tmp_path / "home" / ".agents" / "engineers"
    template_dir = ensure_seat_claude_template(engineers_root, "memory")

    assert {path.name for path in (template_dir / "skills").iterdir()} == {
        "memory-oracle",
        "clawseat",
        "gstack-harness",
        "tmux-basics",
    }

    settings = json.loads((template_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"] == {"defaultMode": "bypassPermissions"}
    assert settings["skipDangerousModePermissionPrompt"] is True
    stop_entries = settings["hooks"]["Stop"]
    assert len(stop_entries) == 1
    hook_def = stop_entries[0]["hooks"][0]
    assert hook_def["type"] == "command"
    assert hook_def["command"].endswith("/scripts/hooks/memory-stop-hook.sh")
    assert hook_def["timeout"] == 10
