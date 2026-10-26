## Why

README 有判定顺序图和主线架构图，但 agent 工程那一面（上下文、工具边界、状态机、验证、失败恢复、评估）没有一张能对着讲的拓扑。讲 agent 部分时要靠口述把六个机制和代码位置串起来，容易讲偏。

## What Changes

- 新增 `docs/AGENT_TOPOLOGY.md`：一张完整拓扑，加六张分图（上下文、工具边界、状态机、验证、失败恢复、评估），每张图下列出对应的 `file:line`。mermaid 直接写在 markdown 里，GitHub 渲染。
- README 第二部分链接到这份文档。
- 图只画仓库里已有的东西；不改代码。

## Impact

纯文档。不影响任何判定、评分和测试。
