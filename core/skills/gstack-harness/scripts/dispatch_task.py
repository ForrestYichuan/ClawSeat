#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add core/lib to path so seat_resolver can be imported
_scripts_dir = Path(__file__).parent.resolve()
_core_lib = _scripts_dir.parent.parent.parent / "lib"
if str(_core_lib) not in sys.path:
    sys.path.insert(0, str(_core_lib))
from real_home import real_user_home

from _common import (
    _should_announce_planner_event,
    _try_announce_planner_event,
    add_notify_args,
    append_status_dispatch_event,
    append_task_to_queue,
    assert_target_not_memory,
    broadcast_feishu_group_message,
    build_notify_message,
    load_json,
    load_profile,
    legacy_feishu_group_broadcast_enabled,
    normalize_role,
    notify,
    require_success,
    resolve_notify,
    stable_dispatch_nonce,
    upsert_tasks_row,
    utc_now_iso,
    write_json,
    write_todo,
    sanitize_name,
)

from seat_resolver import resolve_seat_from_profile


def _is_task_already_queued(todo_path: Path, task_id: str) -> bool:
    """Return True if task_id appears under a [pending] or [queued] header in todo_path."""
    if not todo_path.exists():
        return False
    content = todo_path.read_text(encoding="utf-8")
    return bool(re.search(
        rf'^## \[(pending|queued)\]\s+{re.escape(task_id)}\b',
        content,
        re.MULTILINE,
    ))


def _profile_is_v3_multi_team(profile: object) -> bool:
    """Return True for v3 multi-team profiles.

    Kept local to dispatch_task.py so the legacy harness loader does not need
    to grow a new public schema field just to enforce this guard.
    """
    try:
        text = Path(getattr(profile, "profile_path")).read_text(encoding="utf-8")
    except Exception:
        return False
    return "\n[teams]\n" in text or 'team_structure = "multi"' in text


def _reject_v3_planner_target_dispatch(
    *,
    profile: object,
    source: str,
    target: str,
    source_role: str,
    target_role: str,
) -> bool:
    """Block v3 planner work that bypasses the brief queue.

    In v3 multi-team projects, memory creates planner work by appending to the
    team's queue. dispatch_task.py writes handoff/TODO state instead, which the
    v3 planner-status path does not treat as queued planner work.
    """
    planner_roles = {"planner", "planner-dispatcher"}
    if not (_profile_is_v3_multi_team(profile) and target_role in planner_roles):
        return False
    print(
        "error: v3 planner-target dispatch must use agent_admin.py brief queue; "
        "this blocks memory->planner split-brain and generic planner aliases. "
        "dispatch_task.py is for planner-owned downstream handoffs or legacy v2.\n"
        f"hint: python3 {getattr(profile, 'agent_admin', 'core/scripts/agent_admin.py')} "
        f"brief queue --project {getattr(profile, 'project_name', '<project>')} "
        "--team <team> --task-id <id> --objective '<objective>'",
        file=sys.stderr,
    )
    return True



# ── Intent → gstack skill mapping ──────────────────────────────────────
#
# Users describe needs in natural language ("做个工程审查", "推上去", "想大一点").
# They should not have to remember gstack skill trigger phrases — that is
# koder's job per SOUL.md §5 (the "代用户激活 gstack skill" hard rule).
#
# This map lets koder pass --intent <key> and the dispatch prepends the
# canonical trigger phrase to the objective AND adds the SKILL.md path to
# --skill-refs, so the downstream planner Claude Code runtime picks up the
# right skill without guesswork.
#
# To add a new intent:
#   1. Confirm the gstack SKILL.md trigger phrase in its frontmatter.
#   2. Append an entry here.
#   3. Add a row to TOOLS/dispatch.md's intent table in init_koder.py.
#   4. Add a test row in tests/test_dispatch_intent.py.
#
# All four MUST move together — the SKILL.md text is the source of truth.

def _resolve_gstack_skills_root() -> str:
    env = (os.environ.get("GSTACK_SKILLS_ROOT") or "").strip()
    if env:
        expanded = Path(env).expanduser()
        if not expanded.is_absolute():
            # Refuse relative paths — they silently resolve against cwd,
            # which produces mystery "not found" errors at dispatch time.
            # Keep the pattern identical to skill_registry._resolve_gstack_skills_root.
            sys.stderr.write(
                f"warning: GSTACK_SKILLS_ROOT={env!r} is not absolute; "
                f"ignoring and falling back to ~/.gstack/repos/gstack/.agents/skills.\n"
                f"         Set it to an absolute path like "
                f"{Path(env).expanduser().resolve()} to take effect.\n"
            )
        else:
            return str(expanded)
    return str(real_user_home() / ".gstack" / "repos" / "gstack" / ".agents" / "skills")


_GSTACK_SKILLS_ROOT = _resolve_gstack_skills_root()
DO_MERGE_AT = datetime.fromisoformat("2026-05-09T14:55:53+08:00")
LINEAGE_GRANDFATHER_CUTOFF = DO_MERGE_AT + timedelta(weeks=6)


