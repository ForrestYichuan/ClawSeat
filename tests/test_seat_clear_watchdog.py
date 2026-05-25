from __future__ import annotations

import datetime
import json
from pathlib import Path
import sys


_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import seat_clear_watchdog as watchdog  # noqa: E402


_SESSION = "install-planner-gemini"
_CAPACITY_TEXT = "Selected model is at capacity. Please try a different model."


def _fake_tmux(tmp_path: Path) -> tuple[Path, Path, Path]:
    pane_file = tmp_path / "pane.txt"
    send_log = tmp_path / "send.log"
    tmux = tmp_path / "tmux"
    tmux.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, os, sys",
                "from pathlib import Path",
                "cmd = sys.argv[1]",
                "if cmd == 'list-sessions':",
                "    print(os.environ.get('FAKE_TMUX_SESSIONS', 'install-planner-gemini'))",
                "elif cmd == 'capture-pane':",
                "    print(Path(os.environ['FAKE_TMUX_PANE']).read_text(encoding='utf-8'), end='')",
                "elif cmd == 'send-keys':",
                "    path = Path(os.environ['FAKE_TMUX_SEND_LOG'])",
                "    with path.open('a', encoding='utf-8') as handle:",
                "        handle.write(json.dumps(sys.argv[1:]) + '\\n')",
                "else:",
                "    raise SystemExit(2)",
            ]
        ),
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    return tmux, pane_file, send_log


