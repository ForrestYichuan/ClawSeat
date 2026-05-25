# ClawSeat 代码质量审计 (2026-05-12)

> **目的**: 对 ClawSeat v0.2.1 全仓做一次代码质量、可维护性、死代码、重复冗余、冲突逻辑的系统审计，作为下一阶段集中清理 + 修复 PR 的依据。
>
> **范围**: `/Users/ywf/coding/ClawSeat` main 分支（不含 `tests/__pycache__/`、`.gitignore` 已排除项、`adapters/projects/openclaw/` 上游集成层）。
>
> **方法**: 7 个并行 sub-agent 分领域深审 + 主轨自查跨切面 + 配置链路核对。约 95% 代码量已覆盖。
>
> **当前分支**: `main` (commit `0e89201` 时审计开始)
>
> **维护**: 本文件落盘后保持只读，新增 fix PR 都引用本文件章节号。

---

## 0. 总评

**架构本身健康** — `core/lib/` 14 模块定位清楚、引用关系干净；`core/adapter/` + `shells/_shim_base.py` 经历过 audit M1 已收敛；五座 seat 角色 + harness adapter + transport router 的三层结构在最近提交里仍被持续维护。**没有"烂架构"问题**。

**中层处于 schema 演进半路** — 同时有 v1/v2/dynamic-roster 三套 profile schema、有 fixed-roster vs dynamic-roster 双轨 transport、有 qa→patrol vocab 迁移残骸、cartooner-harness 与 gstack-harness 平行实现。每条迁移都没走到底——template 还在写 v1、validator 已经只认 v2、workspace regen 又把废弃字段塞回去。**是迁移中状态，不是腐烂**，但带来真实的 production bug 和高维护成本。

**用户面 sprawl 是噪音最大来源** — 27 个 `agent_admin_*.py` / 双重 shell tree (`scripts/` + `core/shell-scripts/` + `core/launchers/`) / 14+ 处可清理死代码 / 11 个 docs 互相矛盾 —— 这些不是 bug，但让"下一个读代码的人"得花一周时间消化。

---

## 1. 确认死代码（HIGH 置信度）

### 1.1 Python

| 路径 | 证据 |
|---|---|
| `core/launchers/agent-launcher-fuzzy.py` (333 LOC) | 全仓 0 caller；`agent-launcher.sh:24` 只引 `agent-launcher-discover.py` |
| `core/scripts/agent_admin.py:335` `heartbeat_receipt_path` | 顶层 wrapper，0 引用（`HEARTBEAT_HANDLERS.receipt_path` 是真入口） |
| `core/scripts/agent_admin.py:339` `load_heartbeat_manifest` | 同上 |
| `core/scripts/agent_admin.py:343` `load_heartbeat_receipt` | 同上 |
| `core/scripts/agent_admin.py:351` `heartbeat_install_fingerprint` | 同上 |
| `core/scripts/agent_admin.py:355` `heartbeat_receipt_matches_manifest` | 同上 |
| `core/scripts/agent_admin.py:359` `write_heartbeat_receipt` | 同上；这 6 个 wrapper 形成"假活 API"，污染 IDE 补全 |
| `core/skills/clawseat-install/scripts/init_specialist.py` | 仅 `tests/test_init_specialist.py` + `tests/test_install_qa_gaps.py` + `docs/ARCHITECTURE.md:204`（标 "legacy v0.5 / migration"）；无任何 shell/TOML/CLI 入口 |
| `core/skills/cartooner-harness/scripts/_common.py:469` `validate_project_id()` | 仅 _common.py 自调用（`:62, :86`），脚本层无导入 |
| ~~`core/skills/cartooner-harness/scripts/_common.py:193` `parse_brief()`~~ | ~~仅 `load_brief` 一次内部调用~~ — **撤销**：`tests/test_cartooner_harness_scripts.py:2424,3219` 直接调用，是 contract API |
| `core/scripts/build_product_bundle.py` | 仅 `tests/test_build_product_bundle.py` 引用，无运行时 caller |
| `core/scripts/refresh_all_workspaces.py` | 仅 `tests/test_refresh_all_workspaces.py` 引用，无运行时 caller |
| `core/scripts/iterm_tmux_selftest.py` (525 LOC) | 仅 `tests/test_portability.py:38` + `docs/ITERM_TMUX_REFERENCE.md:95`，运行时 0 调 |

### 1.2 Shell