def _git_main_tip(repo_root: Path | str | None) -> str | None:
    """Return the tip SHA for a known main remote reference."""
    if not repo_root:
        return None
    root = Path(repo_root)
    if not root.exists():
        print(f"warn: repo_root {root} does not exist; skip expected_base_sha", file=sys.stderr)
        return None
    for ref in ("clawseat/main", "origin/main"):
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", ref],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            msg = (exc.stderr or exc.stdout or "").strip()
            print(
                f"warn: cannot read {ref} in {root}: {msg or 'unknown git error'}",
                file=sys.stderr,
            )
            continue
        except FileNotFoundError:
            print("warn: git not found; skip expected_base_sha", file=sys.stderr)
            return None
        sha = result.stdout.strip()
        if sha:
            return sha
    print(
        "warn: missing refs clawseat/main and origin/main; skip expected_base_sha",
        file=sys.stderr,
    )
    return None


def _git_head_tip(repo_root: Path | str | None) -> str | None:
    """Return the current HEAD SHA for a repository, if available."""
    if not repo_root:
        return None
    root = Path(repo_root)
    if not root.exists():
        print(f"warn: repo_root {root} does not exist; skip lineage receipt fields", file=sys.stderr)
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or f"exit {exc.returncode}"
        print(
            f"warn: cannot read HEAD in {root}; skip lineage receipt fields: {detail}",
            file=sys.stderr,
        )
        return None
    except FileNotFoundError:
        print("warn: git not found; skip lineage receipt fields", file=sys.stderr)
        return None
    sha = result.stdout.strip()
    return sha or None


def _git_merge_base_is_ancestor(repo_root: Path | str | None, reported_commit: str) -> bool | None:
    if not repo_root:
        return None
    root = Path(repo_root)
    if not root.exists():
        print(f"warn: repo_root {root} does not exist; skip lineage guard", file=sys.stderr)
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", reported_commit, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("warn: git not found; skip lineage guard", file=sys.stderr)
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = (result.stderr or result.stdout or "").strip()
    print(
        "warn: merge-base --is-ancestor failed for "
        f"commit={reported_commit!r} in {root}: {detail or 'unknown git error'}",
        file=sys.stderr,
    )
    return None


def _populate_lineage_receipt_fields(
    *,
    repo_root: Path | str | None,
    expected_base_sha: str | None,
    source_role: str,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "builder_commit": None,
        "memory_commit": None,
        "head_contains_commit": False,
        "lineage_status": "unknown",
    }
    builder_commit = expected_base_sha.strip() if isinstance(expected_base_sha, str) and expected_base_sha.strip() else None
    if builder_commit:
        fields["builder_commit"] = builder_commit
        contains = _git_merge_base_is_ancestor(repo_root, builder_commit)
        if contains is True:
            fields["head_contains_commit"] = True
            fields["lineage_status"] = "in-lineage"
        elif contains is False:
            fields["head_contains_commit"] = False
            fields["lineage_status"] = "divergent"
        else:
            fields["head_contains_commit"] = False
            fields["lineage_status"] = "unknown"
    if source_role == "memory":
        fields["memory_commit"] = _git_head_tip(repo_root)
    return fields


