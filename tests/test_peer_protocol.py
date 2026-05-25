"""Peer protocol contract tests.

Covers the external peer bundle layout, peer liveness states, MiniMax
readiness diagnostics, the memory-side orphan KB synthesis path, and a
regression guard that the canonical handoff script stayed untouched.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MEMORY_SCRIPTS = _REPO_ROOT / "core" / "skills" / "memory-oracle" / "scripts"
if str(_MEMORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SCRIPTS))

import scan_index


REPO_ROOT = _REPO_ROOT
PEER_SCRIPT_DIR = REPO_ROOT / "core" / "skills" / "clawseat-peer" / "scripts"
PEER_DELIVER = PEER_SCRIPT_DIR / "peer_deliver.py"
PEER_WATCHDOG = PEER_SCRIPT_DIR / "peer_watchdog.py"
MINIMAX_READINESS = PEER_SCRIPT_DIR / "minimax_readiness.py"
MEMORY_WRITE = REPO_ROOT / "core" / "skills" / "memory-oracle" / "scripts" / "memory_write.py"
COMPLETE_HANDOFF = REPO_ROOT / "core" / "skills" / "gstack-harness" / "scripts" / "complete_handoff.py"


def _script_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "AGENT_HOME": str(home),
        }
    )
    return env


def _run(script: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _touch_all_files(root: Path, age_seconds: int) -> None:
    stamp = time.time() - age_seconds
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (stamp, stamp))


def _parse_json(output: str) -> dict[str, object]:
    return json.loads(output.strip())


def _deliver_peer_bundle(
    tmp_path: Path,
    *,
    peer_id: str = "peer-195404",
    task_id: str = "DM-PEER-001",
    summary: str = "peer bundle delivered",
    status: str = "submitted",
    task_text: str | None = None,
    receipt_verdict: str = "SUBMITTED",
    receipt_notes: str = "",
    heartbeat_state: str = "progressing",
) -> tuple[dict[str, object], Path, Path]:
    env = _script_env(tmp_path)
    args = [
        "--project", "install",
        "--peer-id", peer_id,
        "--task-id", task_id,
        "--status", status,
        "--summary", summary,
        "--receipt-verdict", receipt_verdict,
        "--heartbeat-state", heartbeat_state,
    ]
    if receipt_notes:
        args.extend(["--receipt-notes", receipt_notes])
    if task_text is not None:
        args.extend(["--task-text", task_text])
    proc = _run(PEER_DELIVER, *args, env=env)
    assert proc.returncode == 0, proc.stderr
    payload = _parse_json(proc.stdout)
    peer_root = Path(payload["peer_root"])
    task_dir = Path(payload["task_dir"])
    return payload, peer_root, task_dir


def _run_watchdog(tmp_path: Path, peer_id: str, task_id: str) -> subprocess.CompletedProcess[str]:
    env = _script_env(tmp_path)
    return _run(
        PEER_WATCHDOG,
        "--project", "install",
        "--peer-id", peer_id,
        "--task-id", task_id,
        env=env,
    )


def test_peer_deliver_writes_expected_bundle_layout_and_frontmatter(tmp_path: Path) -> None:
    payload, peer_root, task_dir = _deliver_peer_bundle(tmp_path)

    assert payload["project"] == "install"
    assert payload["peer_id"] == "peer-195404"
    assert payload["task_id"] == "DM-PEER-001"
    assert payload["status"] == "submitted"
    assert Path(payload["delivery_md"]).exists()
    assert Path(payload["receipt_json"]).exists()
    assert Path(payload["heartbeat_json"]).exists()
    assert Path(payload["task_md"]).exists()
    assert peer_root == Path(payload["peer_root"])
    assert task_dir == peer_root / "tasks" / "DM-PEER-001"

    meta = json.loads((peer_root / "meta.json").read_text(encoding="utf-8"))
    heartbeat = json.loads((peer_root / "heartbeat.json").read_text(encoding="utf-8"))
    receipt = json.loads((task_dir / "receipt.json").read_text(encoding="utf-8"))
    delivery = scan_index.parse_frontmatter(task_dir / "DELIVERY.md")
    task = scan_index.parse_frontmatter(task_dir / "TASK.md")

    assert meta["peer_id"] == "peer-195404"
    assert meta["project"] == "install"
    assert meta["status"] == "active"
    assert heartbeat["state"] == "progressing"
    assert heartbeat["peer_id"] == "peer-195404"
    assert receipt == {
        "acknowledged_at": receipt["acknowledged_at"],
        "acknowledged_by": "peer-195404",
        "notes": "peer bundle delivered",
        "verdict": "SUBMITTED",
    }
    assert delivery is not None
    assert delivery["peer_id"] == "peer-195404"
    assert delivery["task_id"] == "DM-PEER-001"
    assert delivery["status"] == "submitted"
    assert delivery["summary"] == "peer bundle delivered"
    assert delivery["project"] == "install"
    assert task is not None
    assert task["peer_id"] == "peer-195404"
    assert task["task_id"] == "DM-PEER-001"
    assert "No task brief was supplied" in (task_dir / "TASK.md").read_text(encoding="utf-8")


def test_peer_deliver_honors_inline_task_text_and_receipt_overrides(tmp_path: Path) -> None:
    task_text = "raw task body that should stay in TASK.md"
    payload, peer_root, task_dir = _deliver_peer_bundle(
        tmp_path,
        task_id="DM-PEER-002",
        summary="inline task text delivered",
        task_text=task_text,
        receipt_verdict="PASS",
        receipt_notes="memory acknowledged",
    )

    assert payload["task_id"] == "DM-PEER-002"
    assert (task_dir / "TASK.md").read_text(encoding="utf-8") == task_text + "\n"
    receipt = json.loads((task_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["verdict"] == "PASS"
    assert receipt["notes"] == "memory acknowledged"
    assert scan_index.parse_frontmatter(task_dir / "DELIVERY.md")["summary"] == "inline task text delivered"
    assert peer_root == Path(payload["peer_root"])


def test_peer_watchdog_reports_progressing_for_fresh_bundle(tmp_path: Path) -> None:
    _, _, task_dir = _deliver_peer_bundle(tmp_path, task_id="DM-PEER-003")

    proc = _run_watchdog(tmp_path, "peer-195404", "DM-PEER-003")

    payload = _parse_json(proc.stdout)
    assert proc.returncode == 0
    assert payload["state"] == "progressing"
    assert payload["latest_path"] is not None
    assert Path(payload["latest_path"]).is_file()
    assert task_dir.exists()


def test_peer_watchdog_reports_idle_for_stale_bundle(tmp_path: Path) -> None:
    _, peer_root, _ = _deliver_peer_bundle(tmp_path, task_id="DM-PEER-004")
    _touch_all_files(peer_root, age_seconds=300)

    proc = _run_watchdog(tmp_path, "peer-195404", "DM-PEER-004")

    payload = _parse_json(proc.stdout)
    assert proc.returncode == 1
    assert payload["state"] == "idle"
    assert payload["latest_age_seconds"] is not None
    assert float(payload["latest_age_seconds"]) >= 299


def test_peer_watchdog_reports_stalled_for_old_bundle(tmp_path: Path) -> None:
    _, peer_root, _ = _deliver_peer_bundle(tmp_path, task_id="DM-PEER-005")
    _touch_all_files(peer_root, age_seconds=1200)

    proc = _run_watchdog(tmp_path, "peer-195404", "DM-PEER-005")

    payload = _parse_json(proc.stdout)
    assert proc.returncode == 2
    assert payload["state"] == "stalled"
    assert float(payload["latest_age_seconds"]) >= 1199


def test_minimax_readiness_reports_ready_without_token_leak(tmp_path: Path) -> None:
    secret_file = tmp_path / "minimax.env"
    secret_file.write_text(
        "MINIMAX_API_KEY=sk-test-123\n"
        "MINIMAX_BASE_URL=https://example.invalid\n"
        "SECONDARY=pk-test-456\n",
        encoding="utf-8",
    )

    proc = _run(
        MINIMAX_READINESS,
        "--path", str(secret_file),
        "--category", "api_key",
    )

    payload = _parse_json(proc.stdout)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0
    assert payload["readiness"] == "ready"
    assert payload["category"] == "api_key"
    assert re.search(r"(sk-|pk-|token=|key-)", combined) is None


def test_minimax_readiness_reports_missing_for_absent_path(tmp_path: Path) -> None:
    proc = _run(
        MINIMAX_READINESS,
        "--path", str(tmp_path / "does-not-exist.env"),
        "--category", "api_key",
    )

    payload = _parse_json(proc.stdout)
    assert proc.returncode == 0
    assert payload["readiness"] == "missing"
    assert payload["category"] == "api_key"


def test_minimax_readiness_reports_unreadable_for_bad_bytes(tmp_path: Path) -> None:
    unreadable = tmp_path / "credentials.toml"
    unreadable.write_bytes(b"\xff\xfe\x00")

    proc = _run(
        MINIMAX_READINESS,
        "--path", str(unreadable),
        "--category", "config",
    )

    payload = _parse_json(proc.stdout)
    assert proc.returncode == 0
    assert payload["readiness"] == "unreadable"
    assert payload["category"] == "config"


def test_peer_delivery_can_be_synthesized_into_orphan_finding(tmp_path: Path) -> None:
    raw_task_text = "raw peer task body that must not be copied verbatim"
    _, peer_root, task_dir = _deliver_peer_bundle(
        tmp_path,
        task_id="DM-PEER-006",
        summary="peer delivery ready for orphan synthesis",
        task_text=raw_task_text,
        receipt_verdict="PASS",
        receipt_notes="ACK",
    )

    delivery_path = task_dir / "DELIVERY.md"
    delivery_frontmatter = scan_index.parse_frontmatter(delivery_path)
    assert delivery_frontmatter is not None

    synthesized = tmp_path / "peer-orphan-note.md"
    synthesized.write_text(
        "\n".join(
            [
                "---",
                'schema_version: 1',
                'format: "markdown_note"',
                'id: "peer-synthesis-001"',
                'project: "install"',
                'kind: "finding"',
                'title: "Peer delivery synthesis"',
                'author: "memory"',
                f'ts: "{datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}"',
                'created_at: "2026-05-08T00:00:00Z"',
                'filename_stamp: "peer-synthesis"',
                'content_source: "file"',
                "---",
                "",
                f"Peer {delivery_frontmatter['peer_id']} delivered {delivery_frontmatter['task_id']}.",
                f"Summary: {delivery_frontmatter['summary']}",
                f"Receipt: {(task_dir / 'receipt.json').name}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    memory_root = tmp_path / "memory"
    proc = _run(
        MEMORY_WRITE,
        "--kind", "finding",
        "--project", "install",
        "--title", "Peer delivery synthesis",
        "--author", "memory",
        "--content-file", str(synthesized),
        "--memory-dir", str(memory_root),
    )

    note_path = Path(proc.stdout.strip())
    note_text = note_path.read_text(encoding="utf-8")
    note_frontmatter = scan_index.parse_frontmatter(note_path)

    assert proc.returncode == 0
    assert "/projects/install/finding/" in str(note_path)
    assert note_frontmatter is not None
    assert note_frontmatter["kind"] == "finding"
    assert note_frontmatter["author"] == "memory"
    assert "peer-195404" in note_text
    assert "peer delivery ready for orphan synthesis" in note_text
    assert raw_task_text not in note_text


# v3 spec §10 item 6 — strict ordered-hunk model of the intentional diff to
# core/skills/gstack-harness/scripts/complete_handoff.py. The guard parses
# the actual `git diff` into an ordered list of hunks and asserts EXACT
# equality (hunk count, anchor function, ordered sequence of removed+added
# lines per hunk). Approach B from reviewer follow-up: reject extra hunks,
# duplicate allowed lines in unrelated locations, reordered additions, and
# missing intentional content.
#
# Each entry: (anchor_substring_in_hunk_header, [removed_lines_in_order],
# [added_lines_in_order]). Whitespace-significant.
_COMPLETE_HANDOFF_INTENTIONAL_HUNKS: "list[tuple[str, list[str], list[str]]]" = [
    # Hunk 1: soft-fail rewrite of _validate_completion_receipt mismatch branch
    (
        "def _validate_completion_receipt(",
        [
            "        raise SystemExit(",
            "            \"branch_base mismatch: receipt base does not match dispatch expected_base_sha.\"",
            "            \" Rebase the feature branch onto the current main and retry.\"",
        ],
        [
            "        # v3 spec §10 item 6 (post-DO): soft-fail instead of SystemExit so the",
            "        # canonical receipt path keeps flowing during base drift. The",
            "        # `_annotate_lineage_status` step earlier records lineage_status; here",
            "        # we ensure it reflects divergence even if upstream missed it.",
            "        # Downstream consumers (memory) recover via the PASS_NEEDS_INTEGRATION",
            "        # three-lane handler (spec §C / DO spec). Hard-failing here previously",
            "        # blocked planner→memory fan-in (AL-503 finding).",
            "        print(",
            "            \"warn: branch_base mismatch — \"",
            "            f\"receipt={actual_base!r} vs dispatch expected_base_sha={expected_base!r}; \"",
            "            f\"lineage_status={receipt.get('lineage_status', '?')!r}; \"",
            "            \"receipt still emitted, memory PASS_NEEDS_INTEGRATION handler decides recovery\",",
            "            file=sys.stderr,",
            "        if receipt.get(\"lineage_status\") != \"divergent\":",
            "            receipt[\"lineage_status\"] = \"divergent\"",
            "            receipt[\"head_contains_commit\"] = False",
        ],
    ),
    # Hunk 2: final_lineage_status threading at receipt persistence
    (
        "def main() -> int:",
        [
            "    receipt[\"head_contains_commit\"] = head_contains_commit",
            "    receipt[\"lineage_status\"] = lineage_status",
        ],
        [
            "    # v3 spec §10 item 6: `_validate_completion_receipt` may have downgraded",
            "    # lineage_status to 'divergent' on branch_base mismatch. Preserve that",
            "    # downgrade by reading the dict (not the cached local value from",
            "    # `_annotate_lineage_status` at line ~1018).",
            "    final_lineage_status = str(receipt.get(\"lineage_status\") or lineage_status)",
            "    final_head_contains_commit = bool(receipt.get(\"head_contains_commit\", head_contains_commit))",
            "    receipt[\"head_contains_commit\"] = final_head_contains_commit",
            "    receipt[\"lineage_status\"] = final_lineage_status",
        ],
    ),
    # Hunk 3: PASS_NEEDS_INTEGRATION emit gate uses final_lineage_status
    (
        "def main() -> int:",
        [
            "    if lineage_status == \"divergent\" and reported_commit and args.target != \"memory\":",
        ],
        [
            "    # v3 spec §10 item 6 (audit fix 2): use final_lineage_status so the",
            "    # soft-failed branch_base-mismatch path triggers PASS_NEEDS_INTEGRATION",
            "    # notification to memory. Cached `lineage_status` from line ~1018",
            "    # reflects only the merge-base ancestry check, not the subsequent soft",
            "    # downgrade in `_validate_completion_receipt`.",
            "    if final_lineage_status == \"divergent\" and reported_commit and args.target != \"memory\":",
        ],
    ),
    # Hunk 4: notify-message append uses final_lineage_status
    (
        "def main() -> int:",
        [
            "        if lineage_status == \"divergent\" and reported_commit:",
        ],
        [
            "        if final_lineage_status == \"divergent\" and reported_commit:",
        ],
    ),
]


def _parse_diff_hunks(diff_text: str, target_substr: str) -> "list[dict]":
    """Parse unified diff into ordered hunks targeting a specific file.

    Returns list of {anchor: str, removed: [str...], added: [str...]} dicts
    in the order they appear in the diff. Skips other files, hunk metadata,
    and context lines.
    """
    hunks: list[dict] = []
    in_target = False
    cur: dict | None = None
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git"):
            in_target = target_substr in raw_line
            cur = None
            continue
        if not in_target:
            continue
        if raw_line.startswith("+++") or raw_line.startswith("---"):
            continue
        if raw_line.startswith("@@"):
            # `@@ -A,B +C,D @@ <anchor>`
            anchor = raw_line.split("@@", 2)[-1].strip()
            cur = {"anchor": anchor, "removed": [], "added": []}
            hunks.append(cur)
            continue
        if cur is None:
            continue
        if raw_line.startswith("+"):
            cur["added"].append(raw_line[1:].rstrip("\n"))
        elif raw_line.startswith("-"):
            cur["removed"].append(raw_line[1:].rstrip("\n"))
        # context lines (leading space) ignored
    return hunks


def classify_complete_handoff_diff(
    diff_text: str,
    expected: "list[tuple[str, list[str], list[str]]]" = _COMPLETE_HANDOFF_INTENTIONAL_HUNKS,
) -> "list[str]":
    """Inspect a unified `git diff` payload and return violations.

    Hunk-level strict comparison (Blocker 1 fix):
    - Number of hunks must match `expected` exactly
    - For each hunk i: anchor must contain the expected substring
    - Each hunk's `removed` list must equal expected[i][1] in order (exact)
    - Each hunk's `added` list must equal expected[i][2] in order (exact)

    This rejects:
    - Extra hunks anywhere (length mismatch)
    - Duplicate allowed lines in unrelated locations (sequence/count mismatch)
    - Reordered additions (sequence mismatch)
    - Missing intentional content (sequence mismatch)
    - Unrelated added/removed lines (sequence mismatch)
    """
    actual = _parse_diff_hunks(diff_text, "complete_handoff.py")
    violations: list[str] = []

    if not actual:
        # Empty diff or no hunks targeting complete_handoff.py — the caller
        # decides whether that's a violation (e.g., the wrapper test handles
        # the "clean working tree after commit" fast path).
        return violations

    if len(actual) != len(expected):
        violations.append(
            f"hunk count mismatch: expected {len(expected)} intentional hunks, "
            f"got {len(actual)} in diff"
        )
        # Continue comparing what we can so violations list is more useful
    for idx in range(min(len(actual), len(expected))):
        exp_anchor, exp_removed, exp_added = expected[idx]
        h = actual[idx]
        if exp_anchor not in h["anchor"]:
            violations.append(
                f"hunk #{idx} anchor mismatch: expected substring {exp_anchor!r}, "
                f"got {h['anchor']!r}"
            )
        if h["removed"] != exp_removed:
            violations.append(
                f"hunk #{idx} removed lines mismatch: expected {exp_removed!r}, "
                f"got {h['removed']!r}"
            )
        if h["added"] != exp_added:
            violations.append(
                f"hunk #{idx} added lines mismatch: expected {exp_added!r}, "
                f"got {h['added']!r}"
            )
    return violations


def test_complete_handoff_script_is_unchanged() -> None:
    """Guard against silent edits to complete_handoff.py.

    v3 spec §10 item 6 (DO spec roll-up) intentionally edits branch_base
    handling and lineage propagation. The exact hunks are enumerated in
    `_COMPLETE_HANDOFF_INTENTIONAL_HUNKS` (ordered: anchor + removed seq +
    added seq). Hunk-level strict comparison rejects extra hunks, duplicate
    allowed lines in unrelated locations, reordered additions, and any line
    outside the intentional surfaces.

    Once committed and the working tree returns to clean, the diff is empty
    and this test takes the fast path.
    """
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "diff",
            "--",
            "core/skills/gstack-harness/scripts/complete_handoff.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"git diff failed: {proc.stderr}"
    assert proc.stderr.strip() == ""

    diff_text = proc.stdout
    if not diff_text.strip():
        return  # clean working tree — historical no-touch assertion holds

    violations = classify_complete_handoff_diff(diff_text)
    assert not violations, (
        "complete_handoff.py has uncommitted changes outside the v3 spec §10 "
        "intentional hunks:\n  - "
        + "\n  - ".join(violations)
        + "\nEither revert the unrelated hunks, or extend "
        "_COMPLETE_HANDOFF_INTENTIONAL_HUNKS with the new spec reference."
    )


# ---------- Negative tests for hunk-level guard (Blocker 1 reviewer follow-up) ----------


def _hunk_to_diff(anchor: str, removed: list, added: list) -> str:
    """Synthesize a unified-diff snippet for one hunk in complete_handoff.py."""
    lines = [
        "diff --git a/core/skills/gstack-harness/scripts/complete_handoff.py b/core/skills/gstack-harness/scripts/complete_handoff.py",
        "--- a/core/skills/gstack-harness/scripts/complete_handoff.py",
        "+++ b/core/skills/gstack-harness/scripts/complete_handoff.py",
        f"@@ -1,{len(removed)} +1,{len(added)} @@ {anchor}",
    ]
    for r in removed:
        lines.append("-" + r)
    for a in added:
        lines.append("+" + a)
    return "\n".join(lines) + "\n"


def _all_intentional_diff() -> str:
    """Synthesize a complete diff with all 4 intentional hunks in order."""
    parts = []
    for anchor, removed, added in _COMPLETE_HANDOFF_INTENTIONAL_HUNKS:
        parts.append(_hunk_to_diff(anchor, removed, added))
    # Concat — but diff-git header only appears once for a multi-hunk single file
    out = [
        "diff --git a/core/skills/gstack-harness/scripts/complete_handoff.py b/core/skills/gstack-harness/scripts/complete_handoff.py",
        "--- a/core/skills/gstack-harness/scripts/complete_handoff.py",
        "+++ b/core/skills/gstack-harness/scripts/complete_handoff.py",
    ]
    for anchor, removed, added in _COMPLETE_HANDOFF_INTENTIONAL_HUNKS:
        out.append(f"@@ -1,{len(removed)} +1,{len(added)} @@ {anchor}")
        for r in removed:
            out.append("-" + r)
        for a in added:
            out.append("+" + a)
    return "\n".join(out) + "\n"


def test_classify_passes_when_diff_is_empty() -> None:
    assert classify_complete_handoff_diff("") == []


def test_classify_accepts_exact_intentional_diff() -> None:
    """Synthetic diff of exactly the 4 intentional hunks must pass."""
    diff = _all_intentional_diff()
    assert classify_complete_handoff_diff(diff) == []


def test_classify_rejects_extra_hunk_with_allowed_added_line() -> None:
    """Blocker 1 repro: an extra unrelated hunk that adds `+        print(`
    (allowed inside hunk 1) must FAIL — the previous line-set guard would
    have accepted it because `print(` appeared in the allowlist.
    """
    diff = _all_intentional_diff()
    # Append a 5th hunk in an unrelated location with `print(` add only
    extra = [
        "@@ -1,1 +1,1 @@ def unrelated_helper():",
        "-        # unrelated original",
        "+        print(",
    ]
    diff += "\n".join(extra) + "\n"
    violations = classify_complete_handoff_diff(diff)
    assert violations, "extra hunk with allowed-line `print(` must trip guard"
    assert any("hunk count mismatch" in v for v in violations), violations


def test_classify_rejects_extra_hunk_with_allowed_removed_line() -> None:
    """Blocker 1 repro: an extra hunk removing `-        raise SystemExit(`
    (allowed inside hunk 1) must FAIL despite line-content being allowed.
    """
    diff = _all_intentional_diff()
    extra = [
        "@@ -1,1 +1,1 @@ def some_other_function():",
        "-        raise SystemExit(",
        "+        # silently swallowed",
    ]
    diff += "\n".join(extra) + "\n"
    violations = classify_complete_handoff_diff(diff)
    assert violations
    assert any("hunk count mismatch" in v for v in violations), violations


def test_classify_rejects_duplicate_allowed_line_within_hunk() -> None:
    """Duplicating an allowed added line inside hunk 1 changes the sequence
    length and must FAIL — line-set guard would have missed this."""
    anchor, removed, added = _COMPLETE_HANDOFF_INTENTIONAL_HUNKS[0]
    # Duplicate the first allowed added line
    duped_added = [added[0]] + list(added)
    diff = "\n".join([
        "diff --git a/core/skills/gstack-harness/scripts/complete_handoff.py b/core/skills/gstack-harness/scripts/complete_handoff.py",
        "--- a/core/skills/gstack-harness/scripts/complete_handoff.py",
        "+++ b/core/skills/gstack-harness/scripts/complete_handoff.py",
        f"@@ -1,{len(removed)} +1,{len(duped_added)} @@ {anchor}",
    ])
    diff += "\n"
    for r in removed:
        diff += "-" + r + "\n"
    for a in duped_added:
        diff += "+" + a + "\n"
    # Add remaining 3 hunks unchanged
    for h_anchor, h_removed, h_added in _COMPLETE_HANDOFF_INTENTIONAL_HUNKS[1:]:
        diff += f"@@ -1,{len(h_removed)} +1,{len(h_added)} @@ {h_anchor}\n"
        for r in h_removed:
            diff += "-" + r + "\n"
        for a in h_added:
            diff += "+" + a + "\n"
    violations = classify_complete_handoff_diff(diff)
    assert violations
    assert any("added lines mismatch" in v for v in violations), violations


def test_classify_rejects_reordered_added_lines() -> None:
    """Swapping order of added lines within hunk 1 must FAIL — content set is
    identical but sequence differs."""
    anchor, removed, added = _COMPLETE_HANDOFF_INTENTIONAL_HUNKS[0]
    reordered = list(reversed(added))
    diff = "\n".join([
        "diff --git a/core/skills/gstack-harness/scripts/complete_handoff.py b/core/skills/gstack-harness/scripts/complete_handoff.py",
        "--- a/core/skills/gstack-harness/scripts/complete_handoff.py",
        "+++ b/core/skills/gstack-harness/scripts/complete_handoff.py",
        f"@@ -1,{len(removed)} +1,{len(reordered)} @@ {anchor}",
    ])
    diff += "\n"
    for r in removed:
        diff += "-" + r + "\n"
    for a in reordered:
        diff += "+" + a + "\n"
    # Add remaining 3 hunks unchanged
    for h_anchor, h_removed, h_added in _COMPLETE_HANDOFF_INTENTIONAL_HUNKS[1:]:
        diff += f"@@ -1,{len(h_removed)} +1,{len(h_added)} @@ {h_anchor}\n"
        for r in h_removed:
            diff += "-" + r + "\n"
        for a in h_added:
            diff += "+" + a + "\n"
    violations = classify_complete_handoff_diff(diff)
    assert violations
    assert any("added lines mismatch" in v for v in violations), violations


def test_classify_rejects_missing_hunk() -> None:
    """Dropping one of the 4 intentional hunks must FAIL — guard requires all
    intentional surfaces to be present in order."""
    parts = [
        "diff --git a/core/skills/gstack-harness/scripts/complete_handoff.py b/core/skills/gstack-harness/scripts/complete_handoff.py",
        "--- a/core/skills/gstack-harness/scripts/complete_handoff.py",
        "+++ b/core/skills/gstack-harness/scripts/complete_handoff.py",
    ]
    # Skip hunk index 2 (PASS_NEEDS_INTEGRATION emit gate)
    for idx, (anchor, removed, added) in enumerate(_COMPLETE_HANDOFF_INTENTIONAL_HUNKS):
        if idx == 2:
            continue
        parts.append(f"@@ -1,{len(removed)} +1,{len(added)} @@ {anchor}")
        for r in removed:
            parts.append("-" + r)
        for a in added:
            parts.append("+" + a)
    diff = "\n".join(parts) + "\n"
    violations = classify_complete_handoff_diff(diff)
    assert violations
    assert any("hunk count mismatch" in v for v in violations), violations


def test_classify_rejects_wrong_anchor() -> None:
    """If anchor function doesn't match (e.g., refactoring moved the hunk to
    a different function), the guard must surface that."""
    anchor, removed, added = _COMPLETE_HANDOFF_INTENTIONAL_HUNKS[0]
    parts = [
        "diff --git a/core/skills/gstack-harness/scripts/complete_handoff.py b/core/skills/gstack-harness/scripts/complete_handoff.py",
        "--- a/core/skills/gstack-harness/scripts/complete_handoff.py",
        "+++ b/core/skills/gstack-harness/scripts/complete_handoff.py",
        f"@@ -1,{len(removed)} +1,{len(added)} @@ def WRONG_FUNCTION():",
    ]
    for r in removed:
        parts.append("-" + r)
    for a in added:
        parts.append("+" + a)
    for h_anchor, h_removed, h_added in _COMPLETE_HANDOFF_INTENTIONAL_HUNKS[1:]:
        parts.append(f"@@ -1,{len(h_removed)} +1,{len(h_added)} @@ {h_anchor}")
        for r in h_removed:
            parts.append("-" + r)
        for a in h_added:
            parts.append("+" + a)
    diff = "\n".join(parts) + "\n"
    violations = classify_complete_handoff_diff(diff)
    assert any("anchor mismatch" in v for v in violations), violations


def test_classify_ignores_other_files_in_diff() -> None:
    """Multi-file diffs: only complete_handoff.py is scrutinized."""
    diff = _all_intentional_diff()
    diff = (
        "diff --git a/some/other/file.py b/some/other/file.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old line in other file\n"
        "+new line in other file\n"
        + diff
    )
    assert classify_complete_handoff_diff(diff) == []
