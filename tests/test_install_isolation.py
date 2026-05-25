from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_INSTALL = _REPO / "scripts" / "install.sh"
_WAIT_FOR_SEAT = _REPO / "scripts" / "wait-for-seat.sh"


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _read_jsonl(path: Path) -> list[dict[str, str]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _fake_install_root(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    root = tmp_path / "fake-root"
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    py_stubs = tmp_path / "py-stubs"
    launcher_log = tmp_path / "launcher.jsonl"
    tmux_log = tmp_path / "tmux.log"

    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "core" / "lib").mkdir(parents=True, exist_ok=True)
    (root / "core" / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "core" / "shell-scripts").mkdir(parents=True, exist_ok=True)
    (root / "core" / "templates").mkdir(parents=True, exist_ok=True)
    shutil.copytree(_REPO / "templates", root / "templates", dirs_exist_ok=True)
    shutil.copytree(_REPO / "scripts" / "install", root / "scripts" / "install", dirs_exist_ok=True)
    shutil.copy2(_INSTALL, root / "scripts" / "install.sh")
    (root / "scripts" / "install.sh").chmod(0o755)
    shutil.copy2(_WAIT_FOR_SEAT, root / "scripts" / "wait-for-seat.sh")
    (root / "scripts" / "wait-for-seat.sh").chmod(0o755)
    shutil.copy2(
        _REPO / "core" / "scripts" / "agent_admin_config.py",
        root / "core" / "scripts" / "agent_admin_config.py",
    )
    shutil.copy2(
        _REPO / "core" / "scripts" / "seat_skill_mapping.py",
        root / "core" / "scripts" / "seat_skill_mapping.py",
    )
    shutil.copy2(
        _REPO / "core" / "scripts" / "seat_claude_template.py",
        root / "core" / "scripts" / "seat_claude_template.py",
    )
    shutil.copy2(
        _REPO / "core" / "scripts" / "projects_registry.py",
        root / "core" / "scripts" / "projects_registry.py",
    )
    shutil.copy2(
        _REPO / "core" / "scripts" / "_toml_compat.py",
        root / "core" / "scripts" / "_toml_compat.py",
    )
    shutil.copy2(
        _REPO / "core" / "scripts" / "reconcile_seat_states.py",
        root / "core" / "scripts" / "reconcile_seat_states.py",
    )
    shutil.copy2(
        _REPO / "core" / "scripts" / "migrate_ancestor_paths.py",
        root / "core" / "scripts" / "migrate_ancestor_paths.py",
    )
    shutil.copy2(
        _REPO / "core" / "scripts" / "clawseat-cli.sh",
        root / "core" / "scripts" / "clawseat-cli.sh",
    )
    (root / "core" / "scripts" / "projects_registry.py").chmod(0o755)
    (root / "core" / "scripts" / "clawseat-cli.sh").chmod(0o755)
    shutil.copy2(
        _REPO / "core" / "lib" / "real_home.py",
        root / "core" / "lib" / "real_home.py",
    )
    shutil.copy2(
        _REPO / "core" / "lib" / "state.py",
        root / "core" / "lib" / "state.py",
    )
    shutil.copy2(
        _REPO / "core" / "lib" / "utils.py",
        root / "core" / "lib" / "utils.py",
    )
    shutil.copy2(
        _REPO / "core" / "shell-scripts" / "send-and-verify.sh",
        root / "core" / "shell-scripts" / "send-and-verify.sh",
    )
    (root / "core" / "shell-scripts" / "send-and-verify.sh").chmod(0o755)
    shutil.copy2(
        _REPO / "core" / "templates" / "patrol.plist.in",
        root / "core" / "templates" / "patrol.plist.in",
    )

    _write_executable(
        root / "core" / "preflight.py",
        """#!/usr/bin/env python3
print("preflight ok")
""",
    )
    _write_executable(
        root / "core" / "skills" / "memory-oracle" / "scripts" / "scan_environment.py",
        """#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
machine = Path(args.output) / "machine"
machine.mkdir(parents=True, exist_ok=True)
(machine / "credentials.json").write_text(json.dumps({
    "keys": {
        "MINIMAX_API_KEY": {"value": "minimax-token"},
        "MINIMAX_BASE_URL": {"value": "https://api.minimaxi.com/anthropic"},
        "DEEPSEEK_API_KEY": {"value": "deepseek-token"},
    },
    "oauth": {"has_any": False},
}), encoding="utf-8")
for name in ("network", "openclaw", "github", "current_context"):
    (machine / f"{name}.json").write_text("{}", encoding="utf-8")
""",
    )
    _write_executable(
        root / "core" / "skills" / "memory-oracle" / "scripts" / "install_memory_hook.py",
        """#!/usr/bin/env python3
import sys
raise SystemExit(0)
""",
    )
    for skill_name in ("clawseat", "gstack-harness", "tmux-basics", "memory-oracle"):
        skill_dir = root / "core" / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {skill_name}\n", encoding="utf-8")
    _write_executable(
        root / "core" / "scripts" / "iterm_panes_driver.py",
        """#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import sys

payload = json.load(sys.stdin)
payload_log = os.environ.get("ITERM_PAYLOAD_LOG")
if payload_log:
    with open(payload_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\\n")
window_id = "grid-window" if payload.get("title", "").startswith("clawseat-") else "memory-window"
json.dump({"status": "ok", "window_id": window_id}, sys.stdout)
""",
    )
    _write_executable(
        root / "core" / "scripts" / "agent_admin.py",
        """#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
try:
    import tomllib
except ImportError:
    import tomli as tomllib

log_file = os.environ.get("AGENT_ADMIN_LOG")
if log_file:
    with open(log_file, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
if sys.argv[1:3] == ["project", "bootstrap"]:
    local_path = ""
    for idx, arg in enumerate(sys.argv):
        if arg == "--local" and idx + 1 < len(sys.argv):
            local_path = sys.argv[idx + 1]
            break
    project = "smoketest"
    repo_root = os.getcwd()
    if local_path:
        data = tomllib.loads(Path(local_path).read_text(encoding="utf-8"))
        project = str(data.get("project_name") or project)
        repo_root = str(data.get("repo_root") or repo_root)
    home = Path(os.environ.get("CLAWSEAT_REAL_HOME") or os.environ["HOME"])
    profile = home / ".agents" / "profiles" / f"{project}-profile-dynamic.toml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        "\\n".join([
            "version = 1",
            f'profile_name = "{project}"',
            f'project_name = "{project}"',
            f'repo_root = "{repo_root}"',
            'seats = ["memory", "planner", "builder", "patrol", "designer"]',
            "",
        ]),
        encoding="utf-8",
    )
raise SystemExit(0)
""",
    )
    _write_executable(
        root / "core" / "shell-scripts" / "agentctl.sh",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${AGENTCTL_LOG:-}" ]]; then
  printf '%s\\n' "$*" >> "${AGENTCTL_LOG:?}"
fi
if [[ "${1:-}" == "session-name" ]]; then
  shift
  project=""
  seat=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --project) project="$2"; shift 2 ;;
      *) seat="$1"; shift ;;
    esac
  done
  if [[ -n "$project" && -n "$seat" ]]; then
    printf '%s-%s\\n' "$project" "$seat"
  else
    printf '%s\\n' "$seat"
  fi
