from __future__ import annotations

import importlib.util
import json
import os
import shlex
from pathlib import Path


_HELPERS_PATH = Path(__file__).with_name("test_launcher_gemini_trust_seed.py")
_HELPERS_SPEC = importlib.util.spec_from_file_location("test_launcher_gemini_trust_seed_helpers_claude", _HELPERS_PATH)
assert _HELPERS_SPEC is not None and _HELPERS_SPEC.loader is not None
_HELPERS = importlib.util.module_from_spec(_HELPERS_SPEC)
_HELPERS_SPEC.loader.exec_module(_HELPERS)

_run_bash = _HELPERS._run_bash


def _seed_claude_home(real_home: Path) -> Path:
    claude_home = real_home / ".claude"
    claude_home.mkdir(parents=True, exist_ok=True)
    (real_home / ".claude.json").write_text('{"hasCompletedOnboarding":true}\n', encoding="utf-8")
    (claude_home / "settings.json").write_text('{"theme":"dark"}\n', encoding="utf-8")
    (claude_home / "statsig").write_text("statsig", encoding="utf-8")
    (claude_home / "history.jsonl").write_text('{"type":"history"}\n', encoding="utf-8")
    (claude_home / "skills").mkdir(parents=True, exist_ok=True)
    (claude_home / "commands").mkdir(parents=True, exist_ok=True)
    (claude_home / "agents").mkdir(parents=True, exist_ok=True)
    (claude_home / "projects").mkdir(parents=True, exist_ok=True)
    (claude_home / "tasks").mkdir(parents=True, exist_ok=True)
    return claude_home


def test_prepare_claude_home_materializes_local_settings_and_skills_dirs(tmp_path: Path) -> None:
    real_home = tmp_path / "real_home"
    runtime_home = tmp_path / "runtime_home"
    real_home.mkdir(parents=True)
    runtime_home.mkdir(parents=True)

    claude_home = _seed_claude_home(real_home)

    result = _run_bash(real_home, f"prepare_claude_home {runtime_home!s}")

    assert result.returncode == 0, result.stderr
    runtime_claude_json = runtime_home / ".claude.json"
    assert runtime_claude_json.is_file()
    assert not runtime_claude_json.is_symlink()
    runtime_data = json.loads(runtime_claude_json.read_text(encoding="utf-8"))
    assert runtime_data["hasCompletedOnboarding"] is True
    assert runtime_data["skipDangerousModePermissionPrompt"] is True

    runtime_settings = runtime_home / ".claude" / "settings.json"
    runtime_skills = runtime_home / ".claude" / "skills"
    assert runtime_settings.is_file()
    assert not runtime_settings.is_symlink()
    assert json.loads(runtime_settings.read_text(encoding="utf-8")) == {
        "hooks": {},
        "permissions": {},
    }
    assert runtime_skills.is_dir()
    assert not runtime_skills.is_symlink()
    assert list(runtime_skills.iterdir()) == []

    for item in ("statsig", "commands", "agents"):
        link = runtime_home / ".claude" / item
        assert link.is_symlink()
        assert link.readlink() == claude_home / item


def test_prepare_claude_home_keeps_runtime_state_isolated(tmp_path: Path) -> None:
    real_home = tmp_path / "real_home"
    runtime_home = tmp_path / "runtime_home"
    real_home.mkdir(parents=True)
    runtime_home.mkdir(parents=True)

    _seed_claude_home(real_home)

    result = _run_bash(real_home, f"prepare_claude_home {runtime_home!s}")

    assert result.returncode == 0, result.stderr
    for item in ("projects", "history.jsonl", "tasks"):
        assert not (runtime_home / ".claude" / item).exists()


def test_prepare_claude_home_preserves_existing_runtime_dirs(tmp_path: Path) -> None:
    real_home = tmp_path / "real_home"
    runtime_home = tmp_path / "runtime_home"
    real_home.mkdir(parents=True)
    runtime_home.mkdir(parents=True)

    _seed_claude_home(real_home)
    existing_skills = runtime_home / ".claude" / "skills"
    existing_skills.mkdir(parents=True, exist_ok=True)
    (existing_skills / "sentinel.txt").write_text("keep-me", encoding="utf-8")

    result = _run_bash(real_home, f"prepare_claude_home {runtime_home!s}")

    assert result.returncode == 0, result.stderr
    assert existing_skills.is_dir()
    assert not existing_skills.is_symlink()
    assert (existing_skills / "sentinel.txt").read_text(encoding="utf-8") == "keep-me"


