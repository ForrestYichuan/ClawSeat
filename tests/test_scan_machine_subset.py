"""Tests for scan_environment.py machine/ subset enforcement (F2 reviewer finding).

Coverage:
  - Default full scan writes exactly the 5 MACHINE_DEFAULT_SCANNERS files
  - Default scan does NOT write legacy files (repos, gstack, clawseat) to machine/
  - machine/ file count ≤ 6 after default scan
  - machine/ files are within the known whitelist
  - --only repos still works (writes repos.json to machine/ when requested)
  - MACHINE_DEFAULT_SCANNERS constant contains exactly the expected 5 names
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "skills" / "memory-oracle" / "scripts"
_SCAN_PY = _SCRIPTS / "scan_environment.py"
sys.path.insert(0, str(_SCRIPTS))


MACHINE_WHITELIST = frozenset({
    "credentials.json",
    "network.json",
    "openclaw.json",
    "github.json",
    "current_context.json",
    "system.json",
    "environment.json",
    "gstack.json",
    "clawseat.json",
})

LEGACY_FILES = frozenset({"repos.json"})


def run_scan(*extra_args: str, memory_dir: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(_SCAN_PY), "--output", memory_dir, "--quiet"] + list(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def default_scan_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    memory_dir = tmp_path_factory.mktemp("scan-machine-default")
    result = run_scan(memory_dir=str(memory_dir))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    return memory_dir


@pytest.fixture(scope="module")
def default_machine_files(default_scan_dir: Path) -> set[str]:
    machine_dir = default_scan_dir / "machine"
    assert machine_dir.is_dir()
    return {p.name for p in machine_dir.iterdir() if p.is_file()}


# ── MACHINE_DEFAULT_SCANNERS constant ─────────────────────────────────────────


def test_machine_default_scanners_has_5_entries():
    from scan_environment import MACHINE_DEFAULT_SCANNERS
    assert len(MACHINE_DEFAULT_SCANNERS) == 5


def test_machine_default_scanners_contains_core_five():
    from scan_environment import MACHINE_DEFAULT_SCANNERS
    assert set(MACHINE_DEFAULT_SCANNERS) == {
        "credentials", "network", "openclaw", "github", "current_context"
    }


# ── Default scan output ───────────────────────────────────────────────────────


def test_default_scan_produces_exactly_5_files(default_machine_files):
    assert len(default_machine_files) == 5


def test_default_scan_file_count_le_6(default_machine_files):
    assert len(default_machine_files) <= 6, f"Expected ≤6 files in machine/, got {default_machine_files}"


def test_default_scan_files_within_whitelist(default_machine_files):
    unexpected = default_machine_files - MACHINE_WHITELIST
    assert not unexpected, f"Unexpected files in machine/: {unexpected}"


def test_default_scan_does_not_write_repos(default_scan_dir):
    assert not (default_scan_dir / "machine" / "repos.json").exists()


def test_default_scan_does_not_write_gstack(default_scan_dir):
    assert not (default_scan_dir / "machine" / "gstack.json").exists()


def test_default_scan_does_not_write_clawseat(default_scan_dir):
    assert not (default_scan_dir / "machine" / "clawseat.json").exists()


def test_default_scan_writes_current_context(default_scan_dir):
    assert (default_scan_dir / "machine" / "current_context.json").exists()


def test_default_scan_writes_credentials(default_scan_dir):
    assert (default_scan_dir / "machine" / "credentials.json").exists()


def test_default_scan_writes_network(default_scan_dir):
    assert (default_scan_dir / "machine" / "network.json").exists()


def test_default_scan_writes_github(default_scan_dir):
    assert (default_scan_dir / "machine" / "github.json").exists()


def test_default_scan_writes_openclaw(default_scan_dir):
    assert (default_scan_dir / "machine" / "openclaw.json").exists()


def test_index_is_at_root_not_machine(default_scan_dir):
    assert (default_scan_dir / "index.json").exists()
    assert not (default_scan_dir / "machine" / "index.json").exists()


# ── Legacy scanners still accessible via --only ───────────────────────────────


def test_only_repos_writes_to_machine(tmp_path):
    result = run_scan("--only", "repos", memory_dir=str(tmp_path))
    assert result.returncode == 0
    assert (tmp_path / "machine" / "repos.json").exists()


def test_only_system_writes_to_machine(tmp_path):
    result = run_scan("--only", "system", memory_dir=str(tmp_path))
    assert result.returncode == 0
    assert (tmp_path / "machine" / "system.json").exists()


def test_only_combined_keeps_count_controlled(tmp_path):
    # Explicitly requesting 6 scanners gives 6 files
    result = run_scan(
        "--only", "credentials,network,openclaw,current_context,environment,gstack",
        memory_dir=str(tmp_path),
    )
    assert result.returncode == 0
    machine_files = list((tmp_path / "machine").iterdir())
    assert len(machine_files) == 6