def _setup_env(tmp_path, monkeypatch, pane_text: str) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    (home / ".agents" / "projects" / "install").mkdir(parents=True)
    runtime_root = tmp_path / "runtime"
    tmux, pane_file, send_log = _fake_tmux(tmp_path)
    pane_file.write_text(pane_text, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAWSEAT_REAL_HOME", raising=False)
    monkeypatch.setenv("CLAWSEAT_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("FAKE_TMUX_PANE", str(pane_file))
    monkeypatch.setenv("FAKE_TMUX_SEND_LOG", str(send_log))
    monkeypatch.setenv("FAKE_TMUX_SESSIONS", _SESSION)
    return tmux, send_log, runtime_root


def _sent_commands(send_log: Path) -> list[list[str]]:
    if not send_log.exists():
        return []
    return [json.loads(line) for line in send_log.read_text(encoding="utf-8").splitlines()]


def _capacity_state_path(runtime_root: Path) -> Path:
    return runtime_root / "watchdog" / f"{_SESSION}.capacity.json"


def _load_capacity_state(runtime_root: Path) -> dict:
    path = _capacity_state_path(runtime_root)
    return json.loads(path.read_text(encoding="utf-8"))


def _isotime(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _parsed_iso(ts: str) -> float:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.datetime.fromisoformat(ts).timestamp()


def _set_fake_time(monkeypatch, now: float) -> list[float]:
    current = [now]

    def _time() -> float:
        return current[0]

    monkeypatch.setattr(watchdog.time, "time", _time)
    return current


def _write_pane_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _seed_capacity_state(runtime_root: Path, *, marker_line: str, now: float, retries: int, next_retry_in: int, escalated: bool = False) -> None:
    payload = {
        "first_seen_at": _isotime(now - 30),
        "retries": retries,
        "last_retry_at": _isotime(now - 20),
        "next_retry_at": _isotime(now + next_retry_in),
        "marker_line": marker_line,
        "escalated": escalated,
    }
    path = _capacity_state_path(runtime_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_watchdog_clear_marker_sends_once_and_records_seen(tmp_path, monkeypatch) -> None:
    _, send_log, runtime_root = _setup_env(tmp_path, monkeypatch, "done\n[CLEAR-REQUESTED]\n")

    assert watchdog.main(["--once", "--tmux-bin", str(tmp_path / "tmux")]) == 0
    assert _sent_commands(send_log) == [["send-keys", "-t", "install-planner-gemini", "/clear", "Enter"]]
    seen_files = list((runtime_root / "watchdog").glob("*.seen"))
    assert seen_files

    assert watchdog.main(["--once", "--tmux-bin", str(tmp_path / "tmux")]) == 0
    assert _sent_commands(send_log) == [["send-keys", "-t", "install-planner-gemini", "/clear", "Enter"]]


def test_watchdog_skips_when_pane_is_thinking(tmp_path, monkeypatch) -> None:
    _, send_log, runtime_root = _setup_env(tmp_path, monkeypatch, "Working...\n[CLEAR-REQUESTED]\n")

    assert watchdog.main(["--once", "--tmux-bin", str(tmp_path / "tmux")]) == 0

    assert _sent_commands(send_log) == []
    assert not _capacity_state_path(runtime_root).exists()


def test_watchdog_compact_marker_sends_compact(tmp_path, monkeypatch) -> None:
    _, send_log, runtime_root = _setup_env(tmp_path, monkeypatch, "context heavy\n[COMPACT-REQUESTED]\n")

    assert watchdog.main(["--once", "--tmux-bin", str(tmp_path / "tmux")]) == 0

    assert _sent_commands(send_log) == [["send-keys", "-t", "install-planner-gemini", "/compact", "Enter"]]
    assert not _capacity_state_path(runtime_root).exists()


def test_watchdog_capacity_marker_first_seen_tracks_state(tmp_path, monkeypatch) -> None:
    tmux, send_log, runtime_root = _setup_env(tmp_path, monkeypatch, f"{_CAPACITY_TEXT}\n")
    now = _set_fake_time(monkeypatch, 1_700_000_000.0)

    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0

    state = _load_capacity_state(runtime_root)
    assert state["retries"] == 0
    assert state["marker_line"] == _CAPACITY_TEXT
    assert not _sent_commands(send_log)
    assert abs(_parsed_iso(state["next_retry_at"]) - (now[0] + 10)) < 0.001


def test_watchdog_capacity_marker_retries_and_escalates(tmp_path, monkeypatch) -> None:
    tmux, send_log, runtime_root = _setup_env(
        tmp_path,
        monkeypatch,
        f"{_CAPACITY_TEXT}\n",
    )
    now = _set_fake_time(monkeypatch, 1_700_000_000.0)
    escalated_calls: list[tuple[str, str | None, str, int]] = []

    monkeypatch.setattr(
        watchdog,
        "_notify_capacity_exceeded",
        lambda session, project, marker_line, retries: escalated_calls.append(
            (session, project, marker_line, retries),
        ),
    )

    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert _sent_commands(send_log) == []
    assert _load_capacity_state(runtime_root)["retries"] == 0

    now[0] += 10
    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert _sent_commands(send_log) == [["send-keys", "-t", "install-planner-gemini", "继续", "Enter"]]
    assert _load_capacity_state(runtime_root)["retries"] == 1

    now[0] += 10
    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert _sent_commands(send_log) == [
        ["send-keys", "-t", "install-planner-gemini", "继续", "Enter"],
        ["send-keys", "-t", "install-planner-gemini", "继续", "Enter"],
    ]
    assert _load_capacity_state(runtime_root)["retries"] == 2

    now[0] += 10
    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert _sent_commands(send_log) == [
        ["send-keys", "-t", "install-planner-gemini", "继续", "Enter"],
        ["send-keys", "-t", "install-planner-gemini", "继续", "Enter"],
        ["send-keys", "-t", "install-planner-gemini", "继续", "Enter"],
    ]
    assert _load_capacity_state(runtime_root)["retries"] == 3

    now[0] += 10
    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert len(_sent_commands(send_log)) == 3
    assert escalated_calls == [("install-planner-gemini", "install", _CAPACITY_TEXT, 3)]
    assert _load_capacity_state(runtime_root)["escalated"] is True


def test_watchdog_capacity_marker_recovery_when_marker_disappears(tmp_path, monkeypatch) -> None:
    tmux, send_log, runtime_root = _setup_env(
        tmp_path,
        monkeypatch,
        f"{_CAPACITY_TEXT}\n",
    )
    now = _set_fake_time(monkeypatch, 1_700_000_000.0)

    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    now[0] += 10
    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert _load_capacity_state(runtime_root)["retries"] == 1

    _write_pane_text(tmp_path / "pane.txt", "done\n")
    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert not _capacity_state_path(runtime_root).exists()
    assert _sent_commands(send_log) == [
        ["send-keys", "-t", "install-planner-gemini", "继续", "Enter"],
    ]

    _write_pane_text(tmp_path / "pane.txt", f"{_CAPACITY_TEXT}\n")
    now[0] += 10
    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert _load_capacity_state(runtime_root)["retries"] == 0


def test_watchdog_capacity_marker_matches_uppercase_and_skips_when_thinking(tmp_path, monkeypatch) -> None:
    tmux, send_log, runtime_root = _setup_env(tmp_path, monkeypatch, "AT CAPACITY\n")
    now = _set_fake_time(monkeypatch, 1_700_000_000.0)

    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    now[0] += 10
    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    state = _load_capacity_state(runtime_root)
    assert state["marker_line"] == "AT CAPACITY"
    assert state["retries"] == 1

    _write_pane_text(tmp_path / "pane.txt", "working...\nAT CAPACITY\n")
    now[0] += 10
    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert _load_capacity_state(runtime_root)["retries"] == 1
    assert len(_sent_commands(send_log)) == 1


def test_watchdog_multiple_handlers_fire_capacity_first_then_clear(tmp_path, monkeypatch) -> None:
    tmux, send_log, runtime_root = _setup_env(
        tmp_path,
        monkeypatch,
        f"[CLEAR-REQUESTED]\n{_CAPACITY_TEXT}\n",
    )
    now = _set_fake_time(monkeypatch, 1_700_000_000.0)
    _seed_capacity_state(runtime_root, marker_line=_CAPACITY_TEXT, now=now[0], retries=2, next_retry_in=-1)

    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert _sent_commands(send_log) == [
        ["send-keys", "-t", "install-planner-gemini", "继续", "Enter"],
        ["send-keys", "-t", "install-planner-gemini", "/clear", "Enter"],
    ]
    assert _load_capacity_state(runtime_root)["retries"] == 3


def test_watchdog_auto_compacts_live_non_claude_planner_when_queue_drained(tmp_path, monkeypatch) -> None:
    tmux, tmux_send_log, _runtime_root = _setup_env(tmp_path, monkeypatch, "idle\n")
    home = tmp_path / "home"
    monkeypatch.setenv("FAKE_TMUX_SESSIONS", "install-team-planner-codex")

    profile = home / ".agents" / "profiles" / "install-profile-dynamic.toml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        """
project_name = "install"
seats = ["memory", "team-planner"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
t = { seats = ["team-planner"] }

[seat_roles]
memory = "project-memory"
team-planner = "planner"
""".lstrip(),
        encoding="utf-8",
    )
    queue = home / ".agents" / "tasks" / "install" / "t" / "tasks.queue.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "task_created",
                        "event_ts": "2026-05-20T00:00:00+00:00",
                        "seq": 1,
                        "actor": "memory",
                        "task_id": "task-1",
                        "brief_path": "tasks/install/t/brief/task-1.md",
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "event_type": "task_done",
                        "event_ts": "2026-05-20T00:01:00+00:00",
                        "seq": 2,
                        "actor": "team-planner@codex",
                        "task_id": "task-1",
                        "verdict": "PASS",
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    compact_send_log = tmp_path / "compact-send.log"
    send_script = tmp_path / "send-compact.sh"
    send_script.write_text(
        "#!/usr/bin/env bash\nset -eu\nprintf '%s\\n' \"$*\" >> \"$COMPACT_SEND_LOG\"\n",
        encoding="utf-8",
    )
    send_script.chmod(0o755)
    monkeypatch.setenv("CLAWSEAT_PLANNER_COMPACT_SEND_SCRIPT", str(send_script))
    monkeypatch.setenv("COMPACT_SEND_LOG", str(compact_send_log))

    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0

    assert _sent_commands(tmux_send_log) == []
    assert "--project install team-planner /compact" in compact_send_log.read_text(encoding="utf-8")
    snapshot = home / ".agents" / "tasks" / "install" / "t" / "runtime" / "planner-compact" / "latest.md"
    assert "- team: t" in snapshot.read_text(encoding="utf-8")

    assert watchdog.main(["--once", "--tmux-bin", str(tmux)]) == 0
    assert compact_send_log.read_text(encoding="utf-8").count("/compact") == 1
