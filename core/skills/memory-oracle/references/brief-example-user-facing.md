# User-facing brief example

## Scenario
A product-facing feature modifies user-visible behavior and must include acceptance checks.

```markdown
Goal: the exact user-visible result and where it must appear.
Context: only the facts needed to start.
Boundary: allowed files/actions and no-go areas.
Anti-goal: weaker implementation that must not count as done.
Acceptance: observable behavior, tests, or artifact evidence.

user_facing: true
product_acceptance_criteria:
  - [ ] 关键验收项 1：运行 `python3 -m pytest`，关键路径测试通过。
  - [ ] 关键验收项 2：在 5 分钟内验证主命令成功重跑一次闭环，无阻塞。
  - [ ] 关键验收项 3：产出有可复核证据（日志片段、命令输出、截图路径）。
linked_finding: install-finding-example
rca_override: false
```
