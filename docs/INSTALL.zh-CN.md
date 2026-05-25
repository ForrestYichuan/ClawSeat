# ClawSeat v0.7 安装指南

> 🇬🇧 [English version](INSTALL.md)

## TL;DR（快速开始）

新项目运行：

```bash
bash ~/ClawSeat/scripts/install.sh --project <name>
```

这是唯一的用户入口。其他命令都是内部 plumbing，除非本文明确要求，不要直接调用。

> 注意：在 sandbox 环境（例如 agent-launcher）里，`~` 可能解析到非标准路径。
> 如果涉及 `~/ClawSeat/` 的命令表现异常，请使用绝对路径，例如
> `/Users/ywf/coding/ClawSeat/`，或你的实际 checkout 路径。

> 目标执行者：Claude Code / Codex / Gemini 这类 agent，不是人工逐行执行。
> 本文是 install SSOT。`scripts/install.sh` 负责 host bootstrap 和 runtime startup；
> memory prompt-ready 后，Phase-A 由 memory 接管。
> 安装后的扩展（koder overlay、新项目）见 §4-§5。

## 概述（Overview）

`install.sh` 是 ClawSeat 的 L1 用户入口。它完成以下事情：

| 步骤 | 执行者 | 发生什么 |
|------|--------|----------|
| 0 Agent kickoff | operator | 把 kickoff prompt 粘贴到新的 Claude Code / Codex / Gemini session |
| 1 Prerequisites | operator | `git clone` + `cd ~/ClawSeat` |
| 2 `install.sh` | script (auto) | host deps、env scan、provider pick、workers window、memories window、bypass flush |
| 3 Phase-A | memory CLI | B0-B7 交互式 bootstrap |
| 4 (optional) Koder overlay | memory 或 operator | `scripts/apply-koder-overlay.sh`，选择 OpenClaw agent 作为 Feishu reverse-channel koder |
| 5 (optional) Additional projects | operator | `bash scripts/install.sh --project <name>` |

支持 3 个内置模板：

| 模板 | Seat 数 | 定位 |
|------|---------|------|
| `clawseat-engineering` | 5 | 工程类：memory + planner + builder + reviewer + patrol，绑 gstack skill |
| `clawseat-creative` | 5 | 创意类（绑 cartooner skill）：memory + writer + builder-image + builder-av + patrol |
| `clawseat-solo` | v3 | legacy alias：seed `MULTI_TEAM_MINIMAL`，一个 project-memory 管 planner+builder 子项目组和 `quality-docs` |

为什么使用 `install.sh`，而不是直接用 `agent_admin` 或 `agent-launcher.sh`：

