## 1. 文档
- [x] 1.1 `docs/AGENT_TOPOLOGY.md`：完整拓扑 + 上下文、工具边界、状态机、验证、失败恢复、评估六张分图，每张附 `file:line`
- [x] 1.2 README 文档索引与 2.1 链接到它
## 2. 验收
- [ ] 2.1 七个 mermaid 块全部能渲染：`mmdc -i docs/AGENT_TOPOLOGY.md -o <tmp>/out.md` 输出 7 个 ✅
- [ ] 2.2 `python3 tools/check_rules.py --docs` 通过（路径都存在）
- [ ] 2.3 文中引用的 `file:line` 指向的符号与描述一致（逐条 grep）
