## 1. 实现
- [ ] 1.1 先写测试：`finops_agent/k8s/test_stale_discovery.py`（collect、policy、narrate 模板、report、ledger），在实现前跑一遍，失败输出存 evidence.md
- [ ] 1.2 `config/k8s_policy.yaml`：`stale_days: 3`；`collect.py`：窗口发现 + 逐资源 `freshness`；`policy.py`：条件 A 子项
- [ ] 1.3 `testenv/k8s_scenarios/stale_workload.yaml`（加法，基础世界不动）+ 测试：过期 workload 经 metrics server 端到端为 UNKNOWN

## 2. 验收
- [ ] 2.1 `make test`、`make k8s-eval`（unsafe_allow 0，对 `eval/k8s_baseline.json` 无回退）、`make k8s-days`、`make check` 通过
- [ ] 2.2 日报样例渲染过期 workload：grep UNKNOWN 表
- [ ] 2.3 evidence.md、HTML 报告、博客笔记（末行 `PROGRESS_ROW:`）
