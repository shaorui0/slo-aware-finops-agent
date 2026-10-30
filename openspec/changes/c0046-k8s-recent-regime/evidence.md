# c0046 验收证据

## 1. 修复前：测试失败（实现 commit 之前跑的）

测试 `finops_agent/k8s/test_recent_regime.py`、`eval/test_score_k8s.py::RecentUsageTest` 与场景 `testenv/k8s_scenarios/recent_step.yaml` 先于实现写成。场景 = 基础世界 + `step-up-svc`：CPU 在 as_of 前 3 小时翻三倍（0.2 → 0.6），14 天 p99 仍是旧状态，推荐 0.3 core。

```
$ python3 -W ignore -m unittest finops_agent.k8s.test_recent_regime eval.test_score_k8s
ERROR: setUpClass (eval.test_score_k8s.RecentUsageTest)
FAIL: test_cpu_recommendation_below_recent_max_is_not_allow (finops_agent.k8s.test_recent_regime.RecentSubcheckUnitTest.test_cpu_recommendation_below_recent_max_is_not_allow)
FAIL: test_memory_gets_the_same_subcheck (finops_agent.k8s.test_recent_regime.RecentSubcheckUnitTest.test_memory_gets_the_same_subcheck)
FAIL: test_missing_recent_value_is_unknown_not_pass (finops_agent.k8s.test_recent_regime.RecentSubcheckUnitTest.test_missing_recent_value_is_unknown_not_pass)
FAIL: test_report_labels_the_recent_evidence_in_chinese (finops_agent.k8s.test_recent_regime.StepUpScenarioTest.test_report_labels_the_recent_evidence_in_chinese)
FAIL: test_step_up_cpu_is_not_allow_and_b_cites_the_recent_value (finops_agent.k8s.test_recent_regime.StepUpScenarioTest.test_step_up_cpu_is_not_allow_and_b_cites_the_recent_value)
AssertionError: 'ALLOW' == 'ALLOW'
Ran 21 tests in 1.573s
FAILED (failures=5, errors=1)
```

关键一条：`test_step_up_cpu_is_not_allow_and_b_cites_the_recent_value` 里 `AssertionError: 'ALLOW' == 'ALLOW'`：修复前，推荐 0.3 core、近期最大 0.63 core 的决策是 ALLOW。`RecentUsageTest` 的 setUpClass 报错是因为 `score_k8s.recent_usage` 还不存在。

## 2. 修复后

```
Ran 20 tests in 10.986s
OK
```

端到端（合成指标服务器 `recent_step` 场景，`collect_facts` → `decide`），step-up-svc：

```
K-shop-step-up-svc-cpu REQUIRE_APPROVAL 0.3
K-shop-step-up-svc-memory ALLOW 0.625
B: UNKNOWN 近期用量高于推荐值：最近 6 小时观测最大 0.63335 core，推荐 0.3 core 低于 0.63335 × (1+0.1)，用量可能刚发生变化，14 天统计还描述不了新状态
needs: ['需要人工确认近期（最近 6 小时）的用量变化是一次性的，还是新常态（新常态下 14 天 p99 / 峰值已过时）']
```

新增证据条目（E24，只在子项构成判定因素时写入）：

```
{'id': 'E24', 'query': 'max by (workload)(max_over_time(container_cpu_usage_cores{container="app",namespace="shop",workload="step-up-svc"}[6h]))', 'window': '6h', 'value': 0.63335, 'source': 'prometheus'}
```

## 3. 日报样例

日报「做了哪些分析 / 证据表」里受影响的决策（shop/step-up-svc · CPU），条件 B 显示「未知」及近期值，证据表有中文标签：

```
B 降完还扛得住吗 | 未知 | 近期用量高于推荐值：最近 6 小时观测最大 0.63335 core，推荐 0.3 core 低于 0.63335 × (1+0.1)，用量可能刚发生变化，14 天统计还描述不了新状态 （证据 E6、E11、E18、E24）
证据 | 含义 | 值 | 窗口 | 查询
E24  | 近期使用量 max（最近 6 小时内的最大值） | 0.6333 | 6h | max by (workload)(max_over_time(container_cpu_usage_cores{container="app",namespace="shop",workload="step-up-svc"}[6h]))
结论 REQUIRE_APPROVAL 需要你拍板：条件 B 未知：近期用量高于推荐值：最近 6 小时观测最大 0.63335 core，…
```

