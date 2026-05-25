from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_test_suite_audit_json_reports_legacy_inventory() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/test-suite-audit.py", "--json"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["files_scanned"] > 300
    assert payload["legacy_hit_files"] >= 80
    assert payload["categories"]["migration_or_compat"] >= 1
    assert payload["categories"]["removal_guard"] >= 1

    by_path = {entry["path"]: entry for entry in payload["entries"]}
    assert by_path["tests/test_project_binding_schema_v3.py"]["category"] == "migration_or_compat"
    assert by_path["tests/test_no_legacy_v07_migration.py"]["category"] == "removal_guard"


def test_test_suite_audit_markdown_has_summary() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/test-suite-audit.py"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "# Test Suite Legacy Audit" in result.stdout
    assert "Files with legacy/compat text:" in result.stdout
    assert "## migration_or_compat" in result.stdout
