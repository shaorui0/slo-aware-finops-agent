## 1. 实现
- [x] 1.1 先写测试：`finops_agent/k8s/test_recent_regime.py`（单元：CPU / 内存 / 边界 / 缺失 / FAIL 优先 / 无近期事实不变；端到端：`testenv/k8s_scenarios/recent_step.yaml`）与 `eval/test_score_k8s.py` 的 `RecentUsageTest`，实现前跑一遍，失败输出存 evidence.md
- [x] 1.2 `config/k8s_policy.yaml`：`recent_hours: 6`；`collect.py`：近期 max 取数，证据仅在构成因素时写；`policy.py`：条件 B 子项；`report.py`：`evidence_labels` 中文标签
- [x] 1.3 `eval/score_k8s.py`：`allow_below_recent` 指标、硬闸门、基线；`eval/k8s_baseline.json` 加 0
- [x] 1.4 `testenv/k8s_scenarios/recent_step.yaml`（加法，基础世界不动）

## 2. 验收
- [x] 2.1 `make test`、`make k8s-eval`（unsafe_allow 0，`allow_below_recent` 0，对基线无回退，盒带不缺）、`make k8s-days`、`make check` 通过
- [x] 2.2 日报样例：受影响的决策显示近期值与中文标签
- [x] 2.3 evidence.md、HTML 报告、博客笔记（末行 `PROGRESS_ROW:`）；第三个世界相关数字一律标注「污染」