| 路径 | 证据 |
|---|---|
| `scripts/migrate-workspace-memory.sh` | 唯一引用是脚本自己 usage 字符串 |
| `scripts/migrate-qa-to-patrol.sh` | QA→patrol 词汇 2026-04-29 已完成；仅 docs/test 引用 |
| `scripts/migrate_kb_singular.py` | 全仓 0 caller |
| `core/shell-scripts/agent-admin.sh` | 与 `agentctl.sh` 90% 重叠（diff 仅 14 行：缺 AGENT_HOME 兜底、缺 /opt/homebrew + /usr/local PATH 探测）；agentctl.sh 是较新版 |

### 1.3 字节码 / 测试墓碑

| 路径 | 类型 |
|---|---|
| `templates/__pycache__/clawseat-solo.cpython-312.pyc` | 孤儿（.toml 模板目录里出现 .pyc） |
| `tests/test_build_product_bundle.py` | 跟随 `build_product_bundle.py` |
| `tests/test_refresh_all_workspaces.py` | 跟随 `refresh_all_workspaces.py` |
| `tests/test_portability.py:38` | iterm_tmux_selftest 引用条目 |

---

## 2. 强烈可疑死代码（MED 置信度，需 owner 确认）

### 2.1 5 个"墓碑测试"模板

| 模板 | 测试 | 操作员是否手动用？ |
|---|---|---|
| `core/templates/workspace-builder.template.md.codex` | 无 | 未知 |
| `core/templates/workspace-builder-av.template.md.claude.minimax` | 无 | 未知 |
| `core/templates/workspace-patrol.template.md.claude.minimax` | 无 | 未知 |
| `core/templates/workspace-planner.template.md.gemini` | `tests/test_workspace_template_planner_gemini_renders.py` | 未知 |
| `core/templates/workspace-reviewer.template.md` | `tests/test_install_template_engineering.py` | 未知 |

证据：`core/scripts/agent_admin_template.py:230-232` 只动态加载 `workspace-memory.template.md.{variant}` 和 `workspace-memory-cartooner.template.md`；其他 5 个模板生产代码 0 加载，仅测试 `assert template.exists()`。

### 2.2 其他可疑

| 路径 | 备注 |
|---|---|
| `scripts/launch-grid.sh` | 仅 RFC 文档引用，runtime 0 调 |
| `scripts/task-watch.sh` | 仅 plist template 引用，没有 launchd 装载脚本 |
| `scripts/install.sh:58-59,126-127` `QA_HOOK_INSTALLER` / `QA_PATROL_CRON_INSTALLER` 别名 | patrol→qa 兼容 alias，QA 词已无 caller |
| `core/scripts/agent_admin_config.py:368,387` `provider_defaults` / `provider_url_markers` | 仅 config.py 内部使用，应私有化 |
| `core/skills/memory-oracle/scripts/query_memory.py:573-574,871` `--ask` 子命令 | 显式标 "Deprecated --ask path"，仍可路由 |
| external practice reference files | 已移除；ClawSeat seat 方法论回到本仓 seat/protocol 文档 |

---

## 3. 冲突逻辑（最严重的类别）

### 3.1 transport_router 方向感冲突
- `core/transport/transport_router.py` docstring 把 dynamic 视为 canonical
- 5 个 `core/migration/*_dynamic.py` 文件头标 `DEPRECATED (2026-04-22): transitional dynamic-roster compatibility shim`
- 路由层和被路由的脚本对未来方向的判断完全相反

### 3.2 4 对 static ↔ dynamic 已 drift

| 对 | static LOC | dynamic LOC | 比例 | drift 性质 |
|---|---|---|---|---|
| dispatch_task | 1124 | 304 | 27% | 意图+无意混合 |
| notify_seat | 133 | 77 | 58% | 无意（`notify_seat.py:70` 注释自承） |
| complete_handoff | 1382 | 333 | 24% | 部分意图+大量无意 |
| render_console | 187 | 172 | 92% | 意图 |

### 3.3 5 个由 drift 引出的潜在生产 bug
1. **dynamic profile 下 openclaw seat 通知会失败** — `notify_seat_dynamic.py:56-57` 无 seat_resolver / 无 feishu fallback
2. **dynamic 下 completion 无 lineage / branch-lock 校验** — `complete_handoff.py:197-357,723-841` 整段缺失
3. **dynamic 下 `--intent` 静默无效** — `dispatch_task.py:472-599` 的 INTENT_MAP 未移植
4. **dynamic 路径调飞书会 ImportError** — `dynamic_common.py:130-178` 未 re-export `send_feishu_user_message / broadcast_feishu_group_message / stable_dispatch_nonce`
5. **render_console payload 不对称** — dynamic 缺 `reminders` 字段