def _advance_builder_task_branch(
    repo_root: Path | str | None,
    branch: str | None,
    base_sha: str | None,
) -> bool:
    """Advance a builder task branch to the requested base SHA.

    This is a best-effort repair path. If the branch already points at the
    requested base, it is left alone. If the repo or git is unavailable, the
    dispatch still proceeds and the caller can surface the drift later.
    """
    if not repo_root or not branch or not base_sha:
        return False
    root = Path(repo_root)
    if not root.exists():
        print(f"warn: repo_root {root} does not exist; skip builder branch ref advance", file=sys.stderr)
        return False
    checked_out_ref = f"branch refs/heads/{branch}"
    try:
        worktree_list = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or f"exit {exc.returncode}"
        print(
            f"warn: failed to inspect worktrees for {branch}; skip builder branch ref advance: {detail}",
            file=sys.stderr,
        )
        return False
    except FileNotFoundError:
        print("warn: git not found; skip builder branch ref advance", file=sys.stderr)
        return False
    if checked_out_ref in worktree_list:
        print(
            f"warn: builder branch {branch} is currently checked out; skip branch ref advance",
            file=sys.stderr,
        )
        return False
    try:
        current = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", branch],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        current = ""
    if current == base_sha:
        return True
    try:
        subprocess.run(
            ["git", "-C", str(root), "update-ref", f"refs/heads/{branch}", base_sha],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or f"exit {exc.returncode}"
        print(
            f"warn: failed to advance builder branch ref {branch} -> {base_sha}: {detail}",
            file=sys.stderr,
        )
        return False
    return True


def _validate_user_summary(
    user_summary: str | None,
    *,
    dispatched_at: datetime,
    task_id: str,
) -> None:
    if user_summary is not None and not user_summary.strip():
        raise SystemExit("user_summary must not be empty")
    if user_summary is None and dispatched_at >= LINEAGE_GRANDFATHER_CUTOFF:
        raise SystemExit(
            "dispatch receipt missing required user_summary after grandfather cutoff: "
            f"task_id={task_id!r}"
        )
    if user_summary is None:
        print(
            "warn: deprecated dispatch receipt format for "
            f"task_id={task_id!r}; missing user_summary; "
            f"grandfather until {LINEAGE_GRANDFATHER_CUTOFF.isoformat()}",
            file=sys.stderr,
        )


def _dispatch_lock_metadata(
    *,
    task_id: str,
    target_role: str,
    expected_branch: str | None,
    expected_worktree: str | None,
) -> dict[str, str]:
    if normalize_role(target_role) != "builder" and not expected_branch and not expected_worktree:
        return {}
    fields: dict[str, str] = {}
    branch = expected_branch or (
        f"feat/{task_id}" if normalize_role(target_role) == "builder" else None
    )
    worktree = expected_worktree or (
        f"/tmp/{task_id}-wt" if normalize_role(target_role) == "builder" else None
    )
    if branch:
        fields["expected_branch"] = branch
    if worktree:
        fields["expected_worktree_path"] = worktree
    return fields


def _expanded_handoff_dir(profile: object) -> Path:
    text = str(getattr(profile, "handoff_dir")).strip()
    return Path(os.path.expandvars(text)).expanduser().resolve()


def _expanded_tasks_dir(profile: object) -> Path:
    text = str(getattr(profile, "tasks_root")).strip()
    return Path(os.path.expandvars(text)).expanduser().resolve()


def _builder_outstanding_task(todo_path: Path) -> str | None:
    if not todo_path.exists():
        return None
    match = re.search(r"^## \[(pending|queued)\] (\S+)", todo_path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(2) if match else None


def _finding_hypothesis_counter(profile: object, *, target: str, finding_id: str) -> int:
    handoff_dir = _expanded_handoff_dir(profile)
    count = 0
    for path in handoff_dir.glob(f"*__*__{sanitize_name(target)}.json"):
        if not path.is_file():
            continue
        payload = load_json(path)
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") != "dispatch":
            continue
        if payload.get("target") != target:
            continue
        if payload.get("finding_id") != finding_id:
            continue
        count += 1
    return count


def _latest_consumed_completion_receipt(profile: object, target: str) -> tuple[str | None, Path | None]:
    handoff_dir = _expanded_handoff_dir(profile)
    pattern = f"*__{sanitize_name(target)}__planner.json.consumed"
    candidates = [path for path in handoff_dir.glob(pattern) if path.is_file()]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = load_json(path)
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") != "completion":
            continue
        if payload.get("source") != target or payload.get("target") != "planner":
            continue
        task_id = str(payload.get("task_id") or path.name.split("__", 1)[0]).strip() or None
        return task_id, path
    return None, None


def _clear_audit_script_path() -> Path:
    return Path(__file__).resolve().with_name("audit_clear_before_dispatch.py")


def _clear_audit_finding_path(profile: object, task_id: str) -> Path:
    timestamp = utc_now_iso().split("+", 1)[0].replace(":", "-")
    project = sanitize_name(str(getattr(profile, "project_name")))
    finding_dir = _expanded_tasks_dir(profile) / "finding"
    return finding_dir / f"{project}-finding-{timestamp}-clear-violation-{sanitize_name(task_id)}.md"


def _write_clear_audit_finding(
    *,
    profile: object,
    task_id: str,
    source: str,
    target: str,
    prev_task: str | None,
    receipt_path: Path,
    delivery_path: Path,
    audit_result: subprocess.CompletedProcess[str],
    warning_text: str,
) -> Path:
    finding_path = _clear_audit_finding_path(profile, task_id)
    finding_path.parent.mkdir(parents=True, exist_ok=True)
    audit_stderr = (audit_result.stderr or "").strip() or "<empty>"
    audit_stdout = (audit_result.stdout or "").strip() or "<empty>"
    content = (
        "---\n"
        "kind: finding\n"
        f"project: {profile.project_name}\n"
        f"task_id: {task_id}\n"
        "seat: planner\n"
        f"source: {source}\n"
        f"target: {target}\n"
        "status: open\n"
        f"title: clear-before-dispatch violation for {task_id}\n"
        f"ts: {utc_now_iso()}\n"
        f"detail: Planner dispatched {target} without /clear after gate 1 + gate 3 passed.\n"
        "---\n"
        "\n"
        "# Evidence\n"
        "\n"
        f"- warning: {warning_text}\n"
        f"- prev_task: {prev_task or '<none>'}\n"
        f"- receipt: {receipt_path}\n"
        f"- delivery: {delivery_path}\n"
        f"- audit_script: {_clear_audit_script_path()}\n"
        f"- audit_exit: {audit_result.returncode}\n"
        "\n"
        "## Audit stderr\n"
        "\n"
        "```text\n"
        f"{audit_stderr}\n"
        "```\n"
        "\n"
        "## Audit stdout\n"
        "\n"
        "```text\n"
        f"{audit_stdout}\n"
        "```\n"
    )
    finding_path.write_text(content, encoding="utf-8")
    return finding_path


def _send_clear_audit_warning(profile: object, warning_text: str) -> None:
    try:
        result = notify(profile, "planner", warning_text)
    except Exception as exc:  # noqa: BLE001 - best-effort side channel
        print(f"warn: clear audit planner notify failed: {exc}", file=sys.stderr)
        return
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        print(f"warn: clear audit planner notify failed: {detail}", file=sys.stderr)


def _run_clear_audit_hook(
    *,
    profile: object,
    source_role: str,
    target_role: str,
    task_id: str,
    source: str,
    target: str,
    notify_sent: bool,
) -> None:
    if source_role != "planner" or target_role == "planner" or not notify_sent:
        return
    prev_task, receipt_marker = _latest_consumed_completion_receipt(profile, target)
    if receipt_marker is None:
        return
    delivery_path = profile.delivery_path(target)
    if not delivery_path.exists():
        return
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_clear_audit_script_path()),
                "--profile",
                str(profile.profile_path),
                "--task-id",
                task_id,
                "--target",
                target,
            ],
            cwd=str(profile.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - hook must not block dispatch
        print(f"warn: clear audit hook failed for {task_id}: {exc}", file=sys.stderr)
        return
    if result.returncode == 1:
        warning_text = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"[CLEAR-AUDIT-WARNING] task_id={task_id} target={target} prev_task={prev_task or '<none>'}"
        )
        if "[CLEAR-AUDIT-WARNING]" not in warning_text:
            warning_text = f"[CLEAR-AUDIT-WARNING] {warning_text}"
        finding_path = _write_clear_audit_finding(
            profile=profile,
            task_id=task_id,
            source=source,
            target=target,
            prev_task=prev_task,
            receipt_path=receipt_marker,
            delivery_path=delivery_path,
            audit_result=result,
            warning_text=warning_text,
        )
        print(warning_text, file=sys.stderr)
        _send_clear_audit_warning(profile, f"{warning_text} finding={finding_path}")
        return
    if result.returncode not in {0, 2}:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        print(f"warn: clear audit helper failed for {task_id}: {detail}", file=sys.stderr)

