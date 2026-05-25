#!/usr/bin/env python3
"""Bootstrap ~/.clawseat/machine.toml tenants from memory scan output.

Called after memory has produced machine/openclaw.json. Idempotent: existing
tenants are preserved, and only new tenants with existing workspaces are added.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "core" / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load

from core.lib.machine_config import FeishuRouting, OpenClawTenant, load_machine, write_machine


def _contract_fields(workspace: Path) -> tuple[str, str]:
    contract = workspace / "WORKSPACE_CONTRACT.toml"
    if not contract.exists():
        return "", ""
    try:
        data = _toml_loads(contract.read_text(encoding="utf-8"))
    except Exception:
        return "", ""
    return (
        str(data.get("project") or "").strip(),
        str(data.get("feishu_group_id") or "").strip(),
    )


def bootstrap_machine_tenants(memory_root: Path) -> int:
    oc_json = memory_root / "machine" / "openclaw.json"
    if not oc_json.exists():
        print(f"ERR_NO_SCAN: {oc_json} not found", file=sys.stderr)
        return 2

    try:
        data = json.loads(oc_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERR_BAD_SCAN_JSON: {exc}", file=sys.stderr)
        return 2

    agents = data.get("agents", [])
    if not isinstance(agents, list):
        print("ERR_BAD_SCAN_JSON: agents must be a list", file=sys.stderr)
        return 2

    cfg = load_machine()
    added = 0
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        name = str(agent.get("name", "")).strip()
        workspace_text = str(agent.get("workspace", "")).strip()
        if not name or not workspace_text:
            continue
        workspace = Path(workspace_text).expanduser()
        if not workspace.exists():
            continue
        project = str(agent.get("project") or "").strip()
        feishu_group_id = str(agent.get("feishu_group_id") or agent.get("group_id") or "").strip()
        contract_project, contract_group = _contract_fields(workspace)
        project = project or contract_project
        feishu_group_id = feishu_group_id or contract_group
        if name in cfg.tenants:
            pass
        else:
            cfg.tenants[name] = OpenClawTenant(
                name=name,
                workspace=workspace,
                description="auto-registered by scan",
            )
            added += 1
        if feishu_group_id and project:
            existing = cfg.feishu_routing.get(feishu_group_id)
            bound = list(existing.bound_projects) if existing else []
            if project not in bound:
                bound.append(project)
            cfg.feishu_routing[feishu_group_id] = FeishuRouting(
                chat_id=feishu_group_id,
                bound_projects=bound,
                default_project=(existing.default_project if existing and existing.default_project else project),
            )

    write_machine(cfg)
    print(f"OK: {added} tenant(s) added; {len(cfg.tenants)} total")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    memory_root = Path(args[0] if args else "~/.agents/memory").expanduser()
    return bootstrap_machine_tenants(memory_root)


if __name__ == "__main__":
    raise SystemExit(main())
