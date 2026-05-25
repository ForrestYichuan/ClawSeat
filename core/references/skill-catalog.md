# Skill Catalog

Generated foundation catalog for planner routing and skill discovery. Run `python3 core/scripts/rebuild_skill_catalog.py --force --update-md` to refresh this snapshot and the lazy JSON cache at `~/.agents/cache/skill-catalog.json`.

## Source Notes

- `~/.agents/skills/` - ClawSeat project and machine workflow skills.
- `~/.claude/skills/` - gstack and local Claude skills.
- `~/.claude/plugins/marketplaces/` - Anthropic/Claude marketplace plugin docs.

Total unique entries in this catalog: 132.

| Skill | Source | Purpose | When to use | Command form |
| --- | --- | --- | --- | --- |
| agent-reach | ~/.agents/skills/ | Give your AI agent eyes to see the entire internet. Search and read across Twitter/X, Reddit, YouTube, GitHub, Bilibi... | ClawSeat seat workflow needs this role capability | Skill: agent-reach |
| art-director-expert | ~/.agents/skills/ | 角色资产持久化 — 当你用 claw-image 生成了角色设计图（design.png）或角色三视图（turn-around.png）后， 使用此 skill 把资产安全写入项目角色工作区。自动处理目录路由、版本管理、manifes... | ClawSeat seat workflow needs this role capability | Skill: art-director-expert |
| cartooner-artisan | ~/.agents/skills/ | Persist creative assets (locations, props, visual styles, images, audio, video) to shared project workspace. Use when... | ClawSeat seat workflow needs this role capability | Skill: cartooner-artisan |
| cartooner-browser | ~/.agents/skills/ | Control Cartooner's embedded Inspiration browser via OpenClaw browser tool. Use profile="inspiration" to interact wit... | ClawSeat seat workflow needs this role capability | Skill: cartooner-browser |
| cartooner-resource-ops | ~/.agents/skills/ | Operate Cartooner projects and assets at the resource layer. Use when tasks involve deterministic filesystem-backed o... | ClawSeat seat workflow needs this role capability | Skill: cartooner-resource-ops |
| cartooner-video | ~/.agents/skills/ | 视频工作流 Skill。负责工作流模板的创建、管理、执行与交付，包含素材收集、 分镜设计、workflow JSON 生成、lip-sync、Remotion 和平台编码。优先消费 clawseat-intake 产出的需... | ClawSeat seat workflow needs this role capability | Skill: cartooner-video |
| clawseat-decision-escalation | ~/.agents/skills/ | Routes decisions blocked by automation to operator; enforces 3-option gate. | ClawSeat seat workflow needs this role capability | Skill: clawseat-decision-escalation |
| clawseat-koder | ~/.agents/skills/ | OpenClaw Koder bridge: translates decision payloads, routes Feishu replies, enforces privacy. | ClawSeat seat workflow needs this role capability | Skill: clawseat-koder |
| clawseat-memory | ~/.agents/skills/ | L3 project-memory hub; orchestrates install-memory workflow per RFC-002. | ClawSeat seat workflow needs this role capability | Skill: clawseat-memory |
| clawseat-memory-reporting | ~/.agents/skills/ | Logs dispatch/completion events; maintains project STATUS.md registry. | ClawSeat seat workflow needs this role capability | Skill: clawseat-memory-reporting |
| clawseat-privacy | ~/.agents/skills/ | Pre-commit check gate; blocks exposure of secrets, API keys, tokens. | ClawSeat seat workflow needs this role capability | Skill: clawseat-privacy |
| create-skills | ~/.agents/skills/ | Use this skill whenever the user wants to create a new Claude skill, write a SKILL.md file, turn a workflow into a re... | ClawSeat seat workflow needs this role capability | Skill: create-skills |
| en-to-zh-translator | ~/.agents/skills/ | en-to-zh-translator Skill | ClawSeat seat workflow needs this role capability | Skill: en-to-zh-translator |
| find-skills | ~/.agents/skills/ | 搜索和发现可用的 agent skill。支持 ClawHub 在线搜索和本地已安装 skill 检索。 当需要扩展能力、查找特定功能的 skill、或判断是否有现成方案时使用。 tags: [skill-discovery, sea... | ClawSeat seat workflow needs this role capability | Skill: find-skills |
| nano-banana | ~/.agents/skills/ | Gemini Image API (Nano Banana) prompt engineering guide — official best practices, prompt templates, style presets, c... | ClawSeat seat workflow needs this role capability | Skill: nano-banana |
| pdf | ~/.agents/skills/ | Use this skill whenever the user wants to do anything with PDF files. This includes reading or extracting text/tables... | ClawSeat seat workflow needs this role capability | Skill: pdf |
| pptx | ~/.agents/skills/ | Use this skill any time a .pptx file is involved in any way — as input, output, or both. This includes: creating slid... | ClawSeat seat workflow needs this role capability | Skill: pptx |
| pretext-flow | ~/.agents/skills/ | Use pretext-flow for text layout where shapes, images, or interactive elements are embedded directly in a flowing tex... | ClawSeat seat workflow needs this role capability | Skill: pretext-flow |
| remotion-delegation | ~/.agents/skills/ | Remotion 动画视频委派工作流。Agent 做场景规划，spawn Gemini ACP 写 Remotion 组合件代码， Agent 自己验证+渲染+交付。适用于：品牌动画、数据可视化视频、产品演示、代码演示、片头片尾。 触... | ClawSeat seat workflow needs this role capability | Skill: remotion-delegation |
| remotion-video-production | ~/.agents/skills/ | Produce programmable videos with Remotion (React TSX frame-by-frame rendering). Use when the task requires: data visu... | ClawSeat seat workflow needs this role capability | Skill: remotion-video-production |
| script-analyst | ~/.agents/skills/ | 剧本/文案全维度分析。读取剧本文本 + 已有资产清单（manifest），输出结构化 JSON： 叙事分段（每段对应一组 9 帧九宫格）、角色识别与状态判定、场景识别与状态判定、 关键道具提取、整体视觉风格定义。用于分镜生产 pipe... | ClawSeat seat workflow needs this role capability | Skill: script-analyst |
| script-writing-expert | ~/.agents/skills/ | 编剧专家技能。生成 Fountain 剧本内容后，调用本地 writer helper，把剧本安全写入项目的 script 工作区（`~/.openclaw/projects/{projectId}/narrative/script/`）。 | ClawSeat seat workflow needs this role capability | Skill: script-writing-expert |
| storyboard-expert | ~/.agents/skills/ | 分镜专家技能。生成分镜帧后，调用本地 writer helper，把 `01.png` - `09.png` 与 `manifest.json` 安全写入项目的分镜工作区（`~/.openclaw/projects/{projectI... | ClawSeat seat workflow needs this role capability | Skill: storyboard-expert |
| storyboard-forge | ~/.agents/skills/ | 分镜编排 pipeline — 当用户要你生成 9 宫格故事版、电影分镜、漫画多格图、角色表情包、 角色三视图/五视图、产品多角度展示图、社交媒体九宫格时，使用此 skill。 负责 prompt 设计 + 模型路由 + 参考图编排，... | ClawSeat seat workflow needs this role capability | Skill: storyboard-forge |
| viral-copywriter | ~/.agents/skills/ | 爆款中文推文写手。接收热点素材（facts JSON 或自然语言 brief）， 输出犀利、高密度、去 AI 味的中文长推文 + 事实核查表。 Use when: 写爆款推文, 热点文案, X/Twitter 中文内容, 写一篇犀利的推文, | ClawSeat seat workflow needs this role capability | Skill: viral-copywriter |
| xlsx | ~/.agents/skills/ | Use this skill any time a spreadsheet file is the primary input or output. This means any task where the user wants t... | ClawSeat seat workflow needs this role capability | Skill: xlsx |
| agent-reach | ~/.claude/skills/ | Give your AI agent eyes to see the entire internet. Search and read across Twitter/X, Reddit, YouTube, GitHub, Bilibi... | Use when the matching workflow is requested | Skill: agent-reach |
| art-director-expert | ~/.claude/skills/ | 角色资产持久化 — 当你用 claw-image 生成了角色设计图（design.png）或角色三视图（turn-around.png）后， 使用此 skill 把资产安全写入项目角色工作区。自动处理目录路由、版本管理、manifes... | Use when the matching workflow is requested | Skill: art-director-expert |
| autoplan | ~/.claude/skills/ | Auto-review pipeline — reads the full CEO, design, and eng review skills from disk and runs them sequentially with au... | Use when the matching workflow is requested | Skill: autoplan |
| benchmark | ~/.claude/skills/ | Performance regression detection using the browse daemon. Establishes baselines for page load times, Core Web Vitals,... | Use when the matching workflow is requested | Skill: benchmark |
| browse | ~/.claude/skills/ | Fast headless browser for QA testing and site dogfooding. Navigate any URL, interact with elements, verify page state... | Use when the matching workflow is requested | Skill: browse |
| canary | ~/.claude/skills/ | Post-deploy canary monitoring. Watches the live app for console errors, performance regressions, and page failures us... | Use when the matching workflow is requested | Skill: canary |
| careful | ~/.claude/skills/ | Safety guardrails for destructive commands. Warns before rm -rf, DROP TABLE, force-push, git reset --hard, kubectl de... | Use when the matching workflow is requested | Skill: careful |
| cartooner-artisan | ~/.claude/skills/ | Persist creative assets (locations, props, visual styles, images, audio, video) to shared project workspace. Use when... | Use when the matching workflow is requested | Skill: cartooner-artisan |
| cartooner-browser | ~/.claude/skills/ | Control Cartooner's embedded Inspiration browser via OpenClaw browser tool. Use profile="inspiration" to interact wit... | Use when the matching workflow is requested | Skill: cartooner-browser |
| cartooner-resource-ops | ~/.claude/skills/ | Operate Cartooner projects and assets at the resource layer. Use when tasks involve deterministic filesystem-backed o... | Use when the matching workflow is requested | Skill: cartooner-resource-ops |
| cartooner-video | ~/.claude/skills/ | 视频工作流 Skill。负责工作流模板的创建、管理、执行与交付，包含素材收集、 分镜设计、workflow JSON 生成、lip-sync、Remotion 和平台编码。优先消费 clawseat-intake 产出的需... | Use when the matching workflow is requested | Skill: cartooner-video |
| codex | ~/.claude/skills/ | OpenAI Codex CLI wrapper — three modes. Code review: independent diff review via codex review with pass/fail gate. Ch... | Use when the matching workflow is requested | Skill: codex |
| connect-chrome | ~/.claude/skills/ | Launch real Chrome controlled by gstack with the Side Panel extension auto-loaded. One command: connects Claude to a... | Use when the matching workflow is requested | Skill: connect-chrome |
| cso | ~/.claude/skills/ | Chief Security Officer mode. Infrastructure-first security audit: secrets archaeology, dependency supply chain, CI/CD... | Use when the matching workflow is requested | Skill: cso |
| design-consultation | ~/.claude/skills/ | Design consultation: understands your product, researches the landscape, proposes a complete design system (aesthetic... | Use when the matching workflow is requested | Skill: design-consultation |
| design-html | ~/.claude/skills/ | Design finalization: takes an approved AI mockup from /design-shotgun and generates production-quality Pretext-native... | Use when the matching workflow is requested | Skill: design-html |
| design-review | ~/.claude/skills/ | Designer's eye QA: finds visual inconsistency, spacing issues, hierarchy problems, AI slop patterns, and slow interac... | Use when the matching workflow is requested | Skill: design-review |
| design-shotgun | ~/.claude/skills/ | Design shotgun: generate multiple AI design variants, open a comparison board, collect structured feedback, and itera... | Use when the matching workflow is requested | Skill: design-shotgun |
| document-release | ~/.claude/skills/ | Post-ship documentation update. Reads all project docs, cross-references the diff, updates README/ARCHITECTURE/CONTRI... | Use when the matching workflow is requested | Skill: document-release |
| find-skills | ~/.claude/skills/ | 搜索和发现可用的 agent skill。支持 ClawHub 在线搜索和本地已安装 skill 检索。 当需要扩展能力、查找特定功能的 skill、或判断是否有现成方案时使用。 tags: [skill-discovery, sea... | Use when the matching workflow is requested | Skill: find-skills |
| freeze | ~/.claude/skills/ | Restrict file edits to a specific directory for the session. Blocks Edit and Write outside the allowed path. Use when... | Use when the matching workflow is requested | Skill: freeze |
| gstack | ~/.claude/skills/ | Fast headless browser for QA testing and site dogfooding. Navigate pages, interact with elements, verify state, diff... | Use when the matching workflow is requested | Skill: gstack |
| gstack-upgrade | ~/.claude/skills/ | Upgrade gstack to the latest version. Detects global vs vendored install, runs the upgrade, and shows what's new. Use... | Use when the matching workflow is requested | Skill: gstack-upgrade |
| guard | ~/.claude/skills/ | Full safety mode: destructive command warnings + directory-scoped edits. Combines /careful (warns before rm -rf, DROP... | Use when the matching workflow is requested | Skill: guard |
| investigate | ~/.claude/skills/ | Systematic debugging with root cause investigation. Four phases: investigate, analyze, hypothesize, implement. Iron L... | Use when the matching workflow is requested | Skill: investigate |
| land-and-deploy | ~/.claude/skills/ | Land and deploy workflow. Merges the PR, waits for CI and deploy, verifies production health via canary checks. Takes... | Use when the matching workflow is requested | Skill: land-and-deploy |
| learn | ~/.claude/skills/ | Manage project learnings. Review, search, prune, and export what gstack has learned across sessions. Use when asked t... | Use when the matching workflow is requested | Skill: learn |
| nano-banana | ~/.claude/skills/ | Gemini Image API (Nano Banana) prompt engineering guide — official best practices, prompt templates, style presets, c... | Use when the matching workflow is requested | Skill: nano-banana |
| office-hours | ~/.claude/skills/ | YC Office Hours — two modes. Startup mode: six forcing questions that expose demand reality, status quo, desperate sp... | Use when the matching workflow is requested | Skill: office-hours |
| pdf | ~/.claude/skills/ | Use this skill whenever the user wants to do anything with PDF files. This includes reading or extracting text/tables... | Use when the matching workflow is requested | Skill: pdf |
| plan-ceo-review | ~/.claude/skills/ | CEO/founder-mode plan review. Rethink the problem, find the 10-star product, challenge premises, expand scope when it... | Use when the matching workflow is requested | Skill: plan-ceo-review |
| plan-design-review | ~/.claude/skills/ | Designer's eye plan review — interactive, like CEO and Eng review. Rates each design dimension 0-10, explains what wo... | Use when the matching workflow is requested | Skill: plan-design-review |
| plan-eng-review | ~/.claude/skills/ | Eng manager-mode plan review. Lock in the execution plan — architecture, data flow, diagrams, edge cases, test covera... | Use when the matching workflow is requested | Skill: plan-eng-review |
| pptx | ~/.claude/skills/ | Use this skill any time a .pptx file is involved in any way — as input, output, or both. This includes: creating slid... | Use when the matching workflow is requested | Skill: pptx |
| qa | ~/.claude/skills/ | Systematically QA test a web application and fix bugs found. Runs QA testing, then iteratively fixes bugs in source c... | Use when the matching workflow is requested | Skill: qa |
| qa-only | ~/.claude/skills/ | Report-only QA testing. Systematically tests a web application and produces a structured report with health score, sc... | Use when the matching workflow is requested | Skill: qa-only |
| remotion-delegation | ~/.claude/skills/ | Remotion 动画视频委派工作流。Agent 做场景规划，spawn Gemini ACP 写 Remotion 组合件代码， Agent 自己验证+渲染+交付。适用于：品牌动画、数据可视化视频、产品演示、代码演示、片头片尾。 触... | Use when the matching workflow is requested | Skill: remotion-delegation |
| remotion-video-production | ~/.claude/skills/ | Produce programmable videos with Remotion (React TSX frame-by-frame rendering). Use when the task requires: data visu... | Use when the matching workflow is requested | Skill: remotion-video-production |
| retro | ~/.claude/skills/ | Weekly engineering retrospective. Analyzes commit history, work patterns, and code quality metrics with persistent hi... | Use when the matching workflow is requested | Skill: retro |
| review | ~/.claude/skills/ | Pre-landing PR review. Analyzes diff against the base branch for SQL safety, LLM trust boundary violations, condition... | Use when the matching workflow is requested | Skill: review |
| script-analyst | ~/.claude/skills/ | 剧本/文案全维度分析。读取剧本文本 + 已有资产清单（manifest），输出结构化 JSON： 叙事分段（每段对应一组 9 帧九宫格）、角色识别与状态判定、场景识别与状态判定、 关键道具提取、整体视觉风格定义。用于分镜生产 pipe... | Use when the matching workflow is requested | Skill: script-analyst |
| script-writing-expert | ~/.claude/skills/ | 编剧专家技能。生成 Fountain 剧本内容后，调用本地 writer helper，把剧本安全写入项目的 script 工作区（`~/.openclaw/projects/{projectId}/narrative/script/`）。 | Use when the matching workflow is requested | Skill: script-writing-expert |
| setup-browser-cookies | ~/.claude/skills/ | Import cookies from your real Chromium browser into the headless browse session. Opens an interactive picker UI where... | Use when the matching workflow is requested | Skill: setup-browser-cookies |
| setup-deploy | ~/.claude/skills/ | Configure deployment settings for /land-and-deploy. Detects your deploy platform (Fly.io, Render, Vercel, Netlify, He... | Use when the matching workflow is requested | Skill: setup-deploy |
| ship | ~/.claude/skills/ | Ship workflow: detect + merge base branch, run tests, review diff, bump VERSION, update CHANGELOG, commit, push, crea... | Use when the matching workflow is requested | Skill: ship |
| storyboard-expert | ~/.claude/skills/ | 分镜专家技能。生成分镜帧后，调用本地 writer helper，把 `01.png` - `09.png` 与 `manifest.json` 安全写入项目的分镜工作区（`~/.openclaw/projects/{projectI... | Use when the matching workflow is requested | Skill: storyboard-expert |
| storyboard-forge | ~/.claude/skills/ | 分镜编排 pipeline — 当用户要你生成 9 宫格故事版、电影分镜、漫画多格图、角色表情包、 角色三视图/五视图、产品多角度展示图、社交媒体九宫格时，使用此 skill。 负责 prompt 设计 + 模型路由 + 参考图编排，... | Use when the matching workflow is requested | Skill: storyboard-forge |
| tui-peer-bridge | ~/.claude/skills/ | >- | Use when the matching workflow is requested | Skill: tui-peer-bridge |
| unfreeze | ~/.claude/skills/ | Clear the freeze boundary set by /freeze, allowing edits to all directories again. Use when you want to widen edit sc... | Use when the matching workflow is requested | Skill: unfreeze |
| viral-copywriter | ~/.claude/skills/ | 爆款中文推文写手。接收热点素材（facts JSON 或自然语言 brief）， 输出犀利、高密度、去 AI 味的中文长推文 + 事实核查表。 Use when: 写爆款推文, 热点文案, X/Twitter 中文内容, 写一篇犀利的推文, | Use when the matching workflow is requested | Skill: viral-copywriter |
| xlsx | ~/.claude/skills/ | Use this skill any time a spreadsheet file is the primary input or output. This means any task where the user wants t... | Use when the matching workflow is requested | Skill: xlsx |
| access | ~/.claude/plugins/marketplaces/ | Manage Discord channel access — approve pairings, edit allowlists, set DM/group policy. Use when the user asks to pai... | Use when the matching workflow is requested | Skill: access |
| agent-development | ~/.claude/plugins/marketplaces/ | This skill should be used when the user asks to "create an agent", "add an agent", "write a subagent", "agent frontma... | Use when the matching workflow is requested | Skill: agent-development |
| agent-sdk-dev | ~/.claude/plugins/marketplaces/ | Agent SDK Development Plugin | Use when the matching workflow is requested | Skill: agent-sdk-dev |
| block-dangerous-rm | ~/.claude/plugins/marketplaces/ | Hookify Plugin | Use when the matching workflow is requested | Skill: block-dangerous-rm |
| build-mcp-app | ~/.claude/plugins/marketplaces/ | This skill should be used when the user wants to build an "MCP app", add "interactive UI" or "widgets" to an MCP serv... | Use when the matching workflow is requested | Skill: build-mcp-app |
| build-mcp-server | ~/.claude/plugins/marketplaces/ | This skill should be used when the user asks to "build an MCP server", "create an MCP", "make an MCP integration", "w... | Use when the matching workflow is requested | Skill: build-mcp-server |
| build-mcpb | ~/.claude/plugins/marketplaces/ | This skill should be used when the user wants to "package an MCP server", "bundle an MCP", "make an MCPB", "ship a lo... | Use when the matching workflow is requested | Skill: build-mcpb |
| clangd-lsp | ~/.claude/plugins/marketplaces/ | clangd-lsp | Use when the matching workflow is requested | Skill: clangd-lsp |
| claude-automation-recommender | ~/.claude/plugins/marketplaces/ | Analyze a codebase and recommend Claude Code automations (hooks, subagents, skills, plugins, MCP servers). Use when u... | Use when the matching workflow is requested | Skill: claude-automation-recommender |
| claude-code-setup | ~/.claude/plugins/marketplaces/ | Claude Code Setup Plugin | Use when the matching workflow is requested | Skill: claude-code-setup |
| claude-md-improver | ~/.claude/plugins/marketplaces/ | Audit and improve CLAUDE.md files in repositories. Use when user asks to check, audit, update, improve, or fix CLAUDE... | Use when the matching workflow is requested | Skill: claude-md-improver |
| claude-md-management | ~/.claude/plugins/marketplaces/ | CLAUDE.md Management Plugin | Use when the matching workflow is requested | Skill: claude-md-management |
| claude-plugins-official | ~/.claude/plugins/marketplaces/ | Claude Code Plugins Directory | Use when the matching workflow is requested | Skill: claude-plugins-official |
| code-review | ~/.claude/plugins/marketplaces/ | Code Review Plugin | Use when the matching workflow is requested | Skill: code-review |
| command-development | ~/.claude/plugins/marketplaces/ | This skill should be used when the user asks to "create a slash command", "add a command", "write a custom command",... | Use when the matching workflow is requested | Skill: command-development |
| commit-commands | ~/.claude/plugins/marketplaces/ | Commit Commands Plugin | Use when the matching workflow is requested | Skill: commit-commands |
| configure | ~/.claude/plugins/marketplaces/ | Set up the Discord channel — save the bot token and review access policy. Use when the user pastes a Discord bot toke... | Use when the matching workflow is requested | Skill: configure |
| csharp-lsp | ~/.claude/plugins/marketplaces/ | csharp-lsp | Use when the matching workflow is requested | Skill: csharp-lsp |
| discord | ~/.claude/plugins/marketplaces/ | Discord | Use when the matching workflow is requested | Skill: discord |
| example-command | ~/.claude/plugins/marketplaces/ | An example user-invoked skill that demonstrates frontmatter options and the skills/<name>/SKILL.md layout | Use when the matching workflow is requested | Skill: example-command |
| example-skill | ~/.claude/plugins/marketplaces/ | This skill should be used when the user asks to "demonstrate skills", "show skill format", "create a skill template",... | Use when the matching workflow is requested | Skill: example-skill |
| explanatory-output-style | ~/.claude/plugins/marketplaces/ | Explanatory Output Style Plugin | Use when the matching workflow is requested | Skill: explanatory-output-style |
| fakechat | ~/.claude/plugins/marketplaces/ | fakechat | Use when the matching workflow is requested | Skill: fakechat |
| feature-dev | ~/.claude/plugins/marketplaces/ | Feature Development Plugin | Use when the matching workflow is requested | Skill: feature-dev |
| frontend-design | ~/.claude/plugins/marketplaces/ | Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks... | Use when the matching workflow is requested | Skill: frontend-design |
| gopls-lsp | ~/.claude/plugins/marketplaces/ | gopls-lsp | Use when the matching workflow is requested | Skill: gopls-lsp |
| greptile | ~/.claude/plugins/marketplaces/ | Greptile | Use when the matching workflow is requested | Skill: greptile |
| hook-development | ~/.claude/plugins/marketplaces/ | This skill should be used when the user asks to "create a hook", "add a PreToolUse/PostToolUse/Stop hook", "validate... | Use when the matching workflow is requested | Skill: hook-development |
| imessage | ~/.claude/plugins/marketplaces/ | iMessage | Use when the matching workflow is requested | Skill: imessage |
| jdtls-lsp | ~/.claude/plugins/marketplaces/ | jdtls-lsp | Use when the matching workflow is requested | Skill: jdtls-lsp |
| kotlin-lsp | ~/.claude/plugins/marketplaces/ | Kotlin language server for Claude Code, providing code intelligence, refactoring, and analysis. | Use when the matching workflow is requested | Skill: kotlin-lsp |
| learning-output-style | ~/.claude/plugins/marketplaces/ | Learning Style Plugin | Use when the matching workflow is requested | Skill: learning-output-style |
| lua-lsp | ~/.claude/plugins/marketplaces/ | lua-lsp | Use when the matching workflow is requested | Skill: lua-lsp |
| math-olympiad | ~/.claude/plugins/marketplaces/ | Solve competition math problems (IMO, Putnam, USAMO, AIME) with adversarial | Use when the matching workflow is requested | Skill: math-olympiad |
| mcp-integration | ~/.claude/plugins/marketplaces/ | This skill should be used when the user asks to "add MCP server", "integrate MCP", "configure MCP in plugin", "use .m... | Use when the matching workflow is requested | Skill: mcp-integration |
| mcp-server-dev | ~/.claude/plugins/marketplaces/ | mcp-server-dev | Use when the matching workflow is requested | Skill: mcp-server-dev |
| php-lsp | ~/.claude/plugins/marketplaces/ | php-lsp | Use when the matching workflow is requested | Skill: php-lsp |
| playground | ~/.claude/plugins/marketplaces/ | Creates interactive HTML playgrounds — self-contained single-file explorers that let users configure something visual... | Use when the matching workflow is requested | Skill: playground |
| plugin-dev | ~/.claude/plugins/marketplaces/ | Plugin Development Toolkit | Use when the matching workflow is requested | Skill: plugin-dev |
| plugin-settings | ~/.claude/plugins/marketplaces/ | This skill should be used when the user asks about "plugin settings", "store plugin configuration", "user-configurabl... | Use when the matching workflow is requested | Skill: plugin-settings |
| plugin-structure | ~/.claude/plugins/marketplaces/ | This skill should be used when the user asks to "create a plugin", "scaffold a plugin", "understand plugin structure"... | Use when the matching workflow is requested | Skill: plugin-structure |
| pr-review-toolkit | ~/.claude/plugins/marketplaces/ | PR Review Toolkit | Use when the matching workflow is requested | Skill: pr-review-toolkit |
| pyright-lsp | ~/.claude/plugins/marketplaces/ | pyright-lsp | Use when the matching workflow is requested | Skill: pyright-lsp |
| ralph-loop | ~/.claude/plugins/marketplaces/ | Ralph Loop Plugin | Use when the matching workflow is requested | Skill: ralph-loop |
| ruby-lsp | ~/.claude/plugins/marketplaces/ | ruby-lsp | Use when the matching workflow is requested | Skill: ruby-lsp |
| rust-analyzer-lsp | ~/.claude/plugins/marketplaces/ | rust-analyzer-lsp | Use when the matching workflow is requested | Skill: rust-analyzer-lsp |
| scripts | ~/.claude/plugins/marketplaces/ | Hook Development Utility Scripts | Use when the matching workflow is requested | Skill: scripts |
| session-report | ~/.claude/plugins/marketplaces/ | Generate an explorable HTML report of Claude Code session usage (tokens, cache, subagents, skills, expensive prompts)... | Use when the matching workflow is requested | Skill: session-report |
| skill-creator | ~/.claude/plugins/marketplaces/ | Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a... | Use when the matching workflow is requested | Skill: skill-creator |
| skill-development | ~/.claude/plugins/marketplaces/ | This skill should be used when the user wants to "create a skill", "add a skill to plugin", "write a new skill", "imp... | Use when the matching workflow is requested | Skill: skill-development |
| skill-name | ~/.claude/plugins/marketplaces/ | Trigger conditions for this skill | Use when the matching workflow is requested | Skill: skill-name |
| swift-lsp | ~/.claude/plugins/marketplaces/ | swift-lsp | Use when the matching workflow is requested | Skill: swift-lsp |
| telegram | ~/.claude/plugins/marketplaces/ | Telegram | Use when the matching workflow is requested | Skill: telegram |
| typescript-lsp | ~/.claude/plugins/marketplaces/ | typescript-lsp | Use when the matching workflow is requested | Skill: typescript-lsp |
| writing-hookify-rules | ~/.claude/plugins/marketplaces/ | This skill should be used when the user asks to "create a hookify rule", "write a hook rule", "configure hookify", "a... | Use when the matching workflow is requested | Skill: writing-hookify-rules |
