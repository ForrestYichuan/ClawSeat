# Claude Code 安装驱动

本文把通用 [安装 Agent Prompt](../../INSTALL_AGENT_PROMPT.zh-CN.md) 映射到
Claude Code 的工具行为。

## Voice & Tone

沿用通用 prompt 的语气协议：简洁、明确、每次给推荐理由。整个会话都支持
`/en`、`/zh`、空回车默认、`详`、推荐★。

## Confirmation Pattern

Claude Code 工具使用顺序：

1. **Read** `docs/INSTALL.zh-CN.md`、`docs/INSTALL_AGENT_PROMPT.zh-CN.md` 和相关安装脚本。
2. **Bash** 运行 Step 0：`bash scripts/install.sh --detect-only`。
3. **AskUserQuestion** 处理每次确认。语言、模板、项目名、摘要、执行、
   失败修复选择都使用 rich UI，不要只用普通 markdown。
4. **Bash run_in_background** 执行长时间的 `install.sh`，方便继续叙述和监控。
5. **Monitor** 后台命令，并总结每个状态变化。
6. **TaskCreate** 在执行前建立 11 步 progress checklist。

确认行保持：

```text
推荐★：<choice>
理由：<一句话>
确认：[回车=默认 / 修改 / 详 / 取消]
```

AskUserQuestion reference JSON：

```json
{
  "question": "请选择 ClawSeat template。",
  "header": "Template",
  "options": [
    {"label": "Creative (Recommended)", "description": "首次安装默认推荐。"},
    {"label": "Engineering", "description": "增加 reviewer，用于代码审查 lane。"},
    {"label": "Solo", "description": "v3 MULTI_TEAM_MINIMAL 兼容别名。"}
  ]
}
```

## Failure Pattern

Bash 或 Monitor 失败时，先分类失败，再给 2-3 个具体修复选项。不要 kill
无关 tmux 或 iTerm session。PTY 资源耗尽时，停止并按项目协议升级。

## 启动期 Trust / Permission Prompt

Claude Code v2.1+ 在新 seat 首次连接时可能出现正常的 trust 或 permission
确认界面。直接确认即可，不要把它当作 `install.sh` bug 上报。

常见正常 prompt：

1. `Yes, I trust this folder` workspace trust prompt -> 选择 `1` 或按 Enter。
2. `Bypass Permissions` 权限分级 prompt，显示 default/strict/bypass 选项
   -> 选择 bypass，通常是 `1` 或 Enter。
3. `Allow this skill to read...` skill 首次授权 prompt -> 选择 Yes 或 `1`。

判断规则：启动期出现，并且文案包含 `trust`、`permission`、`bypass` 或
`allow`，就是正常 Claude Code 授权，直接确认。真异常是进程崩溃、Python
traceback、API 401/secret missing、tmux session 不存在，或窗口始终没拉起。

## detect_all JSON Reference

把 Step 0 输出按 JSON 读取，并保留给后续决策：OAuth 状态、PTY 状态、branch
状态、已有项目、timestamp。
