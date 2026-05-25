from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_symlink_skills_preserves_unmanaged_directory_before_linking(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = root / "core" / "skills" / "clawseat-memory"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# current\n", encoding="utf-8")

    skills_home = tmp_path / "home" / ".agents" / "skills"
    stale = skills_home / "clawseat-memory"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("# stale\n", encoding="utf-8")

    script = f"""
set -euo pipefail
REPO_ROOT={shlex.quote(str(root))}
DRY_RUN=0
die() {{ echo "die:$*" >&2; exit 1; }}
warn() {{ echo "warn:$*" >&2; }}
source {shlex.quote(str(REPO / "scripts" / "install" / "lib" / "skills.sh"))}
symlink_skills {shlex.quote(str(skills_home))} clawseat-memory
"""
    result = subprocess.run(["bash", "-lc", script], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert stale.is_symlink()
    assert os.readlink(stale) == str(target)
    backups = list(skills_home.glob("clawseat-memory.bak.*"))
    assert len(backups) == 1
    assert (backups[0] / "SKILL.md").read_text(encoding="utf-8") == "# stale\n"
