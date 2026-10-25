## 1. SLO 目录（第三输入）
- [ ] 1.1 `testenv/k8s_slos.yaml` + `testenv/test_k8s_slos.py`：目录与世界 `slo` 块一致，没有 SLO 的 workload 不在目录里
- [ ] 1.2 `collect.py`：`load_slos`、`collect_facts(slos=...)`；目标与 deadline 取自目录（证据 `source: slo_catalog`），容器名；不给目录时行为不变
- [ ] 1.3 `policy.py`：条件 C 对不在目录的 workload 为 UNKNOWN；deadline 取自目录；决策追加 `container`、`slo`；条件 D 的 max_nodes 行为加测试
- [ ] 1.4 契约修订：本 spec delta 的 MODIFIED 两节

## 2. 判断层
- [ ] 2.1 `narrate.py` + `test_narrate.py`：隔离 prompt、落地校验与抹除、verdict 不变断言、模板退回、trace、缺盒带报错
- [ ] 2.2 用真实 `claude -p` 录制盒带（`FINOPS_LLM_MODE=record`）；`claude` 不可用则在汇报的 BLOCKED 里说明，不伪造

## 3. 计划与台账
- [ ] 3.1 `plans.py` + `test_plans.py`：契约形状、停止条件、DENY/UNKNOWN/NOOP 无计划
- [ ] 3.2 `ledger.py` + `test_ledger.py`：去重、过期、批准、halted + safe_halt、回滚后不复提

## 4. 串联与评估
- [ ] 4.1 `pipeline.py`、`__main__.py` 的 `k8s run|mark|clear-halt` + 端到端测试（进程内 server）
- [ ] 4.2 `eval/score_k8s.py`、`eval/test_score_k8s.py`、`eval/k8s_baseline.json`
- [ ] 4.3 Makefile 末尾追加 `k8s-eval`、`k8s-days`；`.github/workflows/ci.yml` 加 `make k8s-eval` 一步；`scripts/k8s_days.sh`

## 5. 验收
- [ ] 5.1 `make k8s-eval` 通过（K3）：verdict_accuracy ≥ 0.9、unsafe_allow = 0、unknown_recall = 1.0、request_in_range ≥ 0.8
- [ ] 5.2 K4：每条决策有中文叙述，0 个未落地数字，verdict 叙述前后不变
- [ ] 5.3 K8：`make k8s-days` 三天打印 new / unchanged / expired；applied 后 SLO 退化单测
- [ ] 5.4 `make test`、`make eval`（附录数字不变）、`python3 tools/check_rules.py --docs` 全过
- [ ] 5.5 evidence.md、`reports/0031-k8s-pipeline.html`、`notes/blog/0031-k8s-pipeline.md`