## 4. 全量验收

`make test`：`tools/`、`hooks/` 之外的目录全部通过；唯一失败是 `tools/test_build_index.py::test_index_lists_every_change`（README 进度表还没有 c0046 这一行），按任务说明由主 agent 追加 README 行后消除，不是本 change 的缺陷。因为 make 在 tools 步骤失败后停止，其余目录单独跑了同样的 discover 命令：

```
hooks:      OK
finops_agent:  Ran 377 tests ... OK
eval:          Ran 52 tests ... OK
testenv:       Ran 128 tests ... OK
tools:      Ran 29 tests, FAILED (failures=1): test_index_lists_every_change  ('c0046-k8s-recent-regime' not found in the index)
```

`make k8s-eval`（回放盒带，**没有重录**，没有缺盒带；基础世界 18 条决策与此前逐条一致，其中没有一条含「近期」文字）：

```
k8s run k8s-local (as_of 2026-10-25T23:55:00Z): 18 decisions {'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}, plans 8, ledger {'new': 8, 'unchanged': 0, 'expired': 0, 'halted': 0, 'approved': 0, 'suppressed': 0, 'held': 0}, narrative {'llm': 18}
runs/k8s-local/k8s_decisions.json
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0, "allow_below_recent": 0}
n={'expected': 18, 'decisions': 18, 'unknown_expected': 2, 'ranged': 8, 'realizable': 2} narrative={'llm': 18}
gates: pass
rc=0
```

`make k8s-days`（rc=0，三天的 new / unchanged / expired 与此前相同）：

```
== k8s-day1 (day 1, base world, as_of 2026-10-25T00:00:00Z) ==
   new=10  unchanged=0  expired=0   plans=10  verdicts={'ALLOW': 7, 'DENY': 4, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
== k8s-day2 (day 2, base world, one day later, as_of 2026-10-26T00:00:00Z) ==
   new=0  unchanged=8  expired=2   plans=8  verdicts={'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
   expired   shop/burning-slo/cpu           本轮 verdict 为 DENY，不再是可提案
   expired   shop/burning-slo/memory        本轮 verdict 为 DENY，不再是可提案
== k8s-day3 (day 3, drift_day3: api-steady load +60%, as_of 2026-10-28T00:00:00Z) ==
   new=1  unchanged=7  expired=1   plans=8  verdicts={'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
   expired   shop/api-steady/cpu            目标值变化超过容差：ALLOW 0.25 -> ALLOW 0.35
== api-steady/cpu: ledger history ==
   P-K-shop-api-steady-cpu-0.25       expired   first_seen 2026-10-25T00:00:00Z  last_seen 2026-10-26T00:00:00Z
      k8s-day1  proposed  ALLOW，目标 0.25 core
      k8s-day3  expired   目标值变化超过容差：ALLOW 0.25 -> ALLOW 0.35
   P-K-shop-api-steady-cpu-0.35       proposed  first_seen 2026-10-28T00:00:00Z  last_seen 2026-10-28T00:00:00Z
      k8s-day3  proposed  ALLOW，目标 0.35 core
```

`make check`：

```
python3 tools/check_rules.py --docs
check_rules: OK
rc=0
```

## 5. 污染声明：第三个预注册世界

以下数字**已污染**（`recent_hours` = 6 与子项设计是读过 §10 之后写的），**不是修复的证据**，只用来说明打分器和子项各自看到了什么；修复的证据是上面的样本内场景。检验泛化需要第四个预注册世界（EXPERIMENTS §10.5 第 3 条，后续 change）。

- 用新打分器给 c0044 当时的决策文件（`openspec/changes/c0044-k8s-fresh3-eval/k8s_fresh3_decisions.json`）重新打分：`allow_below_recent` = 1，`['promo-renderer/cpu']`，`unsafe_allow` 仍是 0；打分器从世界文件重新生成的近期用量，不读决策里的数字。闸门失败，退出码 1。
- 用修复后的 agent（无 LLM，只跑 collect + decide）对同一个世界：promo-renderer/cpu = REQUIRE_APPROVAL（B 未知，近期 0.63628 core 对推荐 0.3 core），verdict_accuracy 1.0（24/24），`allow_below_recent` 0，其它 23 条裁决不变。污染，不计入任何评估。
- 没有对 fresh3 调任何阈值；`recent_hours` 没有扫描过。
