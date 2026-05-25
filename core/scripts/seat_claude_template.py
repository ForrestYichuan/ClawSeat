from __future__ import annotations

import argparse
import json
import shutil
import sys as _sys, pathlib as _pl
_sct_scripts = str(_pl.Path(__file__).resolve().parent)
_sct_lib = str(_pl.Path(__file__).resolve().parent.parent / "lib")
for _p in (_sct_scripts, _sct_lib):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load
from pathlib import Path

# Import real_user_home via the flat-module path (core/lib on sys.path)
# so this module works both as a CLI and when imported from an inline
# Python block that only adds core/scripts to sys.path (e.g. sandbox.sh).
try:
    from real_home import real_user_home  # flat import: core/lib on sys.path
except ImportError:
    from core.lib.real_home import real_user_home  # package import fallback

try:
    from seat_skill_mapping import skill_names_for_seat
except ModuleNotFoundError:  # pragma: no cover
    from .seat_skill_mapping import skill_names_for_seat


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENGINEERS_ROOT = real_user_home() / ".agents" / "engineers"


def engineer_root(engineers_root: Path, seat_id: str) -> Path:
    return engineers_root / seat_id


def template_root(engineers_root: Path, seat_id: str) -> Path:
    return engineer_root(engineers_root, seat_id) / ".claude-template"


def role_hint_from_engineer(engineers_root: Path, seat_id: str) -> str | None:
    path = engineer_root(engineers_root, seat_id) / "engineer.toml"
    if not path.is_file():
        return None
    try:
        data = _toml_loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    role = str(data.get("role", "") or "").strip()
    return role or None


def render_settings_for_seat(seat_id: str, clawseat_root: Path | None = None) -> dict[str, object]:
    clawseat_root = (clawseat_root or REPO_ROOT).resolve()
    settings: dict[str, object] = {
        "hooks": {},
        "permissions": {
            "defaultMode": "bypassPermissions",
        },
        "skipDangerousModePermissionPrompt": True,
    }
    if seat_id == "memory":
        settings["hooks"] = {
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"bash {clawseat_root / 'scripts' / 'hooks' / 'memory-stop-hook.sh'}",
                            "timeout": 10,
                        }
                    ],
                }
            ]
        }
    return settings


def ensure_seat_claude_template(
    engineers_root: Path,
    seat_id: str,
    *,
    role_hint: str | None = None,
    clawseat_root: Path | None = None,
) -> Path:
    clawseat_root = (clawseat_root or REPO_ROOT).resolve()
    role_hint = role_hint or role_hint_from_engineer(engineers_root, seat_id)
    root = template_root(engineers_root, seat_id)
    skills_root = root / "skills"
    root.mkdir(parents=True, exist_ok=True)
    if skills_root.exists():
        shutil.rmtree(skills_root)
    skills_root.mkdir(parents=True, exist_ok=True)

    for skill_name in skill_names_for_seat(seat_id, role_hint=role_hint):
        source_dir = clawseat_root / "core" / "skills" / skill_name
        if not source_dir.is_dir():
            raise FileNotFoundError(f"seat template skill not found for {seat_id}: {source_dir}")
        shutil.copytree(source_dir, skills_root / skill_name)

    settings_path = root / "settings.json"
    settings_path.write_text(
        json.dumps(render_settings_for_seat(seat_id, clawseat_root=clawseat_root), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return root


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)


def copy_seat_claude_template_to_runtime(
    engineers_root: Path,
    seat_id: str,
    runtime_claude_root: Path,
    *,
    role_hint: str | None = None,
    clawseat_root: Path | None = None,
) -> Path:
    role_hint = role_hint or role_hint_from_engineer(engineers_root, seat_id)
    template_dir = ensure_seat_claude_template(
        engineers_root,
        seat_id,
        role_hint=role_hint,
        clawseat_root=clawseat_root,
    )
    runtime_claude_root.mkdir(parents=True, exist_ok=True)

    runtime_settings = runtime_claude_root / "settings.json"
    runtime_skills = runtime_claude_root / "skills"
    for path in (runtime_settings, runtime_skills):
        if path.exists() or path.is_symlink():
            _remove_path(path)

    shutil.copy2(template_dir / "settings.json", runtime_settings)
    shutil.copytree(template_dir / "skills", runtime_skills)
    return template_dir


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a per-seat Claude template.")
    parser.add_argument("--seat", required=True, help="Seat id, e.g. planner or memory.")
    parser.add_argument(
        "--engineers-root",
        default=str(DEFAULT_ENGINEERS_ROOT),
        help="Root containing ~/.agents/engineers/<seat>/.",
    )
    parser.add_argument(
        "--clawseat-root",
        default=str(REPO_ROOT),
        help="ClawSeat checkout used to source core/skills/*.",
    )
    parser.add_argument("--role-hint", help="Optional engineer role hint, e.g. planner-dispatcher.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = ensure_seat_claude_template(
        Path(args.engineers_root).expanduser().resolve(),
        args.seat,
        role_hint=args.role_hint,
        clawseat_root=Path(args.clawseat_root).expanduser().resolve(),
    )
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
