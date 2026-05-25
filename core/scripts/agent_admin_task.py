from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_STATUSES = {"pending", "in_progress", "done", "blocked"}
ALLOWED_TRANSITIONS = {
    "pending": {"in_progress"},
    "in_progress": {"done", "blocked"},
}
DISPATCH_LOG_HEADER = "## dispatch log (append-only, last 20)"
DISPATCH_LOG_COMMENT = (
    "<!-- dispatch_task.py / complete_handoff.py append entries here. "
    "Do not delete this section. -->"
)
DISPATCH_LOG_HEAL_NOTE = "<!-- auto-healed by dispatch_task.py -->"


@dataclass
class WorkflowStep:
    name: str
    owner_role: str = ""
    status: str = "pending"
    prereq: list[str] = field(default_factory=list)
    start: int = 0
    end: int = 0
    status_line: int = -1


@dataclass
class TodoEntry:
    path: Path
    status: str
    heading_task_id: str
    task_id: str
    dispatched_at: datetime | None
    start_line: int
    heading_line: str


class TaskCommandError(RuntimeError):
    pass


def _task_id_ok(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", value or ""))


def tasks_root(home: Path | None = None) -> Path:
    root = home if home is not None else Path(os.environ.get("CLAWSEAT_REAL_HOME", str(Path.home()))).expanduser()
    return root / ".agents" / "tasks"


def project_tasks_dir(project: str, *, home: Path | None = None) -> Path:
    return tasks_root(home) / project


def task_dir(project: str, task_id: str, *, home: Path | None = None) -> Path:
    return project_tasks_dir(project, home=home) / task_id


def _workflow_template(task_id: str, template: str) -> str:
    return "\n".join(
        [
            f"# Workflow: {task_id}",
            "",
            f"workflow_template: {template or 'blank'}",
            "",
            "steps: []",
            "",
        ]
    )


def _status_template(task_id: str) -> str:
    return (
        f"# Status: {task_id}\n\n"
        "status: pending\n\n"
        f"{DISPATCH_LOG_COMMENT}\n\n"
        f"{DISPATCH_LOG_HEADER}\n"
    )


def _ensure_status_dispatch_log_section(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if DISPATCH_LOG_HEADER in text:
        return
    path.write_text(
        text.rstrip() + f"\n\n{DISPATCH_LOG_HEAL_NOTE}\n\n{DISPATCH_LOG_HEADER}\n",
        encoding="utf-8",
    )


def create_task(args: Any) -> int:
    task_id = str(args.task_id)
    project = str(args.project)
    if not _task_id_ok(task_id):
        raise TaskCommandError(f"invalid task_id: {task_id}")
    # v3 spec §10 item 2: brief-driven workflow template is deprecated for v3
    # multi-team projects. v3 callers should use `agent_admin brief queue`
    # which appends a task_created event + writes a schema-valid brief.
    # Non-breaking: v2 single-team callers still work, just see a warning.
    tmpl = str(getattr(args, "workflow_template", "") or "").strip()
    if tmpl in {"brief-driven", "brief_driven"}:
        import sys
        print(
            f"warn: 'agent_admin task create --workflow-template {tmpl}' is deprecated "
            "for v3 projects; use 'agent_admin brief queue --project ... --team ... "
            "--task-id ... --objective ...' instead (spec §4.2-§4.3).",
            file=sys.stderr,
        )
    root = task_dir(project, task_id)
    root.mkdir(parents=True, exist_ok=True)
    workflow = root / "workflow.md"
    status = root / "STATUS.md"
    if not workflow.exists():
        workflow.write_text(_workflow_template(task_id, tmpl), encoding="utf-8")
    if not status.exists():
        status.write_text(_status_template(task_id), encoding="utf-8")
    else:
        _ensure_status_dispatch_log_section(status)
    print(root)
    return 0


def _parse_list(value: str) -> list[str]:
    raw = value.strip()
    if not raw or raw in {"[]", "null", "None"}:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [item.strip().strip("\"'") for item in raw.split(",") if item.strip().strip("\"'")]


def parse_workflow(text: str) -> list[WorkflowStep]:
    lines = text.splitlines()
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^##\s+Step\s+\d+\s*:\s*(.+?)\s*$", line)
        if match:
            starts.append((match.group(1).strip(), index))

    steps: list[WorkflowStep] = []
    for offset, (name, start) in enumerate(starts):
        end = starts[offset + 1][1] if offset + 1 < len(starts) else len(lines)
        step = WorkflowStep(name=name, start=start, end=end)
        for line_no in range(start + 1, end):
            line = lines[line_no]
            match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$", line)
            if not match:
                continue
            key, value = match.group(1), match.group(2)
            if key == "owner_role":
                step.owner_role = value.strip().strip("\"'")
            elif key == "status":
                step.status = value.strip().strip("\"'")
                step.status_line = line_no
            elif key == "prereq":
                step.prereq = _parse_list(value)
        steps.append(step)
    return steps


def _ready_steps(path: Path, owner_role: str) -> list[WorkflowStep]:
    text = path.read_text(encoding="utf-8")
    steps = parse_workflow(text)
    status_by_name = {step.name: step.status for step in steps}
    ready: list[WorkflowStep] = []
    for step in steps:
        if step.owner_role != owner_role or step.status != "pending":
            continue
        if all(status_by_name.get(name) == "done" for name in step.prereq):
            ready.append(step)
    return ready


def list_pending(args: Any) -> int:
    project = str(args.project)
    owner_role = str(args.owner_role)
    root = project_tasks_dir(project)
    if not root.exists():
        return 0
    for workflow in sorted(root.glob("*/workflow.md")):
        task_id = workflow.parent.name
        for step in _ready_steps(workflow, owner_role):
            print(f"{task_id}\t{step.name}")
    return 0


def _parse_iso_datetime(value: str) -> datetime | None:
    raw = value.strip().strip("\"'")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_todo_entries(path: Path) -> list[TodoEntry]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    entries: list[TodoEntry] = []
    starts: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(##\s+\[([^\]]+)\]\s+(.+?))(\s*)$", line.rstrip("\n"))
        if match:
            starts.append((index, match))

    for offset, (start, match) in enumerate(starts):
        end = starts[offset + 1][0] if offset + 1 < len(starts) else len(lines)
        fields: dict[str, str] = {}
        for line in lines[start + 1 : end]:
            field_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$", line)
            if field_match:
                fields[field_match.group(1)] = field_match.group(2)
        heading_task_id = match.group(3).strip()
        task_id = fields.get("task_id", heading_task_id).strip()
        entries.append(
            TodoEntry(
                path=path,
                status=match.group(2).strip(),
                heading_task_id=heading_task_id,
                task_id=task_id,
                dispatched_at=_parse_iso_datetime(fields.get("dispatched_at", "")),
                start_line=start,
                heading_line=lines[start],
            )
        )
    return entries


def _delivery_mentions_task(delivery: Path, task_id: str) -> bool:
    if not delivery.exists():
        return False
    try:
        text = delivery.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return f"task_id: {task_id}" in text or task_id in text


def _delivery_is_recent_for_task(delivery: Path, task_id: str, dispatched_at: datetime, cutoff: datetime) -> bool:
    if not delivery.exists() or not _delivery_mentions_task(delivery, task_id):
        return False
    try:
        delivery_mtime = datetime.fromtimestamp(delivery.stat().st_mtime, timezone.utc)
    except OSError:
        return False
    return delivery_mtime > dispatched_at and delivery_mtime > cutoff


def _supersede_todo_entries(todo_path: Path, entries: list[TodoEntry]) -> int:
    if not entries:
        return 0
    lines = todo_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for entry in entries:
        newline = "\n" if lines[entry.start_line].endswith("\n") else ""
        lines[entry.start_line] = f"## [superseded] {entry.heading_task_id}{newline}"
    _atomic_write(todo_path, "".join(lines))
    return len(entries)


def auto_supersede(args: Any) -> int:
    project = str(args.project)
    age_days = int(getattr(args, "age_days", 3))
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - age_days * 86400
    cutoff_dt = datetime.fromtimestamp(cutoff, timezone.utc)
    root = project_tasks_dir(project)
    if not root.exists():
        print(f"AUTO_SUPERSEDE project={project} count=0")
        return 0

    total = 0
    for todo_path in sorted(root.glob("*/TODO.md")):
        owner_dir = todo_path.parent
        delivery = owner_dir / "DELIVERY.md"
        stale_entries: list[TodoEntry] = []
        for entry in _parse_todo_entries(todo_path):
            if entry.status != "pending" or entry.dispatched_at is None:
                continue
            if entry.dispatched_at.timestamp() >= cutoff:
                continue
            if _delivery_is_recent_for_task(delivery, entry.task_id, entry.dispatched_at, cutoff_dt):
                continue
            stale_entries.append(entry)
        changed = _supersede_todo_entries(todo_path, stale_entries)
        total += changed
        for entry in stale_entries:
            print(f"superseded\t{owner_dir.name}\t{entry.task_id}")

    print(f"AUTO_SUPERSEDE project={project} count={total}")
    return 0


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def update_status(args: Any) -> int:
    task_id = str(args.task_id)
    project = str(args.project)
    step_name = str(args.step_name)
    new_status = str(args.status)
    if new_status not in VALID_STATUSES:
        raise TaskCommandError(f"invalid status: {new_status}")

    workflow = task_dir(project, task_id) / "workflow.md"
    if not workflow.exists():
        raise TaskCommandError(f"workflow not found: {workflow}")

    text = workflow.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    steps = parse_workflow(text)
    matches = [step for step in steps if step.name == step_name]
    if not matches:
        raise TaskCommandError(f"step not found: {step_name}")
    if len(matches) > 1:
        raise TaskCommandError(f"ambiguous step name: {step_name}")
    step = matches[0]
    if step.status_line < 0:
        raise TaskCommandError(f"step has no status field: {step_name}")
    allowed = ALLOWED_TRANSITIONS.get(step.status, set())
    if new_status not in allowed:
        raise TaskCommandError(f"invalid transition: {step.status} -> {new_status}")

    newline = "\n" if lines[step.status_line].endswith("\n") else ""
    indent = re.match(r"^(\s*)", lines[step.status_line]).group(1)
    lines[step.status_line] = f"{indent}status: {new_status}{newline}"
    _atomic_write(workflow, "".join(lines))
    print(f"{task_id}\t{step_name}\t{new_status}")
    return 0
