# ClawSeat

## OpenClaw × gstack × tmux = 一支住在你 Mac 里的 AI 研发团队

不上云。不订阅。在你的 Mac 上。

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![macOS 14+](https://img.shields.io/badge/macOS-14%2B-black)](docs/INSTALL.md)
[![OpenClaw](https://img.shields.io/badge/built%20on-OpenClaw-purple)](https://github.com/openclaw/openclaw)
[![gstack](https://img.shields.io/badge/powered%20by-gstack-orange)](https://github.com/garrytan/gstack)
[![PRs welcome](https://img.shields.io/badge/PRs-%E6%AC%A2%E8%BF%8E-brightgreen)](CONTRIBUTING.md)

---

```
clawseat-<project>-workers
┌──────────────────────────┬──────────────────────┐
│ planner main             │ builder              │
│ 拆解 / 派工 / 合并         │ 写代码 / 跑测试       │
│                          ├──────────────────────┤
│                          │ reviewer             │
│                          │ 审 diff / 出 verdict  │
│                          ├──────────────────────┤
│                          │ patrol               │
│                          │ 巡检 / 漂移 / 证据    │
└──────────────────────────┴──────────────────────┘

clawseat-memories
┌─────────────────────────────────────────────────┐
│ <project>-memory tabs, one project per tab      │
└─────────────────────────────────────────────────┘
```

---

## 一句话装好

给你的 Claude / Codex / Gemini 讲这句话：

> Install ClawSeat on my Mac. Clone `https://github.com/KaneOrca/ClawSeat`
> to `~/ClawSeat`, then read `~/ClawSeat/docs/INSTALL.md` and follow it.
> Ask me for every choice.

九十秒后，你有一个完整的项目团队。memory 在 memories 窗口，workers 在
项目窗口，各自住在沙箱 HOME 里，通过 tmux 互相说话。

> **你看。它们干活。**

---

## 两个开源底座，织进一台 Mac

不是 agent 框架。不是 SaaS。是约 350 个 bash + Python 文件的一层薄壳，
把已经证明自己的开源能力接成可见、可控、可审计的本地研发团队。

### 左：OpenClaw — agent **是谁**

[OpenClaw](https://github.com/openclaw/openclaw)（MIT）是本地跑的多通道
AI 助手 Gateway。Feishu / WhatsApp / Telegram / Slack / iMessage 等 20+
通道，同一个 agent 在所有通道里都是你。每个 agent 是独立进程、独立
sandbox、独立 memory。心跳机制让它主动行动，不只被动回复。
[ClawHub](https://clawhub.ai) 插件市场 101+ bundled extensions。

### 右：gstack — agent **会做什么**

[gstack](https://github.com/garrytan/gstack)（MIT）是给 Claude Code 用的
工程方法论 skill 包。30+ 一键流水的咒语。`/ship` 是改→测→评→并→部→灰；
`/qa` 是定位→修→验；`/investigate` 是根因→法则→证据；`/cso` 是安全审计。
每一个本身就是一套完整流程。

### 中间：ClawSeat — 把身份、技能、协议叠在一起

每个 seat 不只是一个 prompt。它有**三层**。

身份：OpenClaw 给。
技能：gstack 给。
协议：ClawSeat 自己给，包括 memory/planner/builder/reviewer/patrol 的边界、
dispatch / completion / ACK、team ownership、liveness、review 和验收规则。

| seat | 身份 | 技能 | 协议 |
|---|---|---|---|
| memory | OpenClaw memory agent | ClawSeat memory skills | intake / KB / team ownership / acceptance |
| planner | OpenClaw planner agent | planning and review helpers | workflow / owner_seat / fan-out / fan-in |
| builder | OpenClaw builder agent | implementation helpers | code / tests / DELIVERY / handoff receipt |
| reviewer | OpenClaw reviewer agent | `/review` style checks | verdict / contract drift / evidence |
| patrol | OpenClaw patrol agent | scheduled evidence scans | regression / drift / health patrol |
| designer | OpenClaw designer agent | `/design-review` `/design-shotgun` | UX / visual / content quality |

---

## 三档默认装

| 模板 | seat | 适合 | 卖点 |
|---|---|---|---|
| **`clawseat-solo`** | v3 `MULTI_TEAM_MINIMAL` alias: memory + planner+builder 子项目组 + quality-docs | 想用最小项目组启动并后续横向扩多个 solo 单元的人 | 单 memory 管多个最小开发组 |
| `clawseat-creative` | memory + writer + builder-image + builder-av + patrol | 创作链（图片 / 视频 / 音频 / 分镜） | 绑 cartooner skill；cartooner-harness 协议层 |
| `clawseat-engineering` | memory + planner + builder + reviewer + patrol | 工程链（brief→plan→code→review→merge） | 绑 gstack skill；有 reviewer 守 diff |

> `solo` 已降级为兼容别名；新语义是 v3 multi-team 的最小项目组。每个开发子组都是 planner+builder，项目级 memory 统一验收和扩容。

---

## 两条协议 — 工程 × 创意

```
gstack-harness     task → dispatch → handoff → ack     commit-centric
cartooner-harness  lane → deposit → pick → iterate     asset-centric
```

工程链是 **deterministic** 的——spec 拆任务、builder 实现、reviewer 出
verdict，每一步都有唯一正确答案。

创意是 **indeterministic** 的——spec 永远不完整，每个 lane 抛出 N 张
「好但都不对」的候选，user 做最终美学判断。LLM 没有制片人之眼。

`cartooner-harness` 接受这件事，把边界写进协议：

- **no-image-policy** — 只有 user 看 asset；LLM seat 走 isolated subagent
  间接读视觉，主线程永远 image-free
- **Vision Steward** — memory 是流程引擎，不是审美裁判；所有美学决策
  escalate 给 user
- **Producer-centric** — user 是制片人，可越过 memory 直派任何 seat；任何
  seat 收到 user-direct 必须 fail-closed 回报

| 创意 seat | tool / auth | 职责 |
|---|---|---|
| memory | claude / minimax | Vision Steward — 状态 + 跨 lane 协调 |
| writer | claude / oauth | Story Specialist — narrative_outline.md（纯文学） |
| builder-image | codex / oauth | Image Specialist — nano-banana / gpt-image-2 / storyboard |
| builder-av | gemini / oauth | AV Cinematographer — Seedance / shot list / YouTube 参考学习 |
| patrol | claude / minimax | Asset Guardian — 文件完整性 + SLA + 越权审计 |

11 个 backend 协议脚本（spawn_lane / deposit_asset / pick_winner /
iterate_prompt / share_style_bible / patrol_pipeline_sla / spawn_subagent
…），88 subprocess 单测，零 LLM 美学判断。

[`core/skills/cartooner-harness/SKILL.md`](core/skills/cartooner-harness/SKILL.md)

> **诚实地说出那条边界，让自动化只去自动化能赢的事。**

---

## 三件事让它不一样

### 一. 你跟它对话，它就装好了

别家 agent 编排要 wizard、YAML DSL、集群 control plane。

ClawSeat 的 install 是**一份 5 步对话契约**：
language → template → project name → summary → run。
Step 0 静默 `--detect-only` 扫 OAuth / PTY / git branch / 现有项目，
然后只问你 5 个问题。每问支持 `/en` `/zh` `详`（150 字解释）`回车=默认`。

不是黑魔法，是协议。读
[`docs/INSTALL.md`](docs/INSTALL.md) 自己看。

> **这才叫 AI 原生。**

### 二. 你看见它在干 — 三个视角

不是 dashboard。不是 log 流。是三个互补的视角：

**TUI**：活的 tmux 网格，memory 在 memories 窗口，workers 在项目窗口，
每一格是一个真正在思考的 agent。

**SQLite ledger**：`~/.agents/state.db` 是单文件 SSOT，涵盖
projects / seats / tasks / events 四张表。

```bash
state-admin show-seats --project install
state-admin show-tasks --status open
state-admin pick --project install --role builder   # least-busy seat
state-admin recent-events --limit 20
```

**Typed-link graph**：每写一条 memory，自动 regex 抽 7 类边
（`references-task` `references-commit` `references-component`
`references-file` `references-url` `references-key` `references-project`）
并维护双向索引。

```bash
query_memory.py --backlinks "entity:taskid:ARENA-228"
query_memory.py --graph projects/arena/decision/foo --depth 2
```

零 LLM 调用。零 vector embedding。零 postgres。Inspired by
[gbrain](https://github.com/garrytan/gbrain) 的"graph is carry, vector is
icing"基线（P@5 49.1 vs graph-disabled 17.7）——我们只采纳 graph 这半。

> **你看见、你 grep、你 backlinks 它。**

### 三. 协议长成可解析的形状

工业化不是更多的 dashboard——是让 seat 之间的对话**机器可解析**。

**8 个 intent enum** 让消息变成结构化协议：

```
[<source>] <intent>: task <id> step <N> done; <next-action-hint>
```

`brief-handoff` / `dispatch` / `delivery` / `verdict-request` / `verdict` /
`consumed` / `patrol-finding` / `notice` —— 仅此 8 种，
全部走 `send-and-verify.sh` 唯一 transport，**永不**裸 `tmux send-keys`。

**派工首选规则（强制）**：planner 必须选 narrowest capable seat。
`requires_implementation` → builder。`requires_browser_qa` → reviewer。
`requires_visual_judgment` → designer。**不能** keep local 来省时间。

**Handoff 两步不可二选一**：
1. `complete_handoff.py` 写 durable `.consumed` receipt
2. `send-and-verify.sh` 唤醒 reply_to

少一步就 escalate 给 memory + reply_to，记 `artifacts/`。

**dispatch 工业化**：serial lock 防止并发漂移 base、`core_ux=true` 强制
带 1-3 条用户层 evidence、auto-heal 修复损坏的 STATUS.md、session
auto-resume 让重启完整恢复 6-pane state、codex capacity 限流自动 retry。

> **它不只跑得动，还跑得稳。**

---

## 为什么不是 X

| 你已经有 | 它给你 | ClawSeat 多给什么 |
|---|---|---|
| **Cursor / Windsurf** | IDE 内嵌 AI pair | 多个专业化 agent 并行，每人管自己的事 |
| **Devin / Replit Agents** | 云端单 agent 长任务 | 本地、可看见、可打断、每行代码都在你 Mac 上 |
| **LangChain / AutoGen** | Python 框架写 agent 流程 | 零 DSL；流程是 SKILL.md 自然语言 + 8 intent enum |
| **OpenClaw 单用** | 多通道 AI 助手 | 把它扩成研发团队，配 iTerm 双窗口 + state.db ledger + memory graph |
| **gstack 单用** | 30+ Claude Code skill | 按 seat 角色分发，planner 派一句 intent 就自动激活正确咒语 |
| **gbrain / Mem0 / Letta** | LLM-driven 记忆系统 | typed-link graph 零 LLM、零 embedding、纯 regex，可 grep 可 git diff |

**ClawSeat 不取代任何一个**——是把你已经信的几个缝合成一个你能**看见**的
团队。

---

## 装它

```bash
git clone https://github.com/KaneOrca/ClawSeat ~/ClawSeat
cd ~/ClawSeat && ./scripts/install.sh --project demo
```

或者，跟你的 AI 说一句话。结果一样。

---

## 这是给谁的

给已经在付 Claude Pro、Codex Plus 或 Gemini Advanced 的人。
给用 Mac 的人。
给懂 tmux 的人。
给爱拆开研究整个工具链的人。

> **就是你。**

---

## FAQ

**Q: 这玩意只在 Mac 上能跑？**

独立使用时基本是 Mac-first：默认 iTerm 网格 + macOS Keychain 路由 +
LaunchAgent。配合 Cartooner / 内嵌终端使用时可以走 `--no-window`，只启动
tmux seats，不依赖 iTerm 可视化窗口。Linux 能跑核心功能但少了原生网格可视化。
Windows 没测试。欢迎 PR——绝不 trivial。

**Q: 不用 Docker 怎么隔离 agent？**

`$HOME` 沙箱 + PATH 操控 + 符号链接。每个 seat 的
`~/.agent-runtime/identities/<tool>/<auth>/<id>/home/` 是独立 HOME。
比 Docker 轻，但不隔离系统库——这是 feature，让 seat 共享你的 Homebrew
和 iTerm 配置。

**Q: API key 会被传给 ClawSeat 的作者吗？**

不会。零网络请求出去 ClawSeat。直接向你配的 provider 发请求。零 telemetry,
零 phone-home。grep 整个代码库搜 `http` 验证。

**Q: solo 模板现在是什么？**

它不再是独立 single-mode 三 seat。`--template clawseat-solo` 会 seed v3
`MULTI_TEAM_MINIMAL` proposals：一个 project-memory、一个最小
planner+builder 子项目组，以及 `quality-docs`。multi-team 可以继续增加
多个这样的最小开发组。

**Q: state.db 会损坏吗？**

会。损坏了 `state-admin seed` 从文件系统重新派生即可——
`~/.agents/projects/` 下的 TOML 和 `patrol/handoffs/` 下的 JSON
才是 authoritative source,state.db 只是查询索引。

**Q: typed-link graph 跟 RAG / vector DB 比？**

不是替代品，是 baseline。**先有 graph 才考虑 vector**——gbrain
benchmark 上 graph-only 已经 P@5 49.1 / R@5 97.9,vector 加进去
+1% 不到。我们选了 graph 这半，因为它零依赖、可 grep、可 git diff。

**Q: 为什么不集成外部 practice skill？**

ClawSeat 的 seat 协议已经覆盖“什么时候拆、派给谁、怎么验、怎么回执”。
外部 practice 文档容易把 seat 引向 ClawSeat 之外的命令、目录和子 agent 假设；
因此当前版本不再把外部 practice 作为 skill/catalog/seat 上下文暴露。

**Q: 一个月烧多少 token？**

`solo` alias 走 v3 multi-team minimal；成本取决于后续项目组扩容和 seat provider。
重负载混搭（Claude Opus + Codex API + Gemini 2.5 Pro）一天 $10–30。
建议轻量 seat 用 minimax-M2 这种国产 API,Opus 留给 memory + planner。

**Q: 坏了怎么办？**

`./scripts/clean-slate.sh --yes` 一键清空重装。
`state.db` 有所有 dispatch 历史。所有文件是纯文本，vim 能改、
git 能 bisect。

---

## 深入

| 文档 | 你看到的 |
|---|---|
| [`docs/INSTALL.md`](docs/INSTALL.md) | 5 步 install decision tree（你的 AI 会自己读） |
| [`docs/INSTALL.zh-CN.md`](docs/INSTALL.zh-CN.md) | 5 步 install decision tree（中文） |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | L1/L2/L3 Pyramid + state.db ledger + 三档 templates |
| [`docs/CANONICAL-FLOW.md`](docs/CANONICAL-FLOW.md) | dispatch / completion / ACK 三件套 |
| [`docs/OPENCLAW.md`](docs/OPENCLAW.md) | ClawSeat 怎么用 OpenClaw |
| [`docs/GSTACK.md`](docs/GSTACK.md) | 哪个 seat 装哪些 gstack skill |
| [`docs/HACKING.md`](docs/HACKING.md) | 想改哪就改哪的导览 |
| [`core/references/seat-ownership.md`](core/references/seat-ownership.md) | 5 seat 单写权 canonical 矩阵 |
| [`core/references/seat-capabilities.md`](core/references/seat-capabilities.md) | 6 seat 能力边界 |
| [`core/references/memory-link-graph.md`](core/references/memory-link-graph.md) | typed-link graph v0.9 P1 spec |
| [`core/references/federated-kb-schema.md`](core/references/federated-kb-schema.md) | 联邦 KB 落盘契约 |
| [`core/references/handoff-receipt-protocol.md`](core/references/handoff-receipt-protocol.md) | 完成必须两步 |
| [`core/skills/gstack-harness/references/communication-protocol.md`](core/skills/gstack-harness/references/communication-protocol.md) | 8 intent + send-and-verify 唯一 transport |
| [`core/skills/planner/references/collaboration-rules.md`](core/skills/planner/references/collaboration-rules.md) | 派工首选规则 + swallow semantics |

## 仓库角色

- `~/ClawSeat` 是 install/release clone，永远在 `main`
- LaunchAgent 每天自动 fast-forward（首次安装 opt-in）
- 每次跑 `install.sh` 自检 + auto fast-forward
- 开发用单独 worktree:`git worktree add ~/path/to/dev <branch>`

## 许可

MIT。ClawSeat、OpenClaw、gstack 全是。
