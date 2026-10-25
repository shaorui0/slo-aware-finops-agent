## 1. 场景

- [x] 1.1 `testenv/k8s_scenarios/after_apply.yaml`：应用后 14 天，四条计划（api-steady 干净、packing-trap、hpa-managed 批准后应用、no-slo 回滚）加窗口中途的无关副本增长
- [x] 1.2 `testenv/k8s_scenarios/after_clean.yaml`：对照，两条干净变更
- [x] 1.3 `testenv/test_k8s_scenarios.py`：覆盖层是基础世界的 14 天延伸、基础字段不变、没有手填节点数、事件类型合法、五种现象齐全、可被 metrics_server 提供且 HPA 负载进入用量

## 2. 核实器

- [x] 2.1 `finops_agent/k8s/verify.py`：时间线 + 每小时 FFD 的节点数 + HPA 副本模型
- [x] 2.2 状态链分解（b → a/e → d → c），逐条份额，精确闭合断言
- [x] 2.3 输出 `k8s_verify.json`（契约形状）与 `k8s_verify_detail.json`
- [x] 2.4 `finops_agent/k8s/test_verify.py`：闭合（含所有应用顺序）、五类原因各自归类、packing-trap 实际 0 与白话解释、对照不造偏差、节点数来自 FFD、契约形状、输入不一致时报错

## 3. 脚本与文档

- [x] 3.1 `scripts/k8s_verify.sh` 与 Makefile 末尾的 `k8s-verify` 目标
- [x] 3.2 `docs/K8S_SAVINGS_VERIFICATION.md` 由数据生成（中文、表格、方法与局限）

## 4. 验收

- [x] 4.1 `make k8s-verify` 产出 `runs/k8s-verify/k8s_verify.json` 并重新生成文档
- [x] 4.2 packing-trap 实际 0 且偏差为 (e)；至少一条实现率不是 100% 且原因归类正确；分解闭合
- [x] 4.3 `python3 -W ignore -m unittest finops_agent.k8s.test_verify testenv.test_k8s_scenarios`，全量 `discover -s finops_agent`、`-s testenv`，`python3 tools/check_rules.py --docs` 通过
- [x] 4.4 `make k8s-eval` 的数字与改动前一致；`testenv/k8s_world.yaml` 无改动
- [x] 4.5 evidence.md、`reports/0033-k8s-verify.html`、`notes/blog/0033-k8s-verify.md`