def test_prepare_claude_home_materializes_local_onboarding_file_when_real_home_is_incomplete(tmp_path: Path) -> None:
    real_home = tmp_path / "real_home"
    runtime_home = tmp_path / "runtime_home"
    real_home.mkdir(parents=True)
    runtime_home.mkdir(parents=True)

    _seed_claude_home(real_home)
    (real_home / ".claude.json").write_text(
        json.dumps(
            {
                "hasCompletedOnboarding": False,
                "hasSeenWelcome": False,
                "lastOnboardingVersion": "1.2.3",
                "customFlag": "keep-me",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run_bash(real_home, f"prepare_claude_home {runtime_home!s}")

    assert result.returncode == 0, result.stderr
    runtime_claude_json = runtime_home / ".claude.json"
    runtime_data = json.loads(runtime_claude_json.read_text(encoding="utf-8"))
    assert runtime_claude_json.is_file()
    assert not runtime_claude_json.is_symlink()
    assert runtime_data["hasCompletedOnboarding"] is True
    assert runtime_data["hasSeenWelcome"] is True
    assert runtime_data["skipDangerousModePermissionPrompt"] is True
    assert runtime_data["lastOnboardingVersion"] == "1.2.3"
    assert runtime_data["customFlag"] == "keep-me"
    real_data = json.loads((real_home / ".claude.json").read_text(encoding="utf-8"))
    assert real_data["hasCompletedOnboarding"] is False


def test_prepare_claude_home_replaces_legacy_runtime_symlink_with_local_file(tmp_path: Path) -> None:
    real_home = tmp_path / "real_home"
    runtime_home = tmp_path / "runtime_home"
    real_home.mkdir(parents=True)
    runtime_home.mkdir(parents=True)

    _seed_claude_home(real_home)
    runtime_claude_json = runtime_home / ".claude.json"
    runtime_claude_json.symlink_to(real_home / ".claude.json")

    result = _run_bash(real_home, f"prepare_claude_home {runtime_home!s}")

    assert result.returncode == 0, result.stderr
    assert runtime_claude_json.is_file()
    assert not runtime_claude_json.is_symlink()
    runtime_data = json.loads(runtime_claude_json.read_text(encoding="utf-8"))
    assert runtime_data["hasCompletedOnboarding"] is True


def test_prepare_claude_home_seeds_onboarding_stub_when_real_file_missing(tmp_path: Path) -> None:
    real_home = tmp_path / "real_home"
    runtime_home = tmp_path / "runtime_home"
    real_home.mkdir(parents=True)
    runtime_home.mkdir(parents=True)

    claude_home = real_home / ".claude"
    claude_home.mkdir(parents=True, exist_ok=True)

    result = _run_bash(real_home, f"prepare_claude_home {runtime_home!s}")

    assert result.returncode == 0, result.stderr
    runtime_claude_json = runtime_home / ".claude.json"
    assert runtime_claude_json.is_file()
    assert not runtime_claude_json.is_symlink()
    assert '"hasCompletedOnboarding": true' in runtime_claude_json.read_text(encoding="utf-8")
    assert '"skipDangerousModePermissionPrompt": true' in runtime_claude_json.read_text(encoding="utf-8")


def test_prepare_claude_host_oauth_state_patches_only_host_onboarding_and_trust(tmp_path: Path) -> None:
    real_home = tmp_path / "real_home"
    workdir = tmp_path / "workspace"
    real_home.mkdir(parents=True)
    workdir.mkdir(parents=True)
    claude_dir = real_home / ".claude"
    claude_dir.mkdir(parents=True)
    settings_path = claude_dir / "settings.json"
    settings_path.write_text('{"theme":"dark"}\n', encoding="utf-8")
    (real_home / ".claude.json").write_text(
        json.dumps(
            {
                "hasCompletedOnboarding": False,
                "hasSeenWelcome": False,
                "lastOnboardingVersion": "2.1.126",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run_bash(
        real_home,
        f"prepare_claude_host_oauth_state {shlex.quote(str(real_home))} {shlex.quote(str(workdir))}",
    )

    assert result.returncode == 0, result.stderr
    data = json.loads((real_home / ".claude.json").read_text(encoding="utf-8"))
    assert data["hasCompletedOnboarding"] is True
    assert data["hasSeenWelcome"] is True
    assert data["skipDangerousModePermissionPrompt"] is True
    assert data["lastOnboardingVersion"] == "2.1.126"
    assert data["projects"][str(workdir)]["hasTrustDialogAccepted"] is True
    assert data["projects"][str(workdir)]["hasCompletedProjectOnboarding"] is True
    assert settings_path.read_text(encoding="utf-8") == '{"theme":"dark"}\n'


def test_run_claude_runtime_oauth_branch_seeds_host_onboarding_before_exec(tmp_path: Path) -> None:
    real_home = tmp_path / "real_home"
    workdir = tmp_path / "workspace"
    real_home.mkdir(parents=True)
    workdir.mkdir(parents=True)
    (real_home / ".claude").mkdir(parents=True)
    (real_home / ".claude.json").write_text('{"hasCompletedOnboarding":false}\n', encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    log_file = tmp_path / "claude.log"
    (bin_dir / "claude").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "cat \"$HOME/.claude.json\" > \"${CLAUDE_LOG_FILE:?}\"\n",
        encoding="utf-8",
    )
    (bin_dir / "claude").chmod(0o755)

    result = _run_bash(
        real_home,
        f"run_claude_runtime oauth {shlex.quote(str(workdir))} test-claude-session",
        extra_env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "CLAUDE_LOG_FILE": str(log_file),
        },
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(log_file.read_text(encoding="utf-8"))
    assert data["hasCompletedOnboarding"] is True
    assert data["hasSeenWelcome"] is True
    assert data["skipDangerousModePermissionPrompt"] is True
    assert data["projects"][str(workdir)]["hasTrustDialogAccepted"] is True
    assert data["projects"][str(workdir)]["hasCompletedProjectOnboarding"] is True
