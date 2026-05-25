from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "scripts" / "install.sh"
INSTALL_MD = REPO / "docs" / "INSTALL.md"
INSTALL_ZH = REPO / "docs" / "INSTALL.zh-CN.md"
DIE_RE = re.compile(r"\bdie\s+\d+\s+([A-Z][A-Z0-9_]+)\b")


def _install_sh_codes() -> set[str]:
    return set(DIE_RE.findall(INSTALL.read_text(encoding="utf-8")))


def test_install_md_documents_current_install_sh_die_codes() -> None:
    codes = _install_sh_codes()
    assert codes == {
        "COMMAND_FAILED",
        "INVALID_FLAGS",
        "INVALID_MEMORY_MODEL",
        "INVALID_MEMORY_TOOL",
        "INVALID_MODE",
        "INVALID_PROJECT",
        "INVALID_REPO_ROOT",
        "INVALID_TEMPLATE",
        "MISSING_SCRIPT",
        "UNKNOWN_FLAG",
    }

    for doc_path in (INSTALL_MD, INSTALL_ZH):
        text = doc_path.read_text(encoding="utf-8")
        missing = sorted(code for code in codes if code not in text)
        assert not missing, f"{doc_path.relative_to(REPO)} missing: {missing}"


def test_install_provider_docs_describe_dynamic_candidates_not_fixed_menu_numbers() -> None:
    for doc_path in (INSTALL_MD, INSTALL_ZH):
        text = doc_path.read_text(encoding="utf-8")
        assert "dynamically" in text or "动态检测" in text
        assert "| 1 | Claude memory" not in text
        assert "`oauth_token`" in text
        assert "`custom_api`" in text