- `install.sh` 是 fresh-machine bootstrap 的 L1 入口。
- 现有项目的单个 seat 操作用 `agent_admin session start-engineer`。
- 单 seat 进程由系统内部调用 `agent-launcher.sh`，用户不要直接调用。
- 分层说明见 [docs/ARCHITECTURE.md §3z](ARCHITECTURE.md#seat-lifecycle-entry-points-v07-pyramid)。

## AI-Native Install Decision Tree

本节是 agent 面向 operator 的安装对话协议。Step 0 只运行一次检测，然后只做
五个计划内决策：语言、模板、项目名、摘要、执行。任意提示都支持 `/en` 和
`/zh` 切换语言；空回车接受默认值；`详` 给出约 150 字解释，不附外部链接。

### 步骤 0 — Language, Detection, And Start Gate

**WHAT**：静默运行 `bash scripts/install.sh --detect-only --force-repo-root <CLAWSEAT_ROOT>`，先总结 `detect_all` JSON，再问任何设置问题。

```json
{
  "oauth": {"claude": "oauth|api_key|missing", "codex": "oauth|api_key|missing", "gemini": "oauth|api_key|missing"},
  "pty": {"used": 0, "total": 256, "warn": false},
  "branch": {"branch": "main", "warn": false},
  "existing_projects": [],
  "timestamp": "2026-04-29T00:00:00Z"
}
```

**WHY default**：推荐★：沿用 operator 当前语言；理由：少问一个问题，同时仍可随时用 `/en` 或 `/zh` 切换。

**CONFIRM**：`可以开始吗? [回车=继续 / 详 / 取消]`

**ON-FAIL**：说明失败的 detector，然后给 2-3 个修复选项，例如 `--force-repo-root <path>`、重新登录缺失工具，或 PTY 耗尽时停止并升级。不要 kill session。

### 步骤 1 — Template Decision

**WHAT**：选择 `clawseat-engineering`、`clawseat-creative` 或 `clawseat-solo`。知道项目意图后用
`detect_template_from_name <project>`；如意图不清晰，默认 `clawseat-engineering`。

**WHY default**：默认 `clawseat-engineering`，因为它覆盖大多数 code-shipping 工作（memory + planner + builder + reviewer + patrol）。如果做图片 / 视频 / 音频 / 分镜这类绑 cartooner skill 的创作工作，选 `clawseat-creative`。

**CONFIRM**：`[回车=默认 / 1 engineering / 2 creative / 3 solo / 详 / 取消]`

**ON-FAIL**：operator 不确定时，每个模板给两个例子并保留默认；模板文件缺失时，提供 `git status`、`git pull` 或 `--force-repo-root <path>`。

### 步骤 2 — Project Name Decision

**WHAT**：推荐一个符合 `^[a-z0-9-]+$` 的小写项目名。`detect_all` 里的 `existing_projects` 是 **AVOID list**，只能用于避开已有项目，不能作为推荐名来源。推荐优先级是：operator goal > repo 目录名 > 带 timestamp 的 generated unique name。确认前必须和 `existing_projects` 做碰撞检查。

**WHY default**：推荐★：优先使用 operator goal；没有 goal 时使用不在 `existing_projects` 中的 repo 目录名；再不行生成 `<repo>-20260429-1251` 这类唯一名。理由：避免误把 `install` 或其他已有项目当成新项目名。

**CONFIRM**：`[回车=默认 / 输入新项目名 / 详 / 取消]`

**ON-FAIL**：项目名非法或已存在时，提供 normalized slug；只有 operator 明确要替换该项目时才提供 `--reinstall <project>`；否则提供带 timestamp 的唯一后缀。

### 步骤 3 — Summary, Run, And Progress

**WHAT**：展示一次摘要：语言、模板、项目名、repo root、branch warning、OAuth warning 和确切命令。然后询问执行确认。

**WHY default**：推荐★：运行生成的命令；理由：所有检测已完成，operator 已看过会改变结果的选择。

**CONFIRM**：`[回车=运行 / 修改摘要 / 详 / 取消]`

**ON-FAIL**：先分类失败，再给具体修复选项。可恢复提示用 `⚠️`，命令失败用 `❌`，可选跳过用 `⏭️`；不要只粘贴 raw stderr。

执行时严格按 11 步叙述：

1. 🟢 解析 flags 并确认 ClawSeat root。
2. 🟢 运行 preflight 和 environment scan。
3. 🟢 选择模板。
4. 🟢 确认项目名和路径。
5. 🟢 选择 provider 或 OAuth mode。
6. 🟢 渲染 memory bootstrap brief。
7. 🟢 启动 primary memory seat。
8. 🟢 写入 project registry 和 local config。
9. 🟢 打开 workers 或 solo window layout。
10. 🟢 写入 operator guide 和 kickoff。
11. 🟢 验证 Phase-A handoff；可选跳过标 `⏭️`，警告标 `⚠️`，失败标 `❌`。

## 0. Agent kickoff prompt（启动提示）

把下面提示贴给一个新的 Claude Code / Codex / Gemini session，让它安装 ClawSeat。
这个 prompt 只告诉 agent 去哪里读 playbook、需要询问哪些偏好；具体步骤属于本文。

```text
ClawSeat install — Agent kickoff prompt

You are invoked to install ClawSeat on this machine.

1. If `~/ClawSeat` does not exist, clone it first:
   `git clone https://github.com/KaneOrca/ClawSeat ~/ClawSeat`.
   Then read `~/ClawSeat/docs/INSTALL.md` (your <CLAWSEAT_ROOT>/docs/INSTALL.md)
   and execute it end-to-end.

   Sandbox note: if `~` resolves inside an agent runtime, use the absolute
   path `/Users/ywf/coding/ClawSeat/` or your actual ClawSeat checkout path.

2. Install preferences (operator fills these in before running):
   - Project name:           <PROJECT_NAME>       (e.g. "install" for first-time setup)
   - Repo root for seat cwd: <REPO_ROOT>          (default: CLAWSEAT_ROOT)
   - Seat harness:           <HARNESS_PREF>       (e.g. "default", "all seats claude+api+minimax")
   - Feishu mode:            <FEISHU_MODE>        ("enabled" | "disabled via CLAWSEAT_FEISHU_ENABLED=0")
   - Koder overlay:          <KODER_OVERLAY>      ("skip" | "apply tenant=<name>")

3. Consent & capability disclosure. The install playbook reads sensitive local
   state: shell environment, credential files (~/.claude/*, ~/.codex/*, macOS
   keychain entries, lark-cli session), provider API keys, and writes host
   artifacts under ~/.agents and ~/.openclaw. You are explicitly authorized to
   use platform capabilities — Bash, file Read/Write, `request_access`,
   `WebFetch`, MCP servers — as the playbook requires. Before first invoking
   each new category of access, tell the operator what you are about to access
   and why. If the operator declines, stop and ask for an alternative path.

4. Stop and ask the operator whenever a step is unclear, a prompt looks
   unfamiliar, or a command fails.

5. After install.sh exits, DO NOT end your session. Relay the completion banner,
   read OPERATOR-START-HERE.md, classify the memory pane state, paste the
   kickoff only if the pane is idle, and verify Phase-A is observably running.
```

同意优先是协议，不是礼貌用语。安装会读取 credential、API key、shell 环境，
并写入 `~/.agents/`、`~/.openclaw/`、LaunchAgent 等 host state。第一次访问
每类能力前都要说明。

Phase-A handoff 很重要：`install.sh` 准备 kickoff prompt，但调用它的 agent
必须确认 memory pane 已 ready 且 kickoff 已送达。Claude Code 首次启动可能出现
Bypass Permissions、Trust Folder、OAuth 等确认界面。不要把 `install.sh` 的
exit status 当成最终完成信号。

## Broadcast model（seat-by-seat）

ClawSeat 的 Feishu 是 write-only async notification，不订阅 Feishu。入站由
`koder` overlay 处理。

| Seat | Hook policy | Output channel |
|------|-------------|----------------|
| planner | Stop-hook every turn -> `lark-cli msg send` | 结构化摘要发 Feishu group，不发原始 transcript |
| memory | skill-driven + Stop-hook | 可选摘要广播；`[DELIVER:seat=<X>]` 走 durable memory delivery |
| builder / writer / visual / reviewer / patrol | none | CLI only，pane 可见 |

## 1. 前置依赖（Prerequisites）

窗口模式契约：

- ClawSeat 独立使用默认打开原生 iTerm workers / memories 窗口，因此该可视化模式依赖 macOS + iTerm2 + `iterm2` Python module。
- 配合 Cartooner 或内嵌终端使用时应传 `--no-window`；安装仍会启动 canonical tmux seats，但会跳过原生 iTerm 窗口以及 bootstrap 阶段的 iTerm hard check。
- `--mode multi` 是 v3 多团队 / profile render 路径；它渲染已批准的团队 proposals 与 workspace skeleton，不打开 iTerm 窗口。

推荐环境：

- macOS 14+。
- iTerm2，用于 standalone workers / memories 可视化窗口；Cartooner 集成模式可用 `--no-window` 跳过。
- `tmux`，用于每个 seat 的 canonical session。
- `git`，用于 clone、自更新和 worktree 识别。
- Python >= 3.11。`install.sh` 会自动解析 `python3.13`、`python3.12`、`python3.11`、
  `/opt/homebrew/bin/python3.12` 等常见路径。
- Claude Code / Codex / Gemini 至少一种已登录，或准备好 API key。

安装 checkout：

```bash
git clone <repo-url> "$HOME/ClawSeat"
cd "$HOME/ClawSeat"
export CLAWSEAT_ROOT="$PWD"
export PROJECT_NAME=install
```

验证：

```bash
test -e "$CLAWSEAT_ROOT/.git" && test -f "$CLAWSEAT_ROOT/scripts/install.sh"
```

失败时：

```text
INSTALL_BROKEN: repository missing or scripts/install.sh not found
```

## 2. 运行 `install.sh`（automatic bootstrap）

```bash
cd "$CLAWSEAT_ROOT"
bash scripts/install.sh
```

交互模式（kind-first）：如果没有传 `--template` 和 `--project`，`install.sh`
会先问项目类型（创作 / 工程 / solo），再根据类型显示项目名 placeholder。
CI / sandbox / agent-launcher 等 Non-TTY 环境不会进入交互提示，必须显式传
`--project <name> --template <kind>`。

Dry-run preflight：

```bash
bash scripts/install.sh --dry-run
```

Non-interactive provider shortcuts：

```bash
bash scripts/install.sh --provider minimax
bash scripts/install.sh --provider minimax --api-key sk-cp-...
bash scripts/install.sh --provider anthropic_console --api-key sk-ant-...
bash scripts/install.sh --base-url https://api.example.invalid --api-key sk-test --model claude-sonnet
```

常用扩展 flag：

```bash
# Install for a different project's repo
bash scripts/install.sh --project myproject --repo-root /path/to/myproject

# Override the ClawSeat install code root on multi-worktree machines
bash scripts/install.sh --project myproject --force-repo-root /Users/you/ClawSeat

# Choose a non-Claude primary memory tool
bash scripts/install.sh --memory-tool codex --memory-model gpt-5.2
bash scripts/install.sh --memory-tool gemini

# Force every API-auth worker seat onto the same provider family
bash scripts/install.sh --all-api-provider minimax

# Install the optional patrol cron entries
bash scripts/install.sh --enable-auto-patrol

# Mirror every bundled ClawSeat skill into all supported tool homes
bash scripts/install.sh --load-all-skills

# Disable Feishu notifications
CLAWSEAT_FEISHU_ENABLED=0 bash scripts/install.sh --project myproj

# Cartooner / embedded-terminal mode: tmux seats, no native iTerm windows
bash scripts/install.sh --project myproj --template clawseat-creative --provider minimax --no-window

# v3 multi-team dry-run after memory has written approved team proposals
bash scripts/install.sh --mode multi --project myproj --teams core,content --dry-run

# 最小 v3 项目组：solo alias 会 seed MULTI_TEAM_MINIMAL proposals
bash scripts/install.sh --project myproj --template clawseat-solo --dry-run

# Forget remembered per-seat harness choices from a previous run
bash scripts/install.sh --reset-harness-memory
```

### 命令行选项参考（CLI Flags Reference）

| Flag | 含义 |
|------|------|
| `--project <name>` | 安装或重装指定 ClawSeat project。默认 `install`。 |
| `--repo-root <path>` | 设置 seat cwd 使用的目标项目仓库。 |
| `--force-repo-root <path>` | 多 worktree 自动选择错误时，强制指定 ClawSeat install code root。 |
| `--template <clawseat-engineering\|clawseat-creative\|clawseat-solo>` | 选择 install template。`clawseat-solo` 现在是 v3 `MULTI_TEAM_MINIMAL` 兼容别名，不再是独立 single-mode roster。 |
| `--memory-tool <claude\|codex\|gemini>` | 覆盖 primary memory seat tool。非 Claude tool 会跳过 Claude provider selection。 |
| `--memory-model <model>` | 当 memory tool 支持显式 model 时设置 memory model。 |
| `--provider <mode\|n>` | 通过 detected candidate number 或 mode 选择 memory-seat provider。 |
| `--all-api-provider <provider>` | 覆盖所有 API-auth worker seat provider。支持 `minimax`、`deepseek`、`ark`、`xcode-best`、`anthropic_console`、`custom_api`。 |
| `--base-url <url> --api-key <key> [--model <name>]` | 强制 memory seat 使用自定义 Claude-compatible API provider。 |
| `--api-key <key> [--model <name>]` | 与 `--provider minimax\|deepseek\|ark\|xcode-best\|anthropic_console` 配合，显式指定 provider。 |
| `--reinstall` / `--force` | 重建已存在 project，而不是在 `phase=ready` 时退出。后续 bare word 兼容视为 project name。 |
| `--uninstall <project>` | 从 projects registry 移除 project。 |
| `--enable-auto-patrol` | 安装可选 daily/weekly patrol cron。未传时 preflight 会移除 stale patrol LaunchAgent。 |
| `--load-all-skills` | 为非 Claude tool 也安装所有 bundled ClawSeat skills。Claude 总是获得完整集合。 |
| `--dry-run` | 打印计划动作，尽可能不修改 host state。 |
| `--detect-only` | 打印一次 `detect_all` JSON 环境摘要，并在产生安装副作用前退出。 |
| `--no-window` | 跳过原生 iTerm workers/memories 窗口和 bootstrap 阶段 iTerm hard check；tmux seats 仍会启动，可从内嵌终端 attach。 |
| `--reset-harness-memory` | 删除 remembered per-seat harness choices 并退出。 |
| `--help` / `-h` | 打印 parser-owned usage line。 |

### Provider 选项 ↔ CLI Flag 映射（Z3）

Non-TTY 环境（agent-launcher、CI、scripts）必须用显式 flag 跳过交互 provider menu。
数字 menu choice 由本机 credential 动态检测生成；稳定 CLI contract 是下面这些 mode name：

| 稳定 mode | 描述 | CLI flag |
|------|------|----------|
| `anthropic_console` | Claude memory + Anthropic Console API key | `--provider anthropic_console --api-key <key>` |
| `oauth_token` | Claude memory + Claude Code OAuth token | `--provider oauth_token` |
| `oauth` | Claude memory + host Claude OAuth | `--provider oauth` |
| `minimax` | MiniMax API | `--provider minimax --api-key <key>` |
| `deepseek` | DeepSeek API | `--provider deepseek --api-key <key>` |
| `ark` | Ark API | `--provider ark --api-key <key>` |
| `xcode-best` | Xcode-best Claude-compatible API | `--provider xcode-best --api-key <key> [--model <name>]` |
| `custom_api` | 自定义 Claude-compatible endpoint | `--provider custom_api --base-url <url> --api-key <key> [--model <name>]` |
| `gemini` memory tool | Gemini OAuth primary memory | `--memory-tool gemini` |
| `codex` memory tool | Codex OAuth primary memory | `--memory-tool codex [--memory-model <model>]` |

当前 parser-owned provider 行为见 `scripts/install/lib/provider.sh::select_provider()`。
不要使用旧别名 `--provider anthropic`、`--provider claude_code`、
`--provider gemini_oauth`、`--provider codex_oauth`、`--provider custom`；
它们保留在旧计划文本里，但不是当前可运行接口。

安全提醒：`--api-key` 会出现在 `ps` output 和 shell history 中。优先用环境变量：

```bash
export ANTHROPIC_BASE_URL=https://api.example.invalid
export ANTHROPIC_API_KEY=sk-test
bash scripts/install.sh --provider custom_api
```

只在 CI、agent automation、no-env、no-tty 场景使用 `--base-url + --api-key`。

### Non-TTY Guard（非交互终端，Z2）

`agent-launcher`、CI、scripted subprocess 通常不是 TTY。AA 之前的旧测试曾用
`input="1\n"` 模拟菜单选择；现在这是明确禁止的路径。

- 缺少 `--project` / `--template` 且进入 kind-first prompt：退出 `2`，错误码 `NON_TTY_NO_TEMPLATE`。
- 缺少 `--provider` 且 provider auto-detection 不足：退出 `2`，错误码 `NON_TTY_NO_PROVIDER`。
- 正确做法：显式传 `--project <name> --template <name> --provider <mode|n>`，或传 `--base-url --api-key`。

### Sandbox HOME（沙箱 HOME，Z4）

`install.sh` 启动时会检查 `$HOME` 与 `~` / real operator HOME 的关系。如果发现
sandbox HOME 或解析不一致，会输出类似：

```text
WARN: sandbox HOME: $HOME=/tmp/... but ~ resolves to /Users/...
WARN: Use absolute paths. Some INSTALL.md examples with ~ may fail.
```

在 sandbox 中执行安装时，优先使用绝对路径，例如：

```bash
bash /Users/ywf/coding/ClawSeat/scripts/install.sh --project myproject --template clawseat-solo --provider oauth
```

### Trust Folder 自动确认（Z5）

Claude Code 首次进入某个目录时可能显示 `Trust folder`、`Quick safety check`
或 `Do you trust the files in this folder`。`install.sh` 在 tool=`claude` 的
primary seat spawn 后，会用 `tmux capture-pane -S -50` 检查近期 pane 内容；
若检测到这些提示，会发送 Enter 自动确认。Codex / Gemini 不走这个 helper。

### capture-pane 调试（Z6）

刚启动的 pane 用：

```bash
tmux capture-pane -t <session> -p
```

可能返回空，因为 visible buffer 还没填满。调试时抓 scroll history：

```bash
# 推荐：抓最近 50 行，包括 scroll history
tmux capture-pane -t <session>:<window>.<pane> -p -S -50

# 抓整个 scrollback buffer
tmux capture-pane -t <session> -p -S - -E -
```

排查 Claude Code 首次启动的 `Trust folder` / `Quick safety check` 时尤其要用 `-S -50`。

### `install.sh` 执行顺序

1. Parse flags，解析 real user HOME，必要时选择 freshest ClawSeat worktree，并加载 `scripts/install/lib/`。
2. 在 import `tomllib` 之前解析 Python >= 3.11。
3. 解析 project template 和 roster：`clawseat-engineering` -> `memory, planner, builder, reviewer, patrol`；`clawseat-creative` -> `memory, writer, builder-image, builder-av, patrol`（绑 cartooner skill）；`clawseat-solo` -> v3 `MULTI_TEAM_MINIMAL`，seed planner+builder 子项目组和 `quality-docs` 后委托 `install_multi.sh`。
4. 运行 legacy path migration 和 seat liveness reconciliation。
5. 验证 host deps 并运行 `core/skills/memory-oracle/scripts/scan_environment.py --output ~/.agents/memory/`，
   生成 `machine/{credentials,network,openclaw,github,current_context}.json`。
6. 选择 primary memory provider。`--memory-tool codex|gemini` 跳过 Claude provider selection；
   Claude memory 从 `credentials.json` 构造 candidates。
7. 写 `~/.agents/tasks/<project>/memory-provider.env`。
8. 渲染 `~/.agents/tasks/<project>/patrol/handoffs/memory-bootstrap.md`。
9. 只通过 `core/launchers/agent-launcher.sh` 启动 `<project>-memory`，并使用 sandbox HOME isolation。
10. Bootstrap 或 migrate project profile，seed secrets，安装 skill mirrors、privacy hooks、project registry。
11. 用 `core/scripts/iterm_panes_driver.py` 打开 per-project workers window。
12. 确保 shared memories window 存在，每个 project memory 一个 tab。
13. 写 Phase-A kickoff prompt 并打印 operator banner。

验证：

```bash
test -d ~/.agents/tasks/install/patrol/handoffs
for f in credentials network openclaw github current_context; do
  test -f ~/.agents/memory/machine/$f.json || echo "MISSING $f"
done
for s in install-memory; do
  tmux has-session -t "$s" || echo "MISSING $s"
done
for s in install-planner install-builder install-reviewer install-patrol install-writer install-visual; do
  tmux has-session -t "$s" 2>/dev/null && echo "UNEXPECTED $s"
done
```

## 3. Operator 粘贴 prompt；memory 跑 Phase-A

`install.sh` 结束后会打印 banner。这个 banner 不是最终完成，只表示 memory prompt-ready。
operator 或调用 agent 必须：

1. Relay banner。
2. 读 `~/.agents/tasks/<PROJECT_NAME>/OPERATOR-START-HERE.md`。
3. 用 `tmux capture-pane -t '<PROJECT_NAME>-memory' -p | tail -15` 分类 memory pane：
   - State A：Phase-A 已经在跑，或 kickoff in-flight，不要重复粘贴。
   - State B：卡在 confirmation screen，清掉确认后重新 capture。
   - State C：idle input prompt，此时才手动粘贴 kickoff。
4. 再用 `tmux capture-pane -t '<PROJECT_NAME>-memory' -p | tail -10` 确认 B0 正在处理。

如果 provider menu 出现在 smoke / CI / sandbox run 中，用 `--provider 1` 或
`CLAWSEAT_INSTALL_PROVIDER=1` 选择第一个 detected candidate，不要用 piped stdin。

## 3.5 Sandbox / headless installs

`install.sh` 写 project state 到 `real_user_home()`，不是 caller 的 sandbox `HOME`。
如果从 seat sandbox 或 headless runtime 启动安装：

- Step 7/8 iTerm open 是 best-effort。macOS、`iterm2` import、driver bootstrap 失败时输出 `WARN:` 并继续。
- `ITERM_LAYOUT_FAILED` 仍是 hard failure，因为 driver 返回非 `ok` layout payload 表示 GUI 真问题。
- 恢复窗口仍走 canonical path：`agent_admin window open-grid <project> [--recover]`。

### Identity switch（项目身份切换）

`agent_admin project switch-identity <project> --tool feishu|gemini|codex --identity ...`
只更新 project-local identity metadata，并 reseed 现有 seat sandbox。

## 4. (Optional) Apply koder overlay - Feishu reverse channel

如果需要 Feishu 入站消息进入某个 seat，memory 或 operator 可运行：

```bash
bash scripts/apply-koder-overlay.sh
```

它会选择一个 OpenClaw agent，应用 koder overlay，并把 Feishu 消息转发进目标 seat。
不需要 Feishu 时跳过。ClawSeat 默认只做 outbound notification。

## 5. (Optional) Launch additional projects

创建新 project：

```bash
bash scripts/install.sh --project <new-name> --provider minimax
```

创建 clawseat-creative project（绑 cartooner skill 的 5-seat 创作团队）：

```bash
bash scripts/install.sh \
  --project mycreative \
  --template clawseat-creative \
  --provider oauth
```

创建 engineering project：

```bash
bash scripts/install.sh \
  --project myservice \
  --template clawseat-engineering \
  --provider minimax
```

直接用 `agent_admin` bootstrap（高级路径）：

```bash
python3 core/scripts/agent_admin.py project bootstrap \
  --template clawseat-engineering \
  --local ~/.agents/tasks/myproject/project-local.toml

python3 core/scripts/agent_admin.py project use myproject
```

切换上下文：

```bash
python3 core/scripts/agent_admin.py project use <project>
```

退役 install project：

```bash
INSTALL=install
tmux kill-session -t "${INSTALL}-memory" 2>/dev/null || true
# agent_admin project delete "$INSTALL"   # only if you want to wipe state
```

## 多 Worktree 机器（Multi-worktree，V-dispatch）

如果你同时维护多个 ClawSeat worktree（例如 `/Users/you/ClawSeat` 和
`/Users/you/coding/ClawSeat`），`install.sh` 会自动选择 freshest install code root：

- 优先选择 `main` 分支上的 worktree。
- 跳过 detached HEAD。
- 跳过落后 main 的 stale worktree，并输出 warning。
- 这样可避免 ClawSeat skill symlink 指向旧 SKILL 内容。

覆盖自动检测：

```bash
bash scripts/install.sh --project myproject --force-repo-root /Users/you/coding/ClawSeat
```

`--repo-root` 仍表示目标项目仓库。只有需要覆盖 ClawSeat install code root 时才用
`--force-repo-root`。

安装期间，`~/.agents/skills/` 是 skill symlink source of truth。`install.sh`
会镜像到 `~/.claude/skills/`、`~/.gemini/skills/`、`~/.codex/skills/`，让所有
支持的 tool 都能发现同一套 ClawSeat-visible skill set。

## 错误码列表（Error Codes）

常见 install-script failures：

| Code | 症状 | 恢复方式 |
|------|------|----------|
| `MISSING_PYTHON311` / `INVALID_PYTHON_BIN` | Python 缺失、版本过低，或 `PYTHON_BIN` 指向不支持的 executable。 | 安装/指定 Python 3.11+ 后重跑 Step 2。 |
| `PREFLIGHT_FAILED` | bootstrap preflight 发现 hard block。 | 按输出的 fix command 修复后重跑 Step 2。 |
| `ENV_SCAN_INCOMPLETE` | 缺少必需的 `~/.agents/memory/machine/*.json` scan artifact。 | 重跑 Step 2；若重复出现，检查 `scan_environment.py` output。 |
| `PROFILE_RENDER_MISSING` | `agent_admin project bootstrap` 返回成功，但没有写 `~/.agents/profiles/<project>-profile-dynamic.toml`。 | 更新 ClawSeat 后 reinstall，或检查 `agent_admin_crud_bootstrap.py` 的 profile render。 |
| `NON_TTY_NO_TEMPLATE` | project/template selection 需要输入，但 stdin/stdout 不是 TTY。 | 传 `--project <name> --template <name>`。 |
| `NON_TTY_NO_PROVIDER` / `INTERACTIVE_REQUIRED` | provider selection 需要输入，但 stdin/stdout 不是 TTY。 | 传 `--provider <n|mode>` 或 `--base-url --api-key`。 |
| `PROVIDER_NOT_FOUND` / `INVALID_PROVIDER_CHOICE` | 指定的 provider mode 或 candidate number 不可用。 | 取消 override 重新选择，或传显式 API flags。 |
| `ITERM2_PYTHON_MISSING` / `ITERM_DRIVER_FAILED` / `ITERM_LAYOUT_FAILED` | iTerm2 pane 创建失败或 layout payload 异常。 | 检查 iTerm2 automation 和 Python SDK access 后重跑。 |
| `TMUX_SESSION_CREATE_FAILED` / `TMUX_SESSION_DIED_AFTER_LAUNCH` | seat tmux session 启动失败或启动后消失。 | 查看 tmux stderr 和 seat workspace，再重启对应 seat。 |
| `SKILL_SYMLINK_DIR_FAILED` / `SKILL_SYMLINK_FAILED` | skill symlink 目录创建或链接失败。 | 检查 `~/.agents/skills/`、`~/.claude/skills/`、`~/.gemini/skills/`、`~/.codex/skills/` 权限。 |

Full install-script error-code inventory from `scripts/install.sh` and `scripts/install/lib/*.sh`：

| Source | Codes |
|--------|-------|
| `install.sh` | `COMMAND_FAILED`, `INVALID_FLAGS`, `INVALID_MEMORY_MODEL`, `INVALID_MEMORY_TOOL`, `INVALID_MODE`, `INVALID_PROJECT` |
| | `INVALID_REPO_ROOT`, `INVALID_TEMPLATE`, `MISSING_SCRIPT`, `UNKNOWN_FLAG` |
| `lib/preflight.sh` | `ENV_SCAN_INCOMPLETE`, `INVALID_PYTHON_BIN`, `MISSING_PYTHON311`, `PREFLIGHT_FAILED` |
| `lib/project.sh` | `AGENT_ADMIN_MISSING`, `BRIEF_CHMOD_FAILED`, `GUIDE_CHMOD_FAILED`, `GUIDE_DIR_FAILED`, `INVALID_PROJECT` |
| | `KICKOFF_CHMOD_FAILED`, `KICKOFF_DIR_FAILED`, `KICKOFF_WRITE_FAILED`, `PROJECTS_JSON_ACTION_UNKNOWN`, `PROJECTS_REGISTRY_MISSING` |
| | `PROFILE_RENDER_MISSING`, `PROJECT_BOOTSTRAP_FAILED`, `PROJECT_LOCAL_CHMOD_FAILED`, `PROJECT_LOCAL_DIR_FAILED` |
| | `PROJECT_PROFILE_BACKUP_FAILED`, `PROJECT_PROFILE_MIGRATE_FAILED`, `PROJECT_WORKSPACE_REGEN_FAILED`, `PATROL_ENGINEER_CREATE_FAILED`, `REINSTALL_BACKUP_FAILED` |
| | `PROFILE_REMOVE_FAILED`, `REINSTALL_PROJECT_MISSING` |
| | `NON_TTY_NO_TEMPLATE`, `TEMPLATE_CHMOD_FAILED`, `TEMPLATE_DIR_CREATE_FAILED`, `TEMPLATE_MISSING`, `TEMPLATE_ROOT_CREATE_FAILED` |
| | `WAIT_SCRIPT_MISSING` |
| `lib/provider.sh` | `INTERACTIVE_REQUIRED`, `INVALID_PROVIDER_CHOICE`, `NON_TTY_NO_PROVIDER`, `PROVIDER_ENV_CHMOD_FAILED`, `PROVIDER_ENV_DIR_FAILED` |
| | `PROVIDER_ENV_WRITE_FAILED`, `PROVIDER_INPUT_MISSING`, `PROVIDER_MODE_UNKNOWN`, `PROVIDER_NOT_FOUND` |
| `lib/secrets.sh` | `DEEPSEEK_SECRET_CHMOD_FAILED`, `DEEPSEEK_SECRET_DIR_FAILED`, `DEEPSEEK_SECRET_WRITE_FAILED` |
| | `PRIVACY_KB_CHMOD_FAILED`, `PRIVACY_KB_DIR_FAILED`, `PRIVACY_KB_WRITE_FAILED`, `PROJECT_SECRET_CHMOD_FAILED` |
| | `PROJECT_SECRET_DIR_FAILED`, `PROJECT_SECRET_WRITE_FAILED`, `PROVIDER_MODE_UNKNOWN` |
| `lib/skills.sh` | `PRIVACY_HOOK_CHMOD_FAILED`, `PRIVACY_HOOK_DIR_FAILED`, `PRIVACY_HOOK_PRESERVE_FAILED` |
| | `PRIVACY_HOOK_WRITE_FAILED`, `SKILL_SYMLINK_DIR_FAILED`, `SKILL_SYMLINK_FAILED` |
| `lib/window.sh` | `ITERM2_PYTHON_MISSING`, `ITERM_DRIVER_FAILED`, `ITERM_FOCUS_FAILED`, `ITERM_LAYOUT_FAILED` |
| | `ITERM_MACOS_ONLY`, `MEMORY_PATROL_BOOTSTRAP_FAILED`, `MEMORY_PATROL_CHMOD_FAILED`, `MEMORY_PATROL_DIR_FAILED` |
| | `MEMORY_PATROL_INVALID`, `MEMORY_PATROL_LAUNCHCTL_MISSING`, `MEMORY_PATROL_RENDER_FAILED` |
| | `MEMORY_PATROL_TEMPLATE_MISSING`, `TMUX_CWD_CREATE_FAILED`, `TMUX_SESSION_CREATE_FAILED` |
| | `TMUX_SESSION_DIED_AFTER_LAUNCH` |

Phase-A 和 optional overlay 的失败由 memory 或 helper scripts 发出，不属于
`scripts/install.sh` 自身 error-code inventory。

## 故障排除（Troubleshooting）

1. **Non-TTY 环境失败**：看到 `NON_TTY_NO_PROVIDER` 或 `NON_TTY_NO_TEMPLATE` 时，
   不要用 piped stdin。传 `--project`、`--template`、`--provider`。
2. **`~` 指向错误目录**：看到 sandbox HOME warning 时，改用绝对路径，例如
   `/Users/ywf/coding/ClawSeat/scripts/install.sh`。
3. **provider candidate 不存在**：看到 `PROVIDER_NOT_FOUND` 时，先不传 `--provider`
   跑一次，让 installer 打印 detected candidates；CI 里可用 `--provider 1`。
4. **Claude Code 卡在 Trust folder**：用 `tmux capture-pane -t <session> -p -S -50`
   检查；tool=`claude` 的 primary seat 会尝试自动 Enter。
5. **pane capture 为空**：不要只用 `tmux capture-pane -p`，改用 `-S -50`。
6. **多 worktree 指错版本**：看 install 输出中的 stale/detached warning；必要时传
   `--force-repo-root /absolute/path/to/ClawSeat`。
7. **iTerm 窗口没打开**：sandbox/headless 下可能只是 warn；恢复窗口用
   `agent_admin window open-grid <project> --recover`。
8. **skill 在 Claude/Codex/Gemini 看不到**：确认 `~/.agents/skills/` 是 source of truth，
   且 `~/.claude/skills/`、`~/.gemini/skills/`、`~/.codex/skills/` 已镜像。
9. **项目已 ready 直接退出**：传 `--reinstall` 或 `--force` 重建。
10. **`dispatch_task.py` 报 profile not found**：看到
    `FileNotFoundError: ~/.agents/profiles/<project>-profile-dynamic.toml` 时，
    说明 project bootstrap 没写出 dispatch 所需的 dynamic profile。优先运行：
    `bash ~/ClawSeat/scripts/install.sh --project <project> --reinstall`，让 bootstrap
    重新渲染完整 project state。诊断时可对照
    `core/templates/profile-dynamic.template.toml`，但不要长期手写 profile，除非
    operator 明确选择 manual override。profile 存在后，再重跑原始
    `dispatch_task.py --profile ~/.agents/profiles/<project>-profile-dynamic.toml ...`。
11. **想完全重装**：谨慎使用 `./scripts/clean-slate.sh --yes`，它会清理安装状态。

## Resume

如果安装被打断：

```bash
cd ~/ClawSeat
bash scripts/install.sh --project <name> --template <template> --provider <mode>
```

如果 project 已经 `phase=ready`，但你需要重建：

```bash
bash scripts/install.sh --reinstall <name> --template <template> --provider <mode>
```

恢复 workers window：

```bash
python3 core/scripts/agent_admin.py window open-grid --project <name> --recover
```

恢复某个 seat：

```bash
python3 core/scripts/agent_admin.py session start-engineer <seat> --project <name>
```

不要直接拼 `tmux attach` 或手写 `osascript`；使用 canonical `agent_admin` 路径。
