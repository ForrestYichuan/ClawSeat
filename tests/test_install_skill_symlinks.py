from __future__ import annotations

import os
import importlib.util
from pathlib import Path

import pytest

_SETUP_HELPERS = Path(__file__).with_name("test_install_privacy_setup.py")
_setup_spec = importlib.util.spec_from_file_location("_h3_install_privacy_setup", _SETUP_HELPERS)
assert _setup_spec is not None
assert _setup_spec.loader is not None
_setup = importlib.util.module_from_spec(_setup_spec)
_setup_spec.loader.exec_module(_setup)

_prepare_h3_fake_root = _setup._prepare_h3_fake_root
_run_install = _setup._run_install


def _assert_agents_links(root: Path, home: Path, skills: tuple[str, ...]) -> None:
    for skill in skills:
        link = home / ".agents" / "skills" / skill
        assert link.is_symlink()
        assert os.readlink(link) == str(root / "core" / "skills" / skill)


def _assert_tool_mirrors_agents(home: Path, tool: str, skills: tuple[str, ...]) -> None:
    for skill in skills:
        link = home / f".{tool}" / "skills" / skill
        assert link.is_symlink()
        assert os.readlink(link) == str(home / ".agents" / "skills" / skill)


@pytest.fixture(scope="module")
def default_skill_install(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root, home, py_stubs = _prepare_h3_fake_root(tmp_path_factory.mktemp("h3skills-default"))
    result = _run_install(root, home, py_stubs, project="h3skills")
    assert result.returncode == 0, result.stderr
    return root, home


def test_install_creates_new_skill_symlinks(default_skill_install: tuple[Path, Path]) -> None:
    root, home = default_skill_install

    core_skills = ("clawseat-memory", "clawseat-decision-escalation")
    extended_skills = ("clawseat-koder", "clawseat-privacy", "clawseat-memory-reporting")
    all_skills = (*core_skills, *extended_skills)

    _assert_agents_links(root, home, all_skills)

    for tool in ("claude", "codex"):
        _assert_tool_mirrors_agents(home, tool, all_skills)
    assert not (home / ".gemini" / "skills" / "clawseat-memory").exists()


def test_install_skill_symlinks_are_idempotent(tmp_path: Path) -> None:
    root, home, py_stubs = _prepare_h3_fake_root(tmp_path)
    first = _run_install(root, home, py_stubs, project="h3idem")
    second = _run_install(root, home, py_stubs, project="h3idem")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert (home / ".agents" / "skills" / "clawseat-privacy").is_symlink()
    assert not (home / ".gemini" / "skills" / "clawseat-memory-reporting").exists()
    assert (home / ".codex" / "skills" / "clawseat-memory-reporting").is_symlink()


def test_install_leaves_existing_clawseat_memory_symlink(tmp_path: Path) -> None:
    root, home, py_stubs = _prepare_h3_fake_root(tmp_path)
    skills_home = home / ".agents" / "skills"
    skills_home.mkdir(parents=True, exist_ok=True)
    memory_link = skills_home / "clawseat-memory"
    memory_target = root / "core" / "skills" / "clawseat-memory"
    memory_link.symlink_to(memory_target)

    result = _run_install(root, home, py_stubs, project="h3memorylink")
    assert result.returncode == 0, result.stderr
    assert memory_link.is_symlink()
    assert os.readlink(memory_link) == str(memory_target)
    for tool in ("claude", "codex"):
        assert os.readlink(home / f".{tool}" / "skills" / "clawseat-memory") == str(memory_link)
    assert not (home / ".gemini" / "skills" / "clawseat-memory").exists()


def test_install_creates_codex_skill_dir_but_skips_gemini_symlinks(
    default_skill_install: tuple[Path, Path],
) -> None:
    _root, home = default_skill_install
    skills_home = home / ".codex" / "skills"
    assert skills_home.is_dir()
    assert (skills_home / "clawseat-memory").is_symlink()
    assert os.readlink(skills_home / "clawseat-memory") == str(
        home / ".agents" / "skills" / "clawseat-memory"
    )
    assert not (home / ".gemini" / "skills" / "clawseat-memory").exists()


def test_load_all_skills_restores_extended_tier_for_small_tool_dirs(tmp_path: Path) -> None:
    root, home, py_stubs = _prepare_h3_fake_root(tmp_path)
    result = _run_install(root, home, py_stubs, project="h3loadall", load_all_skills=True)
    assert result.returncode == 0, result.stderr

    for skills_home in (home / ".claude" / "skills", home / ".codex" / "skills"):
        for skill in ("clawseat-koder", "clawseat-privacy", "clawseat-memory-reporting"):
            link = skills_home / skill
            assert link.is_symlink()
            assert os.readlink(link) == str(home / ".agents" / "skills" / skill)
    assert not (home / ".gemini" / "skills" / "clawseat-memory-reporting").exists()
