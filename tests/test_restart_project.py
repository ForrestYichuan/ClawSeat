import os
import subprocess
import sys
import textwrap
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "restart-project.sh"


def test_restart_project_restarts_all_materialized_project_seats(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    log = tmp_path / "restart-seat.log"
    project = "smoke-project-restart"
    seats = ["memory", "planner", "builder"]

    workspace.mkdir(parents=True)
    (home / ".agents" / "projects" / project).mkdir(parents=True)
    (home / ".agents" / "projects" / project / "project.toml").write_text(
        textwrap.dedent(
            f"""\
            version = 1
            name = "{project}"
            repo_root = "{workspace}"
            monitor_session = "project-{project}-monitor"
            engineers = ["memory", "planner", "builder", "reviewer"]
            monitor_engineers = ["memory", "planner", "builder", "reviewer"]
            """
        ),
        encoding="utf-8",
    )
    for seat in seats:
        session_dir = home / ".agents" / "sessions" / project / seat
        session_dir.mkdir(parents=True)
        (session_dir / "session.toml").write_text(
            textwrap.dedent(
                f"""\
                version = 1
                project = "{project}"
                engineer_id = "{seat}"
                tool = "claude"
                auth_mode = "oauth"
                provider = "anthropic"
                identity = "claude.oauth.anthropic.{project}.{seat}"
                workspace = "{workspace}"
                runtime_dir = ""
                session = "{project}-{seat}-claude"
                bin_path = "/usr/bin/true"
                monitor = true
                legacy_sessions = []
                launch_args = []
                """
            ),
            encoding="utf-8",
        )

    fake_restart_seat = tmp_path / "restart-seat.sh"
    fake_restart_seat.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log}
            echo "fake restart $1/$2"
            """
        ),
        encoding="utf-8",
    )
    fake_restart_seat.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
            "RESTART_SEAT_SCRIPT": str(fake_restart_seat),
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), project, "--no-window"],
        cwd=str(REPO),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "restart-project:" in result.stdout
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"{project} memory --no-window",
        f"{project} planner --no-window",
        f"{project} builder --no-window",
    ]
