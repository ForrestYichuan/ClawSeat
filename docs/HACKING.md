# Hacking ClawSeat

> ClawSeat 约 350 个文件。全是 bash、Python、Markdown。没有编译步骤，没有
> plugin SDK 墙。改哪个文件就改哪个文件——效果即刻生效。本篇告诉你：**想
> 改 X，去哪里改**。

## 速查：想改 → 改哪里

| 我想... | 改这个 |
|---|---|
| **让某个 seat 行为变狠**（planner 更严 / builder 更激进） | `core/skills/<role>/SKILL.md`（纯自然语言，不是代码） |
| **加一个新 seat**（比如 `integrator`） | `core/scripts/seat_skill_mapping.py` 加一行映射；同名 skill 放 `core/skills/integrator/SKILL.md` |
| **删一个 seat**（workers 窗口里不想要 designer） | 项目的 `project-local.toml` 改 `seat_order`；或 install 时 `--template` 选不带 designer 的 |
| **给某 seat 换装 gstack skill**（planner 要加 `/cso` 安全审计） | `core/scripts/seat_skill_mapping.py` 对应 seat 的 skills 列表加一行 |
| **加一个新 intent**（比如 `--intent refactor`） | `core/skills/gstack-harness/scripts/dispatch_task.py::INTENT_MAP` 加一条键值对 |
| **换 LLM provider**（builder 不用 Claude 了改用 Codex） | 项目的 `project-local.toml::[overrides]` 改 `builder` 的 `tool` + `auth_mode` + `provider` |
| **换 LLM 模型**（换成某个 minimax 专属模型） | 同上，加 `model = "..."` |
| **换通道**（不用 Feishu，用 Slack） | 不是改 ClawSeat——去 [OpenClaw plugin SDK](https://github.com/openclaw/openclaw) 写一个 Slack 插件；ClawSeat 这侧把 `feishu_sender_mode` 关掉就行 |
| **加一个新模板**（自定义 roster） | 在 `templates/` 加一个 `.toml`，install 时 `--template <name>` |
| **改 install UX**（加新 flag / 改 banner） | `scripts/install.sh`（但这属于 code 改动，走 install 项目组 planner） |
| **给 memory 加一个 Phase-B 步骤** | bootstrap brief 模板加 B 系列步骤 |
| **改 dispatch 协议**（加新状态 / 改 handoff 格式） | `core/skills/gstack-harness/` 下脚本 + `references/chain-protocol.md` |
| **禁用/启用飞书通知** | `CLAWSEAT_FEISHU_ENABLED=0` 环境变量；或 `PROJECT_BINDING.toml` 删掉 `feishu_group_id` |
| **禁用/启用巡检 launchd** | `scripts/install.sh --enable-auto-patrol` 或装完后 `launchctl unload ~/Library/LaunchAgents/com.clawseat.*.plist` |
| **改 tools 隔离模式**（project-level 独立 `.lark-cli/`） | `PROJECT_BINDING.toml::tools_isolation = "per-project"`；然后 `agent_admin project init-tools` |
| **完全重启某个项目组** | kill 对应 tmux session + `agent_admin project delete` + `rm -rf ~/.agents/tasks/<proj>/`，再重跑 `install.sh` |

## 仓库布局

```
ClawSeat/
├── README.md                      # 开源首页（你现在在这）
├── LICENSE                        # MIT
├── scripts/
│   ├── install.sh                 # L1 — operator 入口
│   ├── clean-slate.sh             # 重置到装前状态
│   ├── apply-koder-overlay.sh     # 绑 OpenClaw agent 做反向信道
│   ├── recover-grid.sh            # iTerm workers 窗口被破坏时恢复
│   └── hooks/                     # seat 生命周期 hook
├── core/
│   ├── launchers/
│   │   └── agent-launcher.sh      # L3 — 真正 exec seat 的执行原语
│   ├── scripts/
│   │   ├── agent_admin.py         # L2 — session/seat/project CRUD 入口
│   │   ├── agent_admin_*.py       # 15 个 focused 模块
│   │   ├── seat_skill_mapping.py  # seat → skills 映射（你最常改的文件）
│   │   └── seat_claude_template.py # 把 skills copytree 进 sandbox
│   ├── skills/
│   │   ├── clawseat-memory/       # memory 专属
│   │   ├── planner/               # ClawSeat dispatch planner
│   │   ├── gstack-harness/        # dispatch 协议实现
│   │   ├── memory-oracle/         # memory seat 行为
│   │   └── ...                    # 其他 role skill
│   ├── shell-scripts/
│   │   ├── lark-cli               # sandbox HOME wrapper
│   │   ├── send-and-verify.sh     # seat 间消息发送
│   │   └── agentctl.sh            # 查 seat 状态
│   ├── lib/
│   │   ├── project_binding.py     # PROJECT_BINDING.toml binding schema (BINDING_SCHEMA_VERSION=3)
│   │   ├── project_tool_root.py   # per-project tool 隔离
│   │   └── ...
│   ├── transport/
│   │   └── transport_router.py    # dispatch/notify/complete 单一入口
│   └── templates/                 # bootstrap brief / patrol plist 模板源
├── templates/                     # 项目模板（default / engineering / creative）
├── docs/                          # 你现在读的这堆
├── tests/                         # pytest 套件（~2200+ 测试）
└── adapters/                      # harness 和 consumer 项目适配层
```

## 三层金字塔（知道谁调谁）

```
┌──────────────────────────────────────────────────────────────┐
│ L1:  scripts/install.sh           ← operator 首次 bootstrap  │
│      ↓                                                        │
│ L2:  core/scripts/agent_admin.py  ← 日常 seat/project/session │
│      ↓  (通过 subprocess 调用)                                 │
│ L3:  core/launchers/agent-launcher.sh ← 真正起 seat 进程      │
└──────────────────────────────────────────────────────────────┘
```

**永远不要跨层调用**：operator 不直接调 agent-launcher.sh；agent_admin
内部自己调 launcher；install.sh 起 seat 也走 agent_admin（不直接 exec
launcher）。

这条规则是 ARCHITECTURE.md §3z 写死的——违反了上游 workspace contract 会
报错。

## 沙箱 HOME 模型（想改隔离策略时必读）

每个 seat 跑在 `~/.agent-runtime/identities/<tool>/<auth>/<seat>/home/`
这样的沙箱 HOME 里。通过软链接让 sandbox HOME 看起来像真实 HOME：

```
~/.agent-runtime/identities/claude/api/planner/home/
├── .lark-cli       → 符号链接到 $REAL_HOME/.lark-cli/（或 project root）
├── .claude         → $REAL_HOME/.claude/
├── .codex          → $REAL_HOME/.codex/
├── .gemini         → $REAL_HOME/.gemini/
├── bin/
│   └── lark-cli    → $CLAWSEAT_ROOT/core/shell-scripts/lark-cli
│                     （HOME-override wrapper；路由 Keychain 查找）
└── Library/...     → iTerm 相关
```

两种隔离模式（`PROJECT_BINDING.toml::tools_isolation`）：

- **`shared-real-home`**（默认）——所有 seat 的 `.lark-cli` 都指向
  `$REAL_HOME/.lark-cli/`，共享 operator 的飞书身份
- **`per-project`**——每个项目独立的 `~/.agent-runtime/projects/<project>/`
  tool root，飞书身份不串

核心 trick：[`core/shell-scripts/lark-cli`](../core/shell-scripts/lark-cli)
wrapper 在 seat 沙箱里 PATH-inject 到 `$HOME/bin/lark-cli` 首位，运行时
把 `HOME` 重写成 `CLAWSEAT_PROJECT_TOOL_ROOT` 或 `AGENT_HOME`，让 macOS
Keychain 查询命中正确 namespace。

想改隔离逻辑？看 [`core/lib/project_tool_root.py`](../core/lib/project_tool_root.py) +
`agent-launcher.sh::seed_user_tool_dirs()`。

## Skill 怎么"生效"

Skill 是 `.md` 文件，不是代码。Claude Code 有一个叫 SSR（Scan-Select-Read）
的机制：agent 启动时扫 `.claude/skills/` 目录，记住所有 skill 的 description；
决定调 skill 时 Read 对应 SKILL.md 作为行动指令。

ClawSeat 的 [`seat_claude_template.py`](../core/scripts/seat_claude_template.py)
在 seat 启动时把对应 skill 目录完整 copytree 到：

```
~/.agents/engineers/<seat>/.claude-template/skills/
```

这个目录会被 agent-launcher.sh 软链接到 seat 沙箱的 `.claude/skills/`。
Claude Code 打开时自动发现。

**想让某个 skill 全局可用**：加到 `SHARED_SKILLS`（`seat_skill_mapping.py`
顶部），所有 seat 都会装。

**想让某 skill 只给特定 seat**：加到 `SEAT_SKILL_MAP[<role>]`。

## 调试技巧

**查当前 seat 跑什么**：

```bash
python3 core/scripts/agent_admin.py session list --project <project>
tmux capture-pane -t <project>-<seat>-<tool> -p | tail -30
```

**重放 dispatch 状态**：

```bash
python3 core/skills/gstack-harness/scripts/verify_handoff.py \
  --project <project> --task-id <id>
```

**查 state.db 最近事件**：

```bash
sqlite3 ~/.agents/state.db \
  "SELECT * FROM events ORDER BY created_at DESC LIMIT 20;"
```

**看 lark-cli wrapper 路由到哪**：

```bash
# 在 seat 沙箱里
echo "HOME=$HOME AGENT_HOME=$AGENT_HOME CLAWSEAT_PROJECT_TOOL_ROOT=$CLAWSEAT_PROJECT_TOOL_ROOT"
which lark-cli
readlink $(which lark-cli)
```

**跑测试**：

```bash
bash scripts/test-fast.sh        # 快速分层: 默认排除 host/slow
python3 -m pytest tests/ -q      # 完整本地套件
python3 -m pytest tests/ -q --durations=50
python3 scripts/test-suite-audit.py   # 审计 legacy/compat/removal 守卫测试

# 或针对某块：
python3 -m pytest tests/test_lark_cli_wrapper.py tests/test_launcher_project_tool_seed.py -q
```

测试分层 marker:

- `host`: 依赖维护者工作站状态、真实本地 repo、真实 profile 或用户级工具。
- `slow`: 触发 install 路径、稳定窗口等待或大量子进程的慢 smoke。
- `legacy`: 保护 deprecated / compatibility 行为，删除前必须单独审。
- `script`: shell / CLI / subprocess 表面测试。

CI 仍跑完整 `tests/`，但带 `--durations=50` 输出慢测试榜。日常迭代优先用
`scripts/test-fast.sh`，改 install / launcher / real-home 路径时再跑完整套件。
`legacy` 标记不等于“可删除”：它通常表示兼容、迁移或已移除路径的回归守卫。
需要清理时先跑 `python3 scripts/test-suite-audit.py`，按 `migration_or_compat` /
`removal_guard` / `stale_comment` / `review` 分类逐项处理。

## Fork 路径

想把 ClawSeat 拿去改成自己的东西？这样干：

1. `git clone https://github.com/KaneOrca/ClawSeat my-variant`
2. 改 `README.md` 把名字换成你的
3. 改 `core/scripts/seat_skill_mapping.py` 让 seat roster 符合你的团队
4. 改 `templates/*.toml` 加你自己的模板
5. 改 `scripts/install.sh` 里的 GitHub URL 指向你的 fork
6. 保留 `LICENSE` 里的原作者 attribution（MIT 要求）
7. Ship

三个上游（ClawSeat / OpenClaw / gstack）都 MIT，可以 fork 任意一个。

## 想让改动进主线

[`CONTRIBUTING.md`](../CONTRIBUTING.md) 有完整流程。缩略版：

- 修 bug / 小改 → PR 到 `experimental` 分支
- 大 feature → 先开 issue 讨论 scope
- 加 skill → 本地跑 `python3 core/scripts/agent_admin.py validate`，不要破坏
  role-first 默认 bootstrap
- 跨模块改动 → 先扫 `tests/test_transport_router.py::test_shared_task_io_helpers_do_not_fork`，
  防 helper 分叉

## 深入

- 架构细节：[`ARCHITECTURE.md`](ARCHITECTURE.md)
- 安装流程：[`INSTALL.md`](INSTALL.md)
- OpenClaw 集成：[`OPENCLAW.md`](OPENCLAW.md)
- gstack 集成：[`GSTACK.md`](GSTACK.md)
- agent_admin CLI 参考：[`AGENT_ADMIN.md`](AGENT_ADMIN.md)
- 认证模式：[`auth-modes.md`](auth-modes.md)