fi
exit 0
""",
    )
    (root / "core" / "templates" / "memory-bootstrap.template.md").write_text(
        "\n".join(
            [
                "# Brief for ${PROJECT_NAME} at ${CLAWSEAT_ROOT}",
                "Memory: ${AGENT_HOME}/.agents/memory/machine/",
                "Binding: ${AGENT_HOME}/.openclaw/workspace.toml",
                "Status: ${AGENT_HOME}/.agents/tasks/${PROJECT_NAME}/STATUS.md",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_executable(
        root / "core" / "launchers" / "agent-launcher.sh",
        """#!/usr/bin/env bash
set -euo pipefail
tool=""; auth=""; dir=""; session=""; custom_env_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool) tool="$2"; shift 2 ;;
    --auth) auth="$2"; shift 2 ;;
    --dir) dir="$2"; shift 2 ;;
    --session) session="$2"; shift 2 ;;
    --custom-env-file) custom_env_file="$2"; shift 2 ;;
    --headless|--dry-run) shift ;;
    *) shift ;;
  esac
done

if [[ -n "$custom_env_file" && -f "$custom_env_file" ]]; then
  set -a
  source "$custom_env_file"
  set +a
  rm -f "$custom_env_file"
fi

case "$tool/$auth" in
  claude/oauth_token) runtime_dir="$HOME/.agent-runtime/identities/claude/oauth_token/${auth}-${session}" ;;
  claude/*) runtime_dir="$HOME/.agent-runtime/identities/claude/api/${auth}-${session}" ;;
  *) runtime_dir="$HOME/.agent-runtime/identities/$tool/api/${auth}-${session}" ;;
esac
mkdir -p "$runtime_dir/home"

python3 - "$LOG_FILE" "$session" "$tool" "$auth" "$dir" "$custom_env_file" "$runtime_dir/home" "${CLAWSEAT_ROOT:-}" "${CLAWSEAT_MEMORY_BRIEF:-}" "${LAUNCHER_CUSTOM_BASE_URL:-}" "${LAUNCHER_CUSTOM_MODEL:-}" "${LAUNCHER_CUSTOM_API_KEY:-}" <<'PY'
from __future__ import annotations
import json
import os
from pathlib import Path
import sys

(
    log_file,
    session,
    tool,
    auth,
    workdir,
    custom_env_file,
    runtime_home,
    clawseat_root,
    brief,
    custom_base_url,
    custom_model,
    custom_api_key,
) = sys.argv[1:13]
record = {
    "session": session,
    "tool": tool,
    "auth": auth,
    "dir": workdir,
    "custom_env_file": custom_env_file,
    "runtime_home": runtime_home,
    "clawseat_root": clawseat_root,
    "brief": brief,
    "custom_base_url": custom_base_url,
    "custom_model": custom_model,
    "custom_api_key_present": bool(custom_api_key),
}
with Path(log_file).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\\n")
if os.environ.get("TMUX_LOG_FILE"):
    with Path(os.environ["TMUX_LOG_FILE"] + ".sessions").open("a", encoding="utf-8") as handle:
        handle.write(session + "\\n")
PY
""",
    )
    _write_executable(
        bin_dir / "tmux",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "has-session" ]]; then
  target="${3:-}"
  target="${target#=}"
  registry="${TMUX_LOG_FILE:-}.sessions"
  if [[ -n "${TMUX_LOG_FILE:-}" && -f "$registry" ]] && grep -Fxq "$target" "$registry"; then
    exit 0
  fi
  if [[ "$target" == "machine-memory-claude" ]]; then
    exit "${TMUX_HAS_MEMORY_SESSION_RC:-1}"
  fi
  exit 1
fi
if [[ "${1:-}" == "capture-pane" ]]; then
  target=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t) target="${2:-}"; shift 2 ;;
      *) shift ;;
    esac
  done
  target="${target#=}"
  printf 'capture-pane -t =%s\\n' "$target" >> "${TMUX_LOG_FILE:?}"
  if [[ -n "${TMUX_PANE_DIR:-}" ]]; then
    counter_file="${TMUX_PANE_DIR}/.${target}.counter"
    counter="1"
    if [[ -f "$counter_file" ]]; then
      counter="$(cat "$counter_file")"
    fi
    numbered_path="${TMUX_PANE_DIR}/${target}.${counter}.txt"
    static_path="${TMUX_PANE_DIR}/${target}.txt"
    if [[ -f "$numbered_path" ]]; then
      cat "$numbered_path"
      printf '%s' "$((counter + 1))" > "$counter_file"
      exit 0
    fi
    if [[ -f "$static_path" ]]; then
      cat "$static_path"
      exit 0
    fi
  fi
  printf '%s' "${TMUX_CAPTURE_PANE_TEXT:-Type your message}"
  exit 0
fi
printf '%s\\n' "$*" >> "${TMUX_LOG_FILE:?}"
""",
    )
    _write_executable(
        bin_dir / "launchctl",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${LAUNCHCTL_LOG_FILE:-}" ]]; then
  printf '%s\\n' "$*" >> "${LAUNCHCTL_LOG_FILE:?}"
fi
exit 0
""",
    )
    _write_executable(
        bin_dir / "plutil",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${PLUTIL_LOG_FILE:-}" ]]; then
  printf '%s\\n' "$*" >> "${PLUTIL_LOG_FILE:?}"
fi
exit 0
""",
    )
    _write_executable(
        bin_dir / "sleep",
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )
    _write_executable(
        bin_dir / "uname",
        """#!/usr/bin/env bash
printf 'Darwin\\n'
""",
    )
    py_stubs.mkdir(parents=True, exist_ok=True)
    (py_stubs / "iterm2.py").write_text(
        textwrap.dedent(
            """
            import asyncio


            class _Session:
                def __init__(self, name):
                    self.name = name

                async def async_activate(self):
                    return None


            class _Tab:
                def __init__(self, names):
                    self.sessions = [_Session(name) for name in names]


            class _Window:
                def __init__(self, window_id, names):
                    self.window_id = window_id
                    self.tabs = [_Tab(names)]

                async def async_activate(self):
                    return None


            class _App:
                def __init__(self):
                    self.windows = [
                        _Window("grid-window", ["ancestor"]),
                        _Window("memory-window", ["memory"]),
                    ]

                async def async_activate(self):
                    return None


            async def async_get_app(connection):
                return _App()


            def run_until_complete(main):
                return asyncio.run(main(None))
            """
        ),
        encoding="utf-8",
    )
    (home / ".agents").mkdir(parents=True, exist_ok=True)
    (home / ".agents" / ".env.global").write_text(
        "export DEEPSEEK_API_KEY=deepseek-token\nexport MINIMAX_API_KEY=minimax-token\n",
        encoding="utf-8",
    )
    return root, home, launcher_log, tmux_log, py_stubs


def test_install_script_no_direct_tmux_new_session() -> None:
    text = _INSTALL.read_text(encoding="utf-8") + "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((_REPO / "scripts" / "install" / "lib").glob("*.sh"))
    )
    assert "tmux new-session" not in text
    assert "agent-launcher.sh" in text
    assert "launch_seat()" in text


def test_fake_install_root_includes_reconcile_helpers(tmp_path: Path) -> None:
    root, _home, _launcher_log, _tmux_log, _py_stubs = _fake_install_root(tmp_path)

    assert (root / "core" / "scripts" / "reconcile_seat_states.py").is_file()
    assert (root / "core" / "scripts" / "migrate_ancestor_paths.py").is_file()


def test_install_dry_run_uses_agent_launcher(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = subprocess.run(
        ["bash", str(_INSTALL), "--dry-run", "--project", "smoketest"],
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
        },
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "agent-launcher.sh --headless --tool" in output
    assert "tmux new-session" not in output


def test_install_launches_isolated_seats_via_launcher(tmp_path: Path) -> None:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    result = subprocess.run(
        ["bash", str(root / "scripts" / "install.sh"), "--project", "smoketest", "--provider", "1"],
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
    assert result.returncode == 0, result.stderr

    records = _read_jsonl(launcher_log)
    expected_sessions = [
        "smoketest-memory-claude",
    ]
    assert [record["session"] for record in records] == expected_sessions

    expected_primary_dir = str(home / ".agents" / "workspaces" / "smoketest" / "memory")
    expected_brief = str(home / ".agents" / "tasks" / "smoketest" / "patrol" / "handoffs" / "memory-bootstrap.md")

    for record in records:
        session = record["session"]
        assert record["tool"] == "claude"
        assert record["auth"] == "custom"
        assert record["custom_env_file"]
        assert record["custom_api_key_present"] is True
        assert record["custom_base_url"] == "https://api.minimaxi.com/anthropic"
        assert record["custom_model"] == "MiniMax-M2.7-highspeed"
        assert record["clawseat_root"] == str(root)
        assert record["dir"] == expected_primary_dir
        assert record["runtime_home"] == str(
            home / ".agent-runtime" / "identities" / "claude" / "api" / f"custom-{session}" / "home"
        )

    assert records[0]["brief"] == expected_brief
    brief_text = Path(expected_brief).read_text(encoding="utf-8")
    assert "~/" not in brief_text
    assert "${AGENT_HOME}" not in brief_text
    assert f"Memory: {home}/.agents/memory/machine/" in brief_text
    assert f"Binding: {home}/.openclaw/workspace.toml" in brief_text
    assert f"Status: {home}/.agents/tasks/smoketest/STATUS.md" in brief_text

    tmux_output = tmux_log.read_text(encoding="utf-8")
    assert "new-session" not in tmux_output
    assert "kill-session -t =smoketest-memory-claude" in tmux_output
    assert "send-keys -t smoketest-memory-claude Enter" not in tmux_output