INTENT_MAP: dict[str, dict[str, str]] = {
    # ── Plan-phase intents (planner's own skills) ─────────────────────
    "eng-review": {
        "trigger": "Review the architecture and lock in the plan",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-plan-eng-review/SKILL.md",
        "description": "engineering plan review (architecture / data flow / test coverage / perf)",
    },
    "ceo-review": {
        "trigger": "Think bigger and expand scope if it creates a better product",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-plan-ceo-review/SKILL.md",
        "description": "CEO/founder-mode strategy review (scope expand / hold / reduce)",
    },
    "design-review": {
        "trigger": "Review the design plan and design critique",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-plan-design-review/SKILL.md",
        "description": "designer's-eye plan review (UX / visual / component; plan-mode)",
    },
    "devex-review": {
        "trigger": "DX review and developer experience audit (Addy Osmani framework)",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-plan-devex-review/SKILL.md",
        "description": "developer experience audit (zero friction / learn by doing / fight uncertainty)",
    },
    # ── Build / ship intents (builder-1) ──────────────────────────────
    "ship": {
        "trigger": "Ship it and create a PR (run tests, review diff, bump VERSION, update CHANGELOG)",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-ship/SKILL.md",
        "description": "ship workflow (test → diff → version → commit → push → PR)",
    },
    "land": {
        "trigger": "Land the PR and deploy — merge, wait for CI and deploy, verify production",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-land-and-deploy/SKILL.md",
        "description": "merge + canary + production verification",
    },
    "investigate": {
        "trigger": "Investigate root cause — debug this, why is this broken, root cause analysis",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-investigate/SKILL.md",
        "description": "bug RCA (investigate → analyze → hypothesize → implement)",
    },
    "freeze": {
        "trigger": "Freeze and restrict edits to this directory",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-freeze/SKILL.md",
        "description": "restrict Edit/Write to one module for the session",
    },
    "unfreeze": {
        "trigger": "Unfreeze and remove the edit restriction",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-unfreeze/SKILL.md",
        "description": "clear the /freeze boundary, allow all-directory edits again",
    },
    # ── Review intent (reviewer-1) ────────────────────────────────────
    "code-review": {
        "trigger": "Code review — pre-landing PR review, check the diff for SQL safety, LLM trust, conditional side effects, structural issues",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-review/SKILL.md",
        "description": "pre-landing PR review (NOT plan-review; for final diff check)",
    },
    # Patrol intents use the gstack QA marketplace skills.
    "qa-test": {
        "trigger": "QA — systematically test this web app and fix bugs found (test → fix → verify loop)",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-qa/SKILL.md",
        "description": "full QA test-fix-verify loop with before/after health scores",
    },
    "qa-only": {
        "trigger": "QA report only — just report bugs, don't fix anything",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-qa-only/SKILL.md",
        "description": "report-only QA (no source code edits, only a structured report)",
    },
    # ── Design intents (designer-1) ───────────────────────────────────
    "design-critique": {
        "trigger": "Audit the design — visual QA, check if it looks good, design polish (post-implementation)",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-design-review/SKILL.md",
        "description": "post-implementation visual audit (iteratively fixes visual issues)",
    },
    "design-html": {
        "trigger": "Finalize this design and turn it into production HTML/CSS",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-design-html/SKILL.md",
        "description": "design → production HTML/CSS via Pretext patterns",
    },
    "design-shotgun": {
        "trigger": "Design shotgun — generate multiple AI design variants and compare",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-design-shotgun/SKILL.md",
        "description": "multi-variant design exploration with comparison board",
    },
    # ── Cross-cutting intents (all seats) ─────────────────────────────
    "office-hours": {
        "trigger": "Office hours brainstorm — help me think through this, is this worth building",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-office-hours/SKILL.md",
        "description": "YC office-hours brainstorm (6 forcing questions / design doc)",
    },
    "checkpoint": {
        "trigger": "Checkpoint — save progress and where was I",
        "skill_md": f"{_GSTACK_SKILLS_ROOT}/gstack-checkpoint/SKILL.md",
        "description": "save/resume working state across sessions",
    },
}