### 3.4 seat-role 两套权威认账不同

| 权威 | 位置 | 集合 |
|---|---|---|
| 严格 schema | `core/lib/profile_validator.py:36` `LEGAL_SEATS` | {ancestor, memory, planner, builder, reviewer, patrol, designer} —— 拒绝 koder/engineer |
| 宽松 runtime | `core/scripts/agent_admin_session_lifecycle.py:273-285` `_generic_role` | 接受 koder + engineer（→ builder），fallback `specialist` |
| 仅规范化 | `core/scripts/seat_roles.py:4` `normalize_seat_role` | 无 validation |

**真实后果**：含 `koder` 的 profile 会被 validator 拒绝，但 runtime 仍能跑。

### 3.5 profile schema 5 处定义不一致

| 位置 | 状态 |
|---|---|
| `core/lib/profile_validator.py` `PROFILE_SCHEMA_VERSION=2` | 拒绝 `heartbeat_*` / `runtime_seats` / `materialized_seats` / `feishu_group_id` |
| `core/skills/gstack-harness/scripts/_common/profile.py:32-72` `HarnessProfile` | 仍声明全部"被拒"字段 |
| `core/scripts/agent_admin_workspace.py:1041-1058` `PRESERVE_FIELDS` | 主动保留 validator 拒绝的字段 |
| `core/migration/dynamic_common.py:75-98` `HarnessProfile` | `legacy_seat_roles` 改为必填 |
| `core/templates/profile-dynamic.template.toml` | **写 `version=1`** + 写 7 个 validator 拒绝字段 |

**最严重事实**：模板写出来的 profile 开箱就过不了 validator。

### 3.6 `openclaw 租户` 概念 3 个键

| 键 | 位置 | 含义 |
|---|---|---|
| `ProjectBinding.openclaw_koder_agent` | `core/lib/project_binding.py` | 默认字面值 "koder" |
| `extras["openclaw_frontstage_tenant"]` | `core/scripts/agent_admin_layered.py:276` | 实际租户 id |
| `profile.openclaw_frontstage_agent` | `core/lib/profile_validator.py:208` | 引用 machine.toml 租户表 |

不是 alias，可能持不同值。

### 3.7 `dispatch_session` 三个命名

| 表面 | 键名 |
|---|---|
| 磁盘 (session.toml) | `session` |
| 内存对象（SeatRecord） | `session_name` |
| Brief frontmatter | `dispatch_session` |

### 3.8 tmux 会话存活判定 6 处实现

| 文件:行 | 函数 | 匹配方式 | 超时 | 异常处理 |
|---|---|---|---|---|
| `core/tui/machine_view.py:45` | `_tmux_has_session` | `-t "=<name>"` 精确 | 无 | shutil.which→False |
| `core/tui/ancestor_brief.py:117` | `_tmux_session_alive` | `-t "=<name>"` 精确 | 无 | 同上 |
| `core/scripts/agent_admin_window.py:110` | `tmux_has_session` | `-t name` 子串 | via retry | wraps tmux_with_retry |
| `core/scripts/agent_admin_layered.py:353` | `_tmux_session_alive` | `-t name` 子串 | 3s | catches 3 类 |
| `core/adapter/clawseat_adapter.py:540` | 内联 | `-t name` 子串 | 5s | 仅吞 TimeoutExpired |
| `scripts/task-watch.sh:78` | `capture_tmux` (Python in shell) | `-t "=<name>"` 精确 | 无 | 未捕获 |

**关键 bug**：3 处精确 vs 3 处子串。**session `mem` 在子串组会误匹配 `memory`**。

### 3.9 agent_admin 内部概念分叉

| 概念 | 实现 A | 实现 B | 触发场景 |
|---|---|---|---|
| seat-role 规范化 | `crud_engineer.py:18` 用 `normalize_seat_role` | `session_lifecycle.py:246-264` 内联剥 `minimal-/creative-/code-` 前缀 | 非规范 role 进 session.context 时两边看不同 |
| profile_path 解析 | `crud_bootstrap.py:134` + `crud_project.py:83` 直拼 `self._home()` | `layered.py:68` `project_profile_path()` 接受可选 home | home env override 时路径不同 |
| tmux 会话存活性 | 4 处独立实现（见 3.8） | — | 测试 monkey-patch 时行为分叉 |

### 3.10 cartooner-harness 内部 drift

