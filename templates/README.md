`scripts/install.sh` 首次拉起 v2 项目：创建 `<project>-memory`，打开共享 memories 窗口，并按模板为 workers 窗口建立 planner / builder / patrol / reviewer / designer 等 seat。`clawseat-solo` 现在只是 legacy alias：安装入口会转到 v3 `MULTI_TEAM_MINIMAL`，由一个 project-memory 管理一个或多个 planner+builder 子项目组以及 `quality-docs`。
`clawseat-creative` 已于 2026-05-02 废弃。使用该模板的项目（install/koder/lotus-radar）在下次 reinstall 时迁移至 `clawseat-engineering`。
旧 `templates/clawseat-monitor.yaml` tmuxp 恢复入口已删除；v2 使用 `agent_admin window open-grid --project <project> --recover` 按 project.toml 动态恢复 workers 窗口。
`patrol` 是唯一 verification seat 名称；旧 verification-seat 别名已于 2026-04-29 移除。