def apply_intent(
    intent: str | None,
    objective: str,
    skill_refs: list[str] | None,
) -> tuple[str, list[str]]:
    """Expand --intent into (augmented_objective, augmented_skill_refs).

    - If intent is None, return inputs unchanged.
    - If intent is valid, prepend the canonical trigger phrase to the objective
      and append the skill SKILL.md path to skill_refs (deduped).
    - If intent is unknown, raise ValueError listing valid intents.
    """
    if intent is None:
        return objective, (skill_refs or [])
    if intent not in INTENT_MAP:
        valid = ", ".join(sorted(INTENT_MAP.keys()))
        raise ValueError(
            f"unknown --intent {intent!r}; valid intents: {valid}"
        )
    spec = INTENT_MAP[intent]
    trigger = spec["trigger"]
    skill_md = spec["skill_md"]
    # Prepend trigger only if not already present (idempotent when koder
    # re-runs a dispatch with --intent after the trigger is already in the
    # objective — helpful when the operator wrote both by hand).
    if trigger.lower() not in objective.lower():
        new_objective = f"**{trigger}** — {objective.strip()}"
    else:
        new_objective = objective
    refs = list(skill_refs or [])
    if skill_md not in refs:
        refs.append(skill_md)
    return new_objective, refs


def _write_dispatch_to_ledger(
    *,
    task_id: str,
    project: str,
    source: str,
    target: str,
    role_hint: str | None,
    title: str | None,
    correlation_id: str | None,
) -> None:
    """Write task + task.dispatched event to state.db. Defensive: never fails dispatch."""
    try:
        from datetime import datetime, timezone as _tz
        from core.lib.state import open_db, record_task_dispatched, record_event, Task
        task = Task(
            id=task_id,
            project=project,
            source=source,
            target=target,
            role_hint=role_hint,
            status="dispatched",
            title=title,
            correlation_id=correlation_id,
            opened_at=datetime.now(_tz.utc).isoformat(timespec="seconds"),
        )
        with open_db() as conn:
            record_task_dispatched(conn, task)
            record_event(conn, "task.dispatched", project,
                         task_id=task_id, source=source, target=target)
    except Exception as exc:
        print(f"warn: state.db unavailable, skipping ledger write: {exc}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dispatch a task to a target seat.")
    parser.add_argument("--profile", required=True, help="Path to the project profile TOML.")
    parser.add_argument("--source", default="planner", help="Seat dispatching the task.")
    _target_group = parser.add_mutually_exclusive_group(required=True)
    _target_group.add_argument("--target", help="Target seat (explicit seat id).")
    _target_group.add_argument(
        "--target-role",
        metavar="ROLE",
        help="Pick least-busy live seat with this role from state.db (e.g. 'builder').",
    )
    parser.add_argument(
        "--expected-branch",
        help="Expected branch tip for builder worktree lock metadata.",
    )
    parser.add_argument(
        "--expected-worktree",
        help="Expected worktree path for builder lock metadata.",
    )
    parser.add_argument("--finding-id", help="Optional finding bucket id for hypothesis retries.")
    parser.add_argument(
        "--rca-override",
        action="store_true",
        help="Bypass hypothesis retry warning when the finding has already exceeded the fix counter.",
    )
    parser.add_argument(
        "--core-ux",
        action="store_true",
        help="Mark the dispatch as core UX and require an explicit core UX gate on closeout.",
    )
    parser.add_argument(
        "--force-parallel-builder",
        action="store_true",
        help="Bypass the same-seat builder dispatch lock for intentional task stacking.",
    )
    parser.add_argument("--task-id", required=True, help="Task id.")
    parser.add_argument("--title", required=True, help="Task title.")
    parser.add_argument("--objective", required=True, help="Objective/body text for the TODO.")
    parser.add_argument(
        "--user-summary",
        help="Short plain-language summary for operator-visible progress.",
    )
    parser.add_argument(
        "--test-policy",
        required=True,
        choices=["UPDATE", "FREEZE", "EXTEND", "N/A"],
        help=(
            "UPDATE: tests must follow code changes; "
            "FREEZE: do not touch tests; "
            "EXTEND: add new tests only; "
            "N/A: doc/config only, no testable code"
        ),
    )
    parser.add_argument("--reply-to", help="Seat that should receive completion back from the target.")
    parser.add_argument("--notes", default="dispatched via gstack-harness", help="TASKS.md note.")
    parser.add_argument("--docs-consulted", default="", help="Memory KB record path for official-docs research.")
    parser.add_argument("--docs-skip-reason", default="", help="Explicit reason official-docs research was not required.")
    parser.add_argument("--status-note", help="Optional STATUS.md note.")
    add_notify_args(parser)
    parser.add_argument(
        "--task-type",
        default="unspecified",
        help="Task type hint (implementation/review/research/unspecified).",
    )
    parser.add_argument(
        "--review-required",
        action="store_true",
        help="Mark task as requiring reviewer sign-off.",
    )
    parser.add_argument(
        "--skill-refs",
        nargs="*",
        metavar="SKILL_REF",
        default=None,
        help=(
            "Optional skill documentation pointers to include in the dispatched TODO.md "
            "(e.g. 'references/feishu-bridge-setup.md'). Appended as a '# Skill Refs' section."
        ),
    )
    parser.add_argument(
        "--allow-notify-failure",
        action="store_true",
        help=(
            "Continue even if tmux notify fails (exit 0). "
            "Default: exit 1 with a NOTIFY FAILED banner on failure. "
            "Use in CI/batch where notify is best-effort."
        ),
    )
    parser.add_argument(
        "--intent",
        choices=sorted(INTENT_MAP.keys()),
        default=None,
        help=(
            "High-level user-intent key that auto-injects the canonical gstack "
            "skill trigger phrase into --objective AND appends the skill's "
            "SKILL.md path to --skill-refs. Use this so koder does not have to "
            "memorise every gstack skill's trigger vocabulary. "
            "Valid keys: " + ", ".join(sorted(INTENT_MAP.keys())) + ". "
            "See TOOLS/dispatch.md for the user-intent → key mapping."
        ),
    )
    parser.add_argument(
        "--spec-path",
        default=None,
        help=(
            "Path to memory's SPEC.md for this task. When provided, the path is "
            "embedded in the dispatch receipt and surfaced to the specialist via "
            "TODO so they can read the contract (acceptance criteria, deliverables, "
            "out-of-scope) before working. See core/scripts/spec_admin.py."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.task_type == "external-integration" and not (args.docs_consulted or args.docs_skip_reason):
        print(
            "external-integration task requires --docs-consulted <path> "
            "or --docs-skip-reason <reason>",
            file=sys.stderr,
        )
        return 2
    docs_note = ""
    if args.docs_consulted:
        docs_note = f" docs_consulted:{args.docs_consulted}"
    elif args.docs_skip_reason:
        docs_note = f" docs_skip_reason:{args.docs_skip_reason}"
    if docs_note and docs_note.strip() not in args.notes:
        args.notes = f"{args.notes};{docs_note}"
    do_notify = resolve_notify(args)
    role_hint: str | None = getattr(args, "target_role", None)

    # Load profile early when --target-role is used (need project_name for lookup).
    profile = None
    if role_hint:
        profile = load_profile(args.profile)
        try:
            from core.lib.state import open_db, pick_least_busy_seat
            with open_db() as conn:
                picked = pick_least_busy_seat(conn, profile.project_name, role_hint)
        except Exception as exc:
            print(f"warn: state.db unavailable for role resolution: {exc}", file=sys.stderr)
            picked = None
        if picked is None:
            print(
                f"seat_needed: no live seat with role={role_hint!r} in project={profile.project_name!r}.",
                file=sys.stderr,
            )
            print(
                "hint: state.db seats table may be out of sync. Try:\n"
                f"  python3 ~/ClawSeat/core/scripts/agent_admin.py session reconcile --project {profile.project_name}\n"
                "or use --target <seat_id> to bypass liveness lookup.",
                file=sys.stderr,
            )
            return 3
        args.target = picked.seat_id
        print(f"target-role resolved: {role_hint} -> {args.target}", file=sys.stderr)

    # T9: block dispatch to memory before touching the profile — memory is an
    # oracle, never a task worker; this check is profile-independent.
    assert_target_not_memory(args.target, "dispatch_task.py")
    if profile is None:
        profile = load_profile(args.profile)
    if args.target not in profile.seats:
        raise SystemExit(
            f"dispatch target {args.target!r} is not a declared seat for project "
            f"{profile.project_name!r}; known seats: {profile.seats}"
        )
    # Expand --intent into a canonical trigger phrase + skill-ref before we
    # write anything. This is the "koder should memorise triggers, not the
    # user" plumbing per SOUL.md §5.
    effective_objective, effective_skill_refs = apply_intent(
        args.intent,
        args.objective,
        args.skill_refs,
    )
    todo_path = profile.todo_path(args.target)
    audit_dir = _expanded_handoff_dir(profile) / "audit"
    if _is_task_already_queued(todo_path, args.task_id):
        print(
            f"TASK_ALREADY_QUEUED {args.task_id} @ {utc_now_iso()}",
            file=sys.stderr,
        )
        return 2
    source_role = normalize_role(profile.seat_roles.get(args.source, ""))
    target_role = normalize_role(profile.seat_roles.get(args.target, ""))
    if _reject_v3_planner_target_dispatch(
        profile=profile,
        source=args.source,
        target=args.target,
        source_role=source_role,
        target_role=target_role,
    ):
        return 2
    if target_role == "builder" and not args.finding_id:
        outstanding_task = _builder_outstanding_task(todo_path)
        if outstanding_task and outstanding_task != args.task_id:
            if args.force_parallel_builder:
                print(
                    "WARNING: bypassing same-seat builder dispatch lock; "
                    "use only when intentionally stacking work on one builder seat",
                    file=sys.stderr,
                )
            else:
                print(
                    f"BLOCKED: builder seat {args.target} dispatch outstanding "
                    f"({outstanding_task}); awaiting completion receipt before "
                    "stacking another task on the same seat",
                    file=sys.stderr,
                )
                return 2
    expected_base_sha = _git_main_tip(getattr(profile, "repo_root", None))
    reply_to = args.reply_to or args.source
    correlation_id = stable_dispatch_nonce(profile.project_name, "planning", args.task_id)
    dispatched_at = datetime.now(timezone.utc).replace(microsecond=0)
    _validate_user_summary(
        args.user_summary,
        dispatched_at=dispatched_at,
        task_id=args.task_id,
    )
    finding_counter = None
    finding_exceeded = False
    if args.finding_id:
        finding_counter = _finding_hypothesis_counter(profile, target=args.target, finding_id=args.finding_id)
        finding_exceeded = finding_counter >= 3
        if finding_exceeded and not args.rca_override:
            print(
                f"warning: hypothesis_fix_counter exceeded for finding_id={args.finding_id} "
                f"(counter={finding_counter}); use --rca-override to continue",
                file=sys.stderr,
            )
    dispatch_lock_fields = _dispatch_lock_metadata(
        task_id=args.task_id,
        target_role=target_role,
        expected_branch=args.expected_branch,
        expected_worktree=args.expected_worktree,
    )
    if target_role == "builder" and expected_base_sha:
        _advance_builder_task_branch(
            getattr(profile, "repo_root", None),
            dispatch_lock_fields.get("expected_branch"),
            expected_base_sha,
        )
    append_task_to_queue(
        todo_path,
        task_id=args.task_id,
        project=profile.project_name,
        owner=args.target,
        title=args.title,
        objective=effective_objective,
        source=args.source,
        reply_to=reply_to,
        skill_refs=effective_skill_refs,
        task_type=args.task_type,
        review_required=args.review_required,
        correlation_id=correlation_id,
        test_policy=args.test_policy,
        core_ux=args.core_ux,
        finding_id=args.finding_id,
        hypothesis_fix_counter=finding_counter,
        hypothesis_fix_counter_exceeded=finding_exceeded,
        rca_override=True if args.rca_override else None,
    )
    upsert_tasks_row(
        profile.tasks_doc,
        task_id=args.task_id,
        title=args.title,
        owner=args.target,
        status="pending",
        notes=args.notes,
    )
    receipt = {
        "kind": "dispatch",
        "task_id": args.task_id,
        "correlation_id": correlation_id,
        "source": args.source,
        "target": args.target,
        "title": args.title,
        "test_policy": args.test_policy,
        "todo_path": str(todo_path),
        "reply_to": reply_to,
        "docs_consulted": args.docs_consulted or None,
        "docs_skip_reason": args.docs_skip_reason or None,
        "assigned_at": dispatched_at.isoformat(),
        "notified_at": None,
        "notify_message": None,
    }
    if args.user_summary is not None:
        receipt["user_summary"] = args.user_summary
    if args.finding_id:
        receipt["finding_id"] = args.finding_id
        receipt["hypothesis_fix_counter"] = finding_counter
        receipt["hypothesis_fix_counter_exceeded"] = finding_exceeded
        receipt["rca_override"] = True if args.rca_override else None
    elif args.rca_override:
        receipt["rca_override"] = True
    if args.core_ux:
        receipt["core_ux"] = True
    if args.spec_path:
        receipt["spec_path"] = args.spec_path
    if expected_base_sha:
        receipt["expected_base_sha"] = expected_base_sha
    receipt.update(
        _populate_lineage_receipt_fields(
            repo_root=getattr(profile, "repo_root", None),
            expected_base_sha=expected_base_sha,
            source_role=source_role,
        )
    )
    receipt.update(dispatch_lock_fields)
    if do_notify:
        message = build_notify_message(
            args.target,
            todo_path,
            args.task_id,
            source=args.source,
            reply_to=reply_to,
        )
        resolution = resolve_seat_from_profile(args.target, profile)
        if resolution.kind == "tmux":
            result = notify(profile, args.target, message)
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
                print("============ NOTIFY FAILED ============", file=sys.stderr)
                print(f"  target : {args.target}", file=sys.stderr)
                print(f"  task   : {args.task_id}", file=sys.stderr)
                print(f"  reason : {detail}", file=sys.stderr)
                print(
                    f"  fix    : send-and-verify.sh --project {profile.project_name} "
                    f"{args.target} '<message>'",
                    file=sys.stderr,
                )
                print("=======================================", file=sys.stderr)
                if not getattr(args, "allow_notify_failure", False):
                    receipt_path = profile.handoff_path(args.task_id, args.source, args.target)
                    write_json(receipt_path, receipt)
                    return 1
                print("warn: --allow-notify-failure set; continuing", file=sys.stderr)
            else:
                receipt["notified_at"] = utc_now_iso()
                receipt["notify_message"] = message
            should_broadcast = (
                source_role in {"planner", "planner-dispatcher"}
                or target_role in {"planner", "planner-dispatcher"}
                or args.source == profile.active_loop_owner
                or args.target == profile.active_loop_owner
            )
            if should_broadcast and legacy_feishu_group_broadcast_enabled():
                if source_role in {"planner", "planner-dispatcher"} and target_role not in {
                    "planner",
                    "planner-dispatcher",
                }:
                    group_message = (
                        f"{profile.project_name} 项目 planner 已向 {args.target} 发布任务 {args.task_id}："
                        f"{args.title}. 回复链路 {reply_to}."
                    )
                elif target_role in {"planner", "planner-dispatcher"} and source_role not in {
                    "planner",
                    "planner-dispatcher",
                }:
                    group_message = (
                        f"{profile.project_name} 项目 planner 已收到任务 {args.task_id}，"
                        f"来自 {args.source}：{args.title}. 回复链路 {reply_to}."
                    )
                else:
                    group_message = (
                        f"{profile.project_name} 项目 planner 任务流转 {args.task_id}："
                        f"{args.source} -> {args.target}，{args.title}."
                    )
                broadcast = broadcast_feishu_group_message(group_message, project=profile.project_name)
                receipt["feishu_group_broadcast"] = broadcast
                if broadcast.get("status") == "failed":
                    print(
                        f"warn: feishu group broadcast failed for {args.task_id}: "
                        f"{broadcast.get('stderr') or broadcast.get('stdout') or broadcast.get('reason', 'unknown')}",
                        file=sys.stderr,
                    )
            elif should_broadcast:
                receipt["feishu_group_broadcast"] = {
                    "status": "skipped",
                    "reason": "legacy_group_broadcast_disabled",
                }
        else:
            # kind=openclaw or kind=file-only: tmux notify not applicable
            # For openclaw targets, use complete_handoff.py for the koder closeout path.
            print(
                f"warn: dispatch target {args.target!r} resolves to kind={resolution.kind} — "
                "tmux notify skipped. Use complete_handoff.py for the koder closeout path.",
                file=sys.stderr,
            )
            receipt["notify_message"] = message
            receipt["feishu_group_broadcast"] = {
                "status": "skipped",
                "reason": f"target_kind_{resolution.kind}",
            }
        notify_success = bool(receipt.get("notified_at"))
    else:
        notify_success = False
    receipt_path = profile.handoff_path(args.task_id, args.source, args.target)
    write_json(receipt_path, receipt)
    _write_dispatch_to_ledger(
        task_id=args.task_id,
        project=profile.project_name,
        source=args.source,
        target=args.target,
        role_hint=role_hint,
        title=args.title,
        correlation_id=correlation_id,
    )
    append_status_dispatch_event(
        profile.status_doc,
        source=args.source,
        task_id=args.task_id,
        target=args.target,
        test_policy=args.test_policy,
        finding_id=args.finding_id,
        hypothesis_counter=finding_counter,
        rca_override=args.rca_override,
        core_ux=args.core_ux,
        audit_dir=audit_dir,
    )
    print(f"dispatched {args.task_id} -> {args.target}")
    print(f"todo: {todo_path}")
    print(f"receipt: {receipt_path}")
    if notify_success and source_role == "planner" and target_role != "planner":
        _run_clear_audit_hook(
            profile=profile,
            source_role=source_role,
            target_role=target_role,
            task_id=args.task_id,
            source=args.source,
            target=args.target,
            notify_sent=notify_success,
        )
    if _should_announce_planner_event(args.source, args.target, profile=profile):
        _try_announce_planner_event(
            project=profile.project_name,
            source=args.source,
            target=args.target,
            task_id=args.task_id,
            verb="dispatched",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