- **spawn_subagent.py:207-218 vs spawn_lane.py:119**: 同一 race-condition fix（audit #3）应用到 spawn_lane，没回头修 spawn_subagent。**真实并发 bug**：两个进程并发 spawn subagent 时 PROJECT_INDEX.json 会丢写。
- **Wakeup 解析层数不对称**：deliver_brief.py:220-224（3 层）vs dispatch_brief.py:193-195 / spawn_lane.py:121-123 / deposit_asset.py:239-241,331-333（2 层）。Audit #8 cross-project case 只回移了 deliver_brief。

---

## 4. 重复冗余

### 4.1 agent_admin Facade 层叠
- `agent_admin_crud.py:14` 的 `CrudHandlers` 12 个 method 全部是 `return self.<X>.<same_name>(args)`
- `agent_admin.py:1162-1219` 又把它们各包一层 `cmd_<X> = CRUD_HANDLERS.<X>`
- **改一个签名要 3 处同步**

某些路径穿过 4 个文件：`cmd_project_koder_bind` (agent_admin.py:1218 → crud_validation.py:122 lazy import → layered.py:326 实际实现)

### 4.2 shell 辅助代码重复

| 模式 | 出现次数 | 位置举例 |
|---|---|---|
| `command -v <cmd>` 手写 | **25+** | install.sh, clean-slate.sh, task-watch.sh, install/lib/*, hooks/*, launchers/*, shell-scripts/* |
| Python 解释器探测 | **3** 处独立 | `agent-admin.sh:7-15`, `agentctl.sh:19-36`, `preflight.sh:59-74` |
| `tmux capture-pane -p -S -N` | **9** 处不同参数 | wait-for-seat.sh, install/lib/{project,window}.sh, shell-scripts/*, seat-diagnostic.sh |
| die/warn/note 三件套 | **2-3** 处重定义 | install.sh, apply-koder-overlay.sh, install/lib/preflight.sh |
| OAuth 文件探测 | **双轨** | detect.sh 的 `_detect_*_state` vs auth.sh 的 `resolve_*_secret_file` |
| `trap 'rm -f $tmp' EXIT` | **3** 处 | send-and-verify.sh:154, privacy-check.sh:45, install.sh:359 |

### 4.3 cartooner-harness 内部样板

| 模式 | 重复次数 | 位置 |
|---|---|---|
| `f"<prefix>-{secrets.token_hex(4)}"` ID 生成 | **5** | dispatch_brief.py:101, escalate_to_producer.py:79, iterate_prompt.py:81, spawn_lane.py:76, spawn_subagent.py:106 |
| `target_session = args.target_session.strip() or resolve_seat_session(...)` | **4** | 见 §3.10 |
| `send_wakeup → if not wakeup["ok"] → stderr` | **4** | spawn_lane:137-150, deposit_asset:249/340, deliver_brief:244, dispatch_brief:211 |

### 4.4 shell 双胞胎/边界混乱

- `core/launchers/{claude,codex,gemini}.sh`（1 行 wrapper） vs `core/launchers/runtimes/{claude,codex,gemini}.sh`（被 sourced 的实现） — 同名不同层
- `core/shell-scripts/agent-admin.sh` vs `agentctl.sh` — 90% 重叠

---

## 5. 维护性危险信号

### 5.1 类型系统 / 静态分析逃逸
- `core/scripts/agent_admin_session.py:3` `from agent_admin_session_base import *` 且 session_base 无 `__all__`，line 18-19 用 `# type: ignore[name-defined]` 抑制
- `core/scripts/agent_admin_layered.py:52-57` try/except 包 fallback stub `def validate_profile_v2(...): # type: ignore[no-redef]` —— **profile_validator 缺失时无 runtime 警告**
- `core/scripts/agent_admin_crud_validation.py:123,128` 方法体内 lazy import 做循环依赖

### 5.2 多继承 + 模块表挂实例
`core/scripts/agent_admin_session.py:14`:
```python
class SessionService(SessionRecovery, SessionStartLifecycle, SessionLaunchEnv): ...
    self._compat_module_globals = globals()
```
`session_lifecycle.py` 通过 `_compat_module_globals.get("tmux_has_session", ...)` 20+ 次反查 —— 测试 monkey-patch 通道污染生产路径。

### 5.3 沉默失败
- `core/skills/gstack-harness/scripts/bootstrap_harness.py:370` try/except 吞掉 `bootstrap_completeness` 异常
- `scripts/install/lib/secrets.sh:21` `case oauth|oauth_token) return 0` 让后 80 行对 oauth provider 永不执行
- `scripts/install/lib/detect.sh:30-50` `_detect_claude_state` 三套兜底，后两个分支是死路径
- `scripts/install/lib/window.sh:82-87` Linux 上 `MEMORY_PATROL_LAUNCHCTL_MISSING` die 因为上层 `ENABLE_AUTO_PATROL` 默认 0 而走不到

### 5.4 命名空间污染
`core/skills/gstack-harness/scripts/_common/__init__.py:15-101` 同时做绝对+相对两次 `from _utils import *`

### 5.5 bash 兼容性陷阱
`scripts/install/lib/self_update.sh:30`:
```bash
mapfile -t stale_projects < <(stale_workspace_projects "$new_sha")
```
macOS 默认 `/bin/bash` 是 3.2，`mapfile` 是 bash 4+ builtin。任何 `/bin/bash install.sh` 调用立即 `command not found`。

### 5.6 legacy 包袱仍在跑（除已知 `agent_admin_legacy.py`）
- `session_lifecycle.py:805-811` `stop_engineer(**legacy_kwargs)` 接受 `close_iterm_tab`
- `session_lifecycle.py:246-264` `minimal-/creative-/code-` 前缀剥离 + deprecated 警告
- `agent_admin_window.py:590-591` `--refresh-memories` "This legacy behavior is deprecated"
- `agent_admin_workspace.py:348,429,445` `CLAWSEAT_ENABLE_LEGACY_FEISHU_BROADCAST=1` 仍被 `_feishu.py:760` 读取
- `agent_admin_workspace.py:543` `~/.cartooner/_handoff/` 已删但文案仍提示

### 5.7 cartooner-harness `_common.py` 527 LOC 单文件过载
22 顶层 def 混杂 5 个关注点。对照 gstack-harness 在 ~250 LOC 时已拆 5 个子模块。

---

## 6. 文档 vs 代码漂移

### HIGH 严重度

| 文档 | 漂移点 |
|---|---|
| `docs/GSTACK.md:54-58` | 说 gstack skill 从 `~/.gstack/skills/` copytree —— 该路径不存在；真实路径是 `clawseat_root/core/skills/<skill>/`（见 `seat_claude_template.py:67`）；gstack 真实位置是 `~/.gstack/repos/gstack/` |
| `docs/GSTACK.md:295-297` | snippet 用 `SEAT_SKILL_MAPPING`（错），且把 value 当 list —— 实际是 `SEAT_SKILL_MAP`，value 是 `str`（`seat_skill_mapping.py:12-27`）；照抄会 ImportError + TypeError |

### MEDIUM 严重度

| 文档 | 漂移点 |
|---|---|
| `docs/GSTACK.md:38-43` Seat→Skill 表 | 缺 cartooner 三角色 (writer/builder-image/builder-av)；`qa` skill 目录不存在 |
| `docs/rfc/V2-VOCAB-DRIFT-AUDIT.md:18` `[DONE]` | "PROJECT_BINDING.toml 已替换为 project.toml + project-local.toml" —— 代码仍以 PROJECT_BINDING.toml 为 SSOT |
| `docs/HACKING.md:59` | 说 binding.py 是 "v3 schema" —— 与 V2-VOCAB-DRIFT-AUDIT D 节自相矛盾 |
| `manifest.toml` vs `templates/README.md:2` | manifest 把 `clawseat-creative` 注册为活跃 + install.sh / agent_admin_template.py 全把它当一等公民；README 说"2026-05-02 已废弃" |

### LOW
- `docs/rfc/V2-VOCAB-DRIFT-AUDIT.md:94` 仍列 `core/skills/qa/SKILL.md` —— qa 目录早已不存在

### 抽样未发现漂移（OK）
- `docs/CANONICAL-FLOW.md` dispatch 8 参数 vs `dispatch_task.py:699-803`
- `docs/auth-modes.md` migrate_seat_auth.py plan/apply
- `docs/INSTALL.md:536` bootstrap_machine_tenants.py 接口
- `docs/OPENCLAW.md:80` feishu_announcer + events_watcher

---

## 7. 7 个 skill SKILL.md 一致性

### 7.1 引用 / 脚本路径
- 所有 `core/references/*.md`、`core/schemas/*.json`、`docs/rfc/RFC-002-*` 等被 SKILL.md 引用的文件**全部存在**
- 所有 `dispatch_task.py / complete_handoff.py / verify_handoff.py / peer_deliver.py / minimax_readiness.py / send-and-verify.sh / agentctl` 路径全部命中

### 7.2 职责重叠

| skill A | skill B | 重叠点 |
|---|---|---|
| reviewer | designer | reviewer 自称"Replaces designer in engineering template"且包含"visual consistency review"；designer.SKILL.md 仍声明"UI/UX/a11y review" |
| reviewer | patrol | patrol 把 "review findings" 用作名词（drift findings），与 reviewer 同词，语义重叠 |

### 7.3 frontmatter schema 不统一
`related_skills` 字段在 builder/planner/designer/koder 出现，在 reviewer/patrol/peer 缺失。

---

## 8. 健康部分（确认无问题）

避免 false-positive 误删：
- `core/lib/` 14 模块 — 无 legacy/deprecated 标注，引用计数全 ≥ 1
- `shells/_shim_base.py` + 3 thin wrapper — audit M1 已 dedupe，最新状态健康
- `core/adapter/` 三个文件健康
- `core/scripts/agent_admin_crud.py` 是干净的 facade aggregator（不是冗余）
- `core/scripts/agent_admin_legacy.py` 只在 `migrate_legacy / migrate_session_model` 内 lazy-import（audit H8 已优化），是 transitional fossil 不是死代码
- 4 个 memory-related skill（clawseat-memory / clawseat-memory-reporting / memory-oracle / memory-report-mode）— 名字混淆但实际是 hub / reporting 协议 / 实际脚本 / auto-report mode 四个独立关注点
- `cs` vs `cs-workflow` — re-entry shortcut vs workflow DESIGN/EXECUTE，不重叠
- `lark-shared` / `tmux-basics` — 文档型 skill，被其他 skill 显式引用
- `.gitignore` — 正确排除 `__pycache__/`、`*.pyc`、`.DS_Store`、`/machine.toml`、`.agent/ops/`

---

## 9. 操作员手动 daemon（未自动接入）

5 个文件有完整 `--watch [--interval N]` 循环但没有 launchd plist / cron / install.sh 自动启动：

| 文件 | 文档定位 | 测试 | 自动启动 |
|---|---|---|---|
| `core/scripts/feishu_announcer.py` | "C11 first subscriber" | ✓ 56 tests | ✗ |
| `core/scripts/events_watcher.py` | "C10 passive re-ingest" | ✓ | ✗ |
| `core/scripts/state_admin.py` | "operator CLI for C8 state.db" | **0** | ✗ |
| `core/scripts/liveness_gate.py` | — | **0** | ✗ |
| `core/scripts/bootstrap_machine_tenants.py` | INSTALL.md:536 B2.5 | — | 手动 |

对比：`modal_detector.py` 自带 `--install-launchd` 装载 plist（行 253）。

---

## 10. 可执行修复方案（top 7 production bugs）

### 10.1 `profile-dynamic.template.toml v=1` vs validator 要求 v=2（最严重）

- **位置**: `core/templates/profile-dynamic.template.toml` 写 `version = 1`；`core/lib/profile_validator.py` `PROFILE_SCHEMA_VERSION = 2` + rule 8 拒绝 7 个字段
- **修复方向**（需产品决策）:
  - A. validator 放宽 rule 8 为 warn
  - B. 模板升级到 v2 schema + 同步 `agent_admin_workspace.py:1041-1058 PRESERVE_FIELDS`
  - C. 双 schema 共存
- **建议方向 B + 配套迁移**: 先跑 `core/skills/gstack-harness/scripts/migrate_profile.py` dry-run 全量
- **依赖**: §10.2 先行
- **估时**: 1-2 天

### 10.2 seat-role 两套权威认账不同

- **位置**: 见 §3.4
- **修复**:
  1. 决定 `koder` 是否第一公民 role（加入或不加入 `LEGAL_SEATS`）
  2. `engineer → builder` alias 表搬到 `seat_roles.py:normalize_seat_role`，validator 改为先 normalize 再 check
  3. `specialist` 加入 `LEGAL_SEATS`（当前 fallback 但不在合法集）
- **估时**: 半天

### 10.3 transport_router 双轨方向感反向

- **位置**: 见 §3.1-3.3
- **修复方向**（需产品决策）:
  - 决定 dynamic 是终态还是过渡，then collapse
- **短期可修的 5 个 drift bug**:
  1. `notify_seat_dynamic.py:56-57` 接 seat_resolver + feishu fallback
  2. `complete_handoff_dynamic.py` 加 lineage / branch-lock 校验
  3. `dispatch_task_dynamic.py` 加 INTENT_MAP + clear-audit
  4. `dynamic_common.py:130-178` 补 4 个飞书相关 re-export
  5. `render_console_dynamic.py` 加 `reminders` 字段
- **估时**: 决策半天；5 个 drift bug 2-3 天

### 10.4 cartooner-harness spawn_subagent race condition

- **位置**: `core/skills/cartooner-harness/scripts/spawn_subagent.py:207-218` 用 `load → mutate → write_project_index`（无锁）；对照 `spawn_lane.py:119` 用 `update_project_index`（有锁）
- **修复**: 改 `spawn_subagent.py:207-218` 为 `update_project_index(project_id, callback)` 闭包式
- **风险**: 极低
- **估时**: 1-2 小时

### 10.5 tmux 会话存活判定子串/精确匹配混用

- **位置**: 6 处实现（§3.8），3 处 `-t "=<name>"`（精确）vs 3 处 `-t name`（子串）
- **修复**:
  1. 新建 `core/lib/tmux.py`，抽 `tmux_session_alive(name, *, timeout=3.0) -> bool` 单实现
  2. 6 处 Python + 1 处 shell 全部改为复用
- **风险**: 中（依赖子串匹配的 caller 行为会变）
- **估时**: 半天

### 10.6 cartooner-harness wakeup 2/3 层不对称

- **位置**: `deliver_brief.py:220-224` 是 3 层；`dispatch_brief.py:193-195` / `spawn_lane.py:121-123` / `deposit_asset.py:239-241,331-333` 是 2 层
- **修复**: 在 `cartooner-harness/scripts/_common.py` 加 `resolve_wakeup_target(args, brief=None) -> str` 统一 3 层 fallback；4 个 caller 改为调用
- **风险**: 低（2→3 层是功能增强）
- **估时**: 1-2 小时

### 10.7 `openclaw 租户` 3 键命名混乱

- **位置**: 见 §3.6
- **修复**:
  - `openclaw_frontstage_tenant` → `openclaw_tenant_id`
  - `openclaw_frontstage_agent` → `openclaw_tenant_id`（与 binding 对齐）
  - `openclaw_koder_agent` → `openclaw_agent_name`
  - 加迁移代码 / 别名读取保护现存 binding
- **依赖**: §10.1 + §10.2
- **估时**: 1 天

---

## 11. 集中清理（一个 PR 解决 ~20 个 dead-code 项）

### 11.1 直接 `git rm`（HIGH 置信度）

```
core/launchers/agent-launcher-fuzzy.py
core/skills/clawseat-install/scripts/init_specialist.py
core/scripts/build_product_bundle.py        # 同删 tests/test_build_product_bundle.py
core/scripts/refresh_all_workspaces.py      # 同删 tests/test_refresh_all_workspaces.py
core/scripts/iterm_tmux_selftest.py         # 同删 tests/test_portability.py 第 38 行的引用
scripts/migrate-workspace-memory.sh
scripts/migrate-qa-to-patrol.sh
scripts/migrate_kb_singular.py
core/shell-scripts/agent-admin.sh           # 与 agentctl.sh 90% 重叠
templates/__pycache__/clawseat-solo.cpython-312.pyc  # 孤儿
```

### 11.2 编辑式删除（具体 file:line）

| 路径 | 行 | 操作 |
|---|---|---|
| `core/scripts/agent_admin.py` | 335, 339, 343, 351, 355, 359 | 删 6 个 heartbeat wrapper |
| `core/skills/cartooner-harness/scripts/_common.py` | 469 | 删 `validate_project_id`（仅自调用） |
| ~~`core/skills/cartooner-harness/scripts/_common.py`:193~~ | ~~193~~ | ~~删 `parse_brief`~~ — **取消**：`tests/test_cartooner_harness_scripts.py:2424,3219` 直接 `c.parse_brief(raw)`，是 contract API（2026-05-12 FIX-A 修复时发现） |
| `scripts/install.sh` | 58, 59, 126, 127 | 删 `QA_HOOK_INSTALLER` / `QA_PATROL_CRON_INSTALLER` 别名 |
| `core/scripts/agent_admin_config.py` | 368, 387 | 私有化为 `_provider_defaults` / `_provider_url_markers` |

### 11.3 待 owner 决策（MED）

| 项 | 决策点 |
|---|---|
| 5 个墓碑测试模板 + 6 个墓碑测试 | 操作员是否手动用 |
| 外部 practice reference 文件 | 已删；不作为 ClawSeat runtime / catalog / seat 上下文 |
| `prune_koder_todo_history.py` | one-time 保留 vs 删 |
| 5 个未自动启动 daemon | 接 launchd vs 文档明确 operator-run |

---

## 12. 集中重构（中等成本，高维护性收益）

### 12.1 抽 `scripts/_common.sh`（合并 25+ 处复制粘贴）
新建公共 helper：has_cmd / die / warn / note / find_python / tmux_session_alive_exact / tmux_capture_pane / mktemp+trap。26 个 shell 文件改 `source`。预期减少 ~300 行 bash 重复。**估时 1-2 天**

### 12.2 `agent_admin_*.py` facade 层叠 collapse
删 `agent_admin_crud.py` 的 12 个 pass-through method（保留 `__init__`）；`agent_admin.py:1162-1219` 直接 `cmd_<X> = CRUD_HANDLERS.<sub>.<X>`。**估时半天**

### 12.3 `SessionService` 多继承拆解
mixin 改 composition；删 `_compat_module_globals`；测试改正常 DI。**估时 1 天**

### 12.4 cartooner-harness `_common.py` 拆 5 子模块
仿 gstack 同样的模式拆 paths / index_io / brief_io / transport / toml_io。**估时 1 天**

---

## 13. 文档清理

### 13.1 必修
- `docs/GSTACK.md:54-58` `~/.gstack/skills/` 改成真实路径
- `docs/GSTACK.md:295-297` `SEAT_SKILL_MAPPING` → `SEAT_SKILL_MAP`，value 当 str 不当 list
- `docs/rfc/V2-VOCAB-DRIFT-AUDIT.md:18` D 类 `[DONE]` 重新判定
- `docs/rfc/V2-VOCAB-DRIFT-AUDIT.md:94` 删 `qa/SKILL.md` 陈旧条目
- `docs/HACKING.md:59` "v3 schema" 改为 "v2 schema"
- `clawseat-creative` 命运决策：彻底废弃 vs 重新接纳

### 13.2 选修
- 7 SKILL.md frontmatter schema 统一

---

## 14. 推荐执行顺序

1. **Week 1**: §11 集中清理（一个 PR，~20 项死代码） + §13.1 文档必修 + §10.4 spawn_subagent race（1-2h）+ §10.6 cartooner wakeup（1-2h）
2. **Week 2**: §10.5 tmux 匹配统一 + §12.1 shell `_common.sh` + §10.2 seat-role 决策
3. **Week 3**: §10.1 profile schema 升级（含 §10.7 命名）
4. **Week 4**: §10.3 transport_router 双轨决策 + 5 个 drift bug 修复 + §12.2/§12.3/§12.4 重构

---

## 15. 覆盖范围声明

| 区域 | 状态 | 覆盖方法 |
|---|---|---|
| `core/scripts/agent_admin_*.py` 27 文件 | ✓ | sub-agent A |
| `core/migration/*_dynamic.py` + gstack drift | ✓ | sub-agent B |
| shell scripts（21 大文件） | ✓ | sub-agent C |
| `core/skills/cartooner-harness/scripts/` 15 文件 | ✓ | sub-agent D |
| `core/skills/memory-oracle/scripts/` + `clawseat-install/scripts/` | ✓ | sub-agent E |
| 跨切面概念漂移 | ✓ | sub-agent F |
| 文档 vs 代码漂移 + 7 剩余 skill SKILL.md | ✓ | sub-agent G |
| `core/lib/` 14 模块 + `core/adapter/` + `shells/_shim_base.py` | ✓ | 主轨自查 |
| `core/templates/` + `templates/` | ✓ | 主轨自查 |
| `core/references/` | ✓ | 主轨自查 |
| `manifest.toml` / `marketplace.json` / `pyproject.toml` / `plugin.json` | ✓ | 主轨自查 |
| `tests/` 437 文件 macro 健康 | ✓ | 主轨自查 |
| 操作员 daemon 群 | ✓ | 主轨自查 |

**未深审但风险低**：
- `tests/test_cartooner_harness_scripts.py` 165 测试内部冗余度
- 8 个 doc-only skill 彼此一致性（lark-im / lark-shared / tmux-basics / clawseat-privacy / clawseat-intake / workflow-architect / clawseat-decision-escalation / memory-report-mode）
- `core/launchers/runtimes/*.sh` + `helpers/*.sh` 内部冗余

到此 ~95% 代码量已审过。

---

*审计完成: 2026-05-12*
