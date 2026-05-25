#!/usr/bin/env python3
"""Install the universal seat clear/compact watchdog scheduler."""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CLAWSEAT_ROOT = SCRIPT_DIR.parents[3]
LABEL = "com.clawseat.seat-clear-watchdog"
CRON_MARKER = "# clawseat-seat-clear-watchdog"


def _home() -> Path:
    return Path(os.environ.get("CLAWSEAT_REAL_HOME") or os.environ.get("HOME") or str(Path.home())).expanduser()


def render_plist(*, python_bin: str, clawseat_root: Path, home: Path, interval: int, tmux_bin: str = "tmux") -> str:
    watchdog = clawseat_root / "core" / "scripts" / "seat_clear_watchdog.py"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python_bin}</string>
    <string>{watchdog}</string>
    <string>--once</string>
    <string>--tmux-bin</string>
    <string>{tmux_bin}</string>
  </array>
  <key>StartInterval</key><integer>{interval}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{home}/.agents/logs/seat-clear-watchdog.log</string>
  <key>StandardErrorPath</key><string>{home}/.agents/logs/seat-clear-watchdog.err</string>
</dict>
</plist>
"""


def install_launchd(
    *,
    home: Path,
    clawseat_root: Path,
    python_bin: str,
    interval: int,
    tmux_bin: str,
    load: bool = True,
) -> tuple[Path, bool]:
    plist_path = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    expected = render_plist(
        python_bin=python_bin,
        clawseat_root=clawseat_root,
        home=home,
        interval=interval,
        tmux_bin=tmux_bin,
    )
    if plist_path.exists() and plist_path.read_text(encoding="utf-8") == expected:
        print(f"unchanged: {plist_path}")
        return plist_path, False
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    (home / ".agents" / "logs").mkdir(parents=True, exist_ok=True)
    existed = plist_path.exists()
    plist_path.write_text(expected, encoding="utf-8")
    print(f"{'updated' if existed else 'installed'}: {plist_path}")
    if load:
        launchctl = os.environ.get("LAUNCHCTL_BIN", "launchctl")
        if existed:
            subprocess.run([launchctl, "unload", str(plist_path)], check=False, text=True, capture_output=True)
        result = subprocess.run([launchctl, "load", str(plist_path)], check=False, text=True, capture_output=True)
        if result.returncode == 0:
            print(f"loaded: {LABEL}")
        else:
            print(f"warn: launchctl load failed for {plist_path}: {result.stderr.strip()}", file=sys.stderr)
    return plist_path, True


def install_cron(*, clawseat_root: Path, python_bin: str, tmux_bin: str) -> bool:
    watchdog = clawseat_root / "core" / "scripts" / "seat_clear_watchdog.py"
    entry = f"{CRON_MARKER}\n*/1 * * * * {python_bin} {watchdog} --once --tmux-bin {tmux_bin}"
    current = subprocess.run(["crontab", "-l"], check=False, text=True, capture_output=True)
    existing = current.stdout if current.returncode == 0 else ""
    if CRON_MARKER in existing:
        print("unchanged: crontab seat clear watchdog")
        return False
    new_cron = existing.rstrip("\n")
    if new_cron:
        new_cron += "\n"
    new_cron += entry + "\n"
    subprocess.run(["crontab", "-"], input=new_cron, check=True, text=True)
    print("installed: crontab seat clear watchdog")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clawseat-root", default=str(DEFAULT_CLAWSEAT_ROOT))
    parser.add_argument("--home", default=str(_home()))
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--tmux-bin", default=shutil.which("tmux") or "tmux")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--no-load", action="store_true", help="Write plist without launchctl load.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = Path(args.home).expanduser()
    clawseat_root = Path(args.clawseat_root).expanduser().resolve()
    if platform.system() == "Darwin":
        install_launchd(
            home=home,
            clawseat_root=clawseat_root,
            python_bin=args.python_bin,
            interval=args.interval,
            tmux_bin=args.tmux_bin,
            load=not args.no_load,
        )
        return 0
    install_cron(clawseat_root=clawseat_root, python_bin=args.python_bin, tmux_bin=args.tmux_bin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
