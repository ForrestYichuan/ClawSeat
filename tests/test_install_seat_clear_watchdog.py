from __future__ import annotations

import subprocess
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SCRIPT_DIR = _REPO / "core" / "skills" / "clawseat-install" / "scripts"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import install_seat_clear_watchdog as installer  # noqa: E402


def test_install_seat_clear_watchdog_writes_launchd_plist_and_loads(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(installer.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    home = tmp_path / "home"
    clawseat_root = tmp_path / "ClawSeat"
    assert installer.main(
        [
            "--home",
            str(home),
            "--clawseat-root",
            str(clawseat_root),
            "--python-bin",
            "/usr/bin/python3",
            "--tmux-bin",
            "/usr/bin/tmux",
        ]
    ) == 0

    plist = home / "Library" / "LaunchAgents" / "com.clawseat.seat-clear-watchdog.plist"
    text = plist.read_text(encoding="utf-8")
    assert "<key>StartInterval</key><integer>60</integer>" in text
    assert "<string>/usr/bin/python3</string>" in text
    assert "<string>--tmux-bin</string>" in text
    assert "<string>/usr/bin/tmux</string>" in text
    assert f"<string>{clawseat_root}/core/scripts/seat_clear_watchdog.py</string>" in text
    assert "<string>--once</string>" in text
    assert calls == [["launchctl", "load", str(plist)]]


def test_install_seat_clear_watchdog_updates_stale_existing_plist(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(installer.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    home = tmp_path / "home"
    plist = home / "Library" / "LaunchAgents" / "com.clawseat.seat-clear-watchdog.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("operator-owned\n", encoding="utf-8")

    assert installer.main(["--home", str(home), "--clawseat-root", str(tmp_path / "ClawSeat")]) == 0

    assert "operator-owned" not in plist.read_text(encoding="utf-8")
    assert calls == [["launchctl", "unload", str(plist)], ["launchctl", "load", str(plist)]]


def test_install_seat_clear_watchdog_is_idempotent_when_plist_matches(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(installer.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    home = tmp_path / "home"
    clawseat_root = tmp_path / "ClawSeat"
    plist = home / "Library" / "LaunchAgents" / "com.clawseat.seat-clear-watchdog.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text(
        installer.render_plist(
            python_bin=sys.executable,
            clawseat_root=clawseat_root,
            home=home,
            interval=60,
            tmux_bin="tmux",
        ),
        encoding="utf-8",
    )

    assert installer.main(["--home", str(home), "--clawseat-root", str(clawseat_root), "--tmux-bin", "tmux"]) == 0

    assert calls == []
