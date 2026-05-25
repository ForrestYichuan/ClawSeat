# gstack × ClawSeat

> [gstack](https://github.com/garrytan/gstack)（MIT 开源）是给 Claude Code 用
> 的 30+ 种工程方法论 skill 包。ClawSeat 把这些 skill 按 seat 角色分发——
> builder 会 `/ship`，planner 会 `/review`，designer 会 `/design-review`，不用谁记忆咒语。

## gstack 是什么

gstack 不是一个 agent，也不是一个框架。是**一组给 Claude Code 看的行为
指令（skill）**——每个 skill 是一个 `.md` 文件 + 可选 `scripts/`，告诉
Claude 在某个场景下该怎么一步一步做事。

比如 `/ship` skill 不是一个脚本。它是一份说明：
1. 检测当前分支是否基于 main
2. 跑完整测试套件
3. 自动 review diff
4. bump VERSION
5. 更新 CHANGELOG
6. 提 commit
7. push + 开 PR

Claude Code 读到这份 skill 后，自己组织工具调用去完成每一步。**方法论沉淀
成文档，Claude 当作行动纲领。**

30+ gstack skill 覆盖了软件工程的大部分方法论：shipping、QA、code review、
debug、security audit、design review、performance benchmark、deploy monitor⋯⋯

## ClawSeat 怎么分发 gstack skill

### Seat → Skill 映射表

| Seat | gstack skills（会做什么） | ClawSeat 协议重点 |
|---|---|---|
| **memory** | `/cs` 系列, `learn`, `patrol`, `careful` | intake / KB / team ownership / acceptance |
| **planner** | `plan-eng-review`, `plan-ceo-review`, `plan-design-review`, `office-hours`, `autoplan`, `review` | workflow / `owner_seat` / fan-out / fan-in |
| **builder** | `ship`, `investigate`, `land-and-deploy`, `freeze`, `unfreeze`, `browse`, `careful` | implementation / tests / DELIVERY / handoff receipt |
| **reviewer** | `review`, `investigate`, `browse` | verdict / contract drift / evidence |
| **patrol** | `canary`, `benchmark`, `browse`, `careful` | scheduled regression / drift / health patrol |
| **designer** | `design-html`, `design-review`, `design-shotgun`, `design-consultation`, `browse` | UX / visual / content quality |
| **writer** | cartooner-harness | — |
| **builder-image** | cartooner-harness | — |
| **builder-av** | cartooner-harness | — |

映射代码在 [`core/scripts/seat_skill_mapping.py`](../core/scripts/seat_skill_mapping.py)。
想调整？改一行就行。

### 装载机制

每个 seat 启动时，[`seat_claude_template.py::ensure_seat_claude_template`](../core/scripts/seat_claude_template.py)
从 `${CLAWSEAT_ROOT}/core/skills/<skill>/` 把对应 skill 目录完整 copytree 到：

```
~/.agents/engineers/<seat>/.claude-template/skills/
```

Claude Code 读这个目录作为 skill 发现路径。seat 一启动，所有映射的 skill
就在它的 system prompt 视野里。

同时 ClawSeat 自己也贡献了几个 skill 住在 `core/skills/`：

- `clawseat-memory` — memory 专属行为（Phase-A / Phase-B、三 ID 辨析、seat 协调）
- `planner` — ClawSeat 的 dispatch 专属 planner 行为（区别于 gstack-plan-eng-review）
- `gstack-harness` — dispatch 协议实现（见下面）
- `memory-oracle` — memory seat 的 SSR 存储访问
- `clawseat` + `tmux-basics` — 共享给所有 seat 的基础知识

## 方法论边界

ClawSeat 不再把外部 practice 文档作为 seat skill、catalog 来源或模板默认技能。
seat 的“怎么想事”由 ClawSeat 自己的协议承担：seat ownership、workflow schema、
dispatch / completion / ACK、team ownership、liveness、review、acceptance 和
patrol 规则。这样可以避免 seat 幻觉到 ClawSeat 之外的命令、目录和子 agent
假设。

---

## gstack-harness — ClawSeat 特有的一层

ClawSeat 没直接用 gstack 的 `ship`、`review`、`qa` 触发，而是包了一层叫
`gstack-harness` 的 skill + 一组 Python 脚本
（[`core/skills/gstack-harness/`](../core/skills/gstack-harness/)），干三件事：

### 1. Intent 系统（operator 说意图，harness 翻译成咒语）

planner 派活时写：

```bash
python3 core/skills/gstack-harness/scripts/dispatch_task.py \
  --source koder --target builder-1 \
  --task-id task-001 \
  --objective "实现新的 API 路由" \
  --test-policy UPDATE \
  --intent ship
```

`--intent ship` 这一个参数，harness 内部会：

1. 在 `INTENT_MAP`（[`dispatch_task.py:95-200`](../core/skills/gstack-harness/scripts/dispatch_task.py)）
   里查到 `ship` 对应的 gstack trigger phrase
2. 把 trigger phrase 注入 objective
3. 把 gstack `/ship` SKILL.md 路径追加到 `--skill-refs`

builder 收到时，Claude Code 看见 trigger phrase 自动激活 `/ship` 方法论。
**planner 只用说意图，不用记 gstack 咒语**。

### 2. Dispatch 协议（三阶段状态机）

派一条活有三个阶段，每阶段写 durable 存档：

```
assigned    → dispatch_task.py 写 dispatch 回执 (handoff.json + state.db)
notified    → send-and-verify.sh 把 message 发到 target seat (tmux)
consumed    → complete_handoff.py 写完成回执
```

三阶段的任何一个卡住都能恢复——`verify_handoff.py --task-id X` 查状态就
知道从哪接回。**不是消息队列，是有回执的状态机。**

### 3. 子 agent 扇出规则

gstack-harness 的 [`references/sub-agent-fan-out.md`](../core/skills/gstack-harness/references/sub-agent-fan-out.md)
定义：一个任务如果有 2+ 个独立文件集、独立测试目标、独立调研方向——seat
**必须**用 Claude Code Agent / Codex subagent / Gemini subagent 并行起子
agent。避免该并行的活串行做。

预估墙钟节省：40-50% on 多独立子部分的任务。

## 完整 Dispatch 生命周期（例子）

**0. Setup**（一次性）

```bash
python3 core/skills/gstack-harness/scripts/bootstrap_harness.py \
  --profile demo --start
```

创建 6 seat 的 workspace、sandbox HOME、WORKSPACE_CONTRACT。koder 起来，
其他先 headless。

**1. 派活：koder → planner**

```bash
dispatch_task.py \
  --source koder --target planner \
  --task-id task-001 \
  --objective "设计 API 路由架构" \
  --test-policy UPDATE \
  --intent eng-review
```

harness 把 `/plan-eng-review` 方法论注入，planner 的 Claude 自动激活。

**2. 执行：planner 内部**

planner 读 intent 后，按 `/plan-eng-review` 的方法论拆需求。如果需求有
3 个不相干子部分（数据模型 / 路由 handler / 集成测试），planner 按
sub-agent 规则并行起 3 个 Agent 子实例，各自写 `DELIVERY-A.md`、
`DELIVERY-B.md`、`DELIVERY-C.md`，主 agent 汇总。

**3. 交接：planner → koder**

```bash
complete_handoff.py \
  --source planner --target koder \
  --task-id task-001 \
  --disposition AUTO_ADVANCE \
  --summary "架构已锁定，builder-1 可以开始实现。推荐 ship 工作流。"
```

写 `DELIVERY.md` + append "Consumed: ACK" 到 planner 自己的 `PLANNER_BRIEF.md`
+ 发 Feishu 异步镜像。

**4. 派活：koder → builder**

```bash
dispatch_task.py \
  --source koder --target builder-1 \
  --task-id task-001-impl \
  --objective "实现 API 路由架构设计，从 PLANNER_BRIEF.md 读设计文档" \
  --test-policy UPDATE \
  --intent ship
```

`/ship` 咒语注入，builder 的 Claude 自动走 ship 流程。

**5. 验证链路完整**

```bash
verify_handoff.py --task-id task-001 --task-id task-001-impl
```

查 state.db：三阶段全部完成？全链 closeout？

## 外部设计工具 handoff

ClawSeat 的 dispatch chain 不一定从 koder 内部派发开始——也可以接收**外部设计工具的产物**作为入口。这让 ClawSeat 成为「任何设计来源 → 实现」的通用 handoff 终点。

### Claude Design（Anthropic Labs，2026-04 发布）

[claude.ai/design](https://claude.ai/design)（Claude Pro / Max / Team / Enterprise 可用）把文字提示转成设计 / 原型 / one-pager / HTML，由 Claude Opus 4.7 驱动。完成后可打包成 **handoff bundle 传给 Claude Code 实现**——正好对应 ClawSeat 的 chain 入口。

**接入流程**：

1. 在 `claude.ai/design` 探索方向 → 选定 → 导出 handoff bundle（HTML / PNG / Canva / 描述）
2. 把 bundle 提交给 ClawSeat 的 koder：
   - 飞书群里 @koder 发设计描述 + bundle 链接
   - 或直接放进 `~/.agents/tasks/<project>/inbox/<task-id>/`
3. koder 写一条 dispatch：

```bash
dispatch_task.py \
  --source koder --target engineer-e \
  --task-id task-design-001 \
  --objective "评审 Claude Design 出的 hall 卡片重设计，bundle 见 attached" \
  --intent design-review \
  --skill-refs <bundle-path>
```

4. engineer-e（designer）按 `/design-review` 评审视觉决策 → 通过则 engineer-b 拆解给 engineer-a 实现 → engineer-c review → engineer-d QA

### gstack `/design-shotgun` + `/design-html`

本地开源等价方案：

- `/design-shotgun` 用 GPT-4o vision 出多版设计候选
- `/design-html` 落地成 **Pretext-native HTML**（30KB zero deps，文字真能 reflow / heights 真能 compute / charRect 字符级精度）

适合需要**字符级物理对齐**的视觉（如 arena-pretext-ui 那种文字本身是物理参与者的项目）——`/design-html` 产物的 `prepare/layout` 调用天然兼容 obstacle 系统。

### Figma / Sketch / 其它

任何能导出 PNG + HTML + 设计描述的工具都能走同一条 chain。关键是给 koder 一个清晰的 spec，koder 再 dispatch 给适当的 specialist——chain 的内部协议（assigned → notified → consumed 三阶段状态机）不变。

### 三种工具如何选

| 场景 | 推荐 |
|---|---|
| 探索新方向 / 客户协作 / 要 PPT 输出 | Claude Design |
| 本地 / 离线 / 字符级物理对齐（Pretext-native） | gstack `/design-shotgun` + `/design-html` |
| 已有 Figma / 团队设计师 | Figma export → 喂 koder |

> **ClawSeat 不绑设计工具——只要给 koder 一个能转化成 dispatch objective 的产物就行。**

## 查询 + 定制

**看每个 seat 现在绑了哪些 skill**：

```bash
python3 -c "
from core.scripts.seat_skill_mapping import SEAT_SKILL_MAP
for seat, skill in SEAT_SKILL_MAP.items():
    print(f'{seat:10s}  {skill}')
"
```

**加一个 seat / 改映射**：

改 `core/scripts/seat_skill_mapping.py`——它只有 50 行，很直观。

**加一个 intent**：

改 `core/skills/gstack-harness/scripts/dispatch_task.py` 的 `INTENT_MAP`，
加一行 `"my-intent": "trigger phrase here..."`，再把 SKILL.md 放进
`${CLAWSEAT_ROOT}/core/skills/`。

## 关于 gstack 本身

- 作者：[@garrytan](https://github.com/garrytan)
- 许可：MIT
- 安装：`curl https://gstack.sh/install | bash` 或从 GitHub clone 到 `~/.gstack/`
- 配置：`~/.gstack/config.yaml`（首次安装自动生成；控制 telemetry、Codex
  review 模式、自动升级等）
- 升级：`gstack upgrade`（ClawSeat 也提供 `/gstack-upgrade` skill）

## 深入

- gstack：[github.com/garrytan/gstack](https://github.com/garrytan/gstack)
- Dispatch 协议完整规范：[`core/skills/gstack-harness/references/chain-protocol.md`](../core/skills/gstack-harness/references/chain-protocol.md)
- Seat 权限模型：[`core/skills/gstack-harness/references/seat-model.md`](../core/skills/gstack-harness/references/seat-model.md)
- 子 agent 规则：[`core/skills/gstack-harness/references/sub-agent-fan-out.md`](../core/skills/gstack-harness/references/sub-agent-fan-out.md)
- ClawSeat 架构：[`ARCHITECTURE.md`](ARCHITECTURE.md)
- OpenClaw 集成：[`OPENCLAW.md`](OPENCLAW.md)
