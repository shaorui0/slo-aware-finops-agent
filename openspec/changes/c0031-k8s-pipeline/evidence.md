# c0031 验收证据

所有输出为原样。路径中的本机前缀已去掉。LLM 盒带由真实 `claude -p`（sonnet）录制，见 K4。

## 契约修订前后：同一世界、同一内核，只差 SLO 目录
```
without catalog (c0029): 16/18 verdicts right; wrong=[('K-data-batch-nightly-cpu', 'REQUIRE_APPROVAL', 'DENY'), ('K-data-batch-nightly-memory', 'REQUIRE_APPROVAL', 'DENY')]
with testenv/k8s_slos.yaml (c0031): 18/18 verdicts right; wrong=[]
```
差的两条是 batch-nightly 的 cpu 与 memory：deadline 只在目录里，指标里没有。

## K3：`make k8s-eval`（LLM 回放盒带，无网络、无 API key）
```
$ make k8s-eval
k8s run k8s-local (as_of 2026-10-25T23:55:00Z): 18 decisions {'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}, plans 8, ledger {'new': 8, 'unchanged': 0, 'expired': 0, 'halted': 0, 'approved': 0, 'suppressed': 0, 'held': 0}, narrative {'llm': 18}
runs/k8s-local/k8s_decisions.json
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0}
n={'expected': 18, 'decisions': 18, 'unknown_expected': 2, 'ranged': 8, 'realizable': 2} narrative={'llm': 18}
gates: pass
exit 0
```
阈值：verdict_accuracy ≥ 0.9、unsafe_allow = 0、unknown_recall = 1.0、request_in_range ≥ 0.8，基线 `eval/k8s_baseline.json`。
缺盒带的行为：录制前第一次回放，`make k8s-eval` 以 `CassetteMissing: no cassette 55539a87f220 ...` 非零退出，server 照常被停掉；
单测 `test_replay_without_cassette_fails_loudly`（管道级）与 `test_replay_missing_cassette_fails_loudly`（叙述级）固化这一行为。

## K4：叙述
- 每条决策一次独立调用（prompt 只含该决策的 facts 与 evidence）；18 次回放 trace：tokens_in 148381、tokens_out 9920、录制成本 0.6927 USD、mode=replay；Prometheus 调用 208 次。
- 评分闸里的 `narrative_ungrounded` 对每条叙述重新跑 `judge/validate.py` 的数字落地检查，18 条均为空（上面 `gates: pass` 含这一项）。
- verdict 不变：`pipeline.py` 在叙述前后比较全部 verdict，不一致即断言失败；`narrate.narrative()` 对每条决策断言除 `narrative` 外逐字段相同。
- 抹除与改 verdict 的单测：

```
test_applied_then_slo_degrades_halts_and_stops_plans (finops_agent.k8s.test_ledger.LedgerTest.test_applied_then_slo_degrades_halts_and_stops_plans) ... ok
test_replay_missing_cassette_fails_loudly (finops_agent.k8s.test_narrate.NarrateTest.test_replay_missing_cassette_fails_loudly) ... ok
test_llm_cannot_change_verdict (finops_agent.k8s.test_narrate.NarrateTest.test_llm_cannot_change_verdict) ... ok
test_ungrounded_number_sentence_is_redacted (finops_agent.k8s.test_narrate.NarrateTest.test_ungrounded_number_sentence_is_redacted) ... ok
test_batch_without_catalog_deadline_has_no_deadline_even_if_label_exists (finops_agent.k8s.test_slo_catalog.CatalogTest.test_batch_without_catalog_deadline_has_no_deadline_even_if_label_exists) ... ok
test_catalog_entry_with_metrics_passes (finops_agent.k8s.test_slo_catalog.CatalogTest.test_catalog_entry_with_metrics_passes) ... ok
test_catalog_numbers_are_evidence_with_their_own_source (finops_agent.k8s.test_slo_catalog.CatalogTest.test_catalog_numbers_are_evidence_with_their_own_source) ... ok
test_deadline_comes_from_the_catalog_not_from_the_metric_label (finops_agent.k8s.test_slo_catalog.CatalogTest.test_deadline_comes_from_the_catalog_not_from_the_metric_label) ... ok
test_no_catalog_keeps_the_old_behaviour (finops_agent.k8s.test_slo_catalog.CatalogTest.test_no_catalog_keeps_the_old_behaviour) ... ok
test_workload_missing_from_catalog_is_unknown_even_with_slo_metrics (finops_agent.k8s.test_slo_catalog.CatalogTest.test_workload_missing_from_catalog_is_unknown_even_with_slo_metrics) ... ok

```
- 样例（K-tools-no-slo-cpu，REQUIRE_APPROVAL，节选，来自 `runs/k8s-local/k8s_decisions.json`）：
  - 现状：tools/no-slo 的 CPU request 为 1.0 core，limit 为 2.0 core，副本数 2，p99 为 0.1307。
  - 结论：由条件 C 驱动，SLO 目录里没有该 workload 的定义，状态为 UNKNOWN。
  - 补什么会改变：在 SLO 目录里为该 workload 定义 SLO。
  - 提案：request 从 1.0 core 改为 0.2 core，影响 2 个副本，账面释放 36.9088 USD/月，装箱后可兑现 140.16 USD/月；需人工确认没有未登记的 SLO。
- 18 条里 17 条第一次即合格；1 条（batch-nightly 内存）模型返回的 JSON 嵌套错位，被严格解析拒绝；按设计该条在 record 模式退回模板叙述。删除这一条盒带重录后得到合格回复。这一过程是现场观察，没有保留拒绝记录，因为 replay 只读最终盒带。

## K8：`make k8s-days`（三天共用一个台账）
```
$ make k8s-days
bash scripts/k8s_days.sh
== k8s-day1 (day 1, base world, as_of 2026-10-25T00:00:00Z) ==
   new=10  unchanged=0  expired=0   plans=10  verdicts={'ALLOW': 7, 'DENY': 4, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
   new       shop/api-steady/cpu            ALLOW，目标 0.25 core
   new       shop/api-steady/memory         ALLOW，目标 1.125 GiB
   new       shop/burning-slo/cpu           ALLOW，目标 0.15 core
   new       shop/burning-slo/memory        ALLOW，目标 0.75 GiB
   new       shop/hpa-managed/cpu           REQUIRE_APPROVAL，目标 0.45 core
   new       shop/hpa-managed/memory        ALLOW，目标 1.125 GiB
   new       shop/mem-near-oom/cpu          ALLOW，目标 0.1 core
   new       shop/packing-trap/cpu          ALLOW，目标 0.2 core
   new       tools/no-slo/cpu               REQUIRE_APPROVAL，目标 0.2 core
   new       tools/no-slo/memory            REQUIRE_APPROVAL，目标 1 GiB
== k8s-day2 (day 2, base world, one day later, as_of 2026-10-26T00:00:00Z) ==
   new=0  unchanged=8  expired=2   plans=8  verdicts={'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
   expired   shop/burning-slo/cpu           本轮 verdict 为 DENY，不再是可提案
   expired   shop/burning-slo/memory        本轮 verdict 为 DENY，不再是可提案
== k8s-day3 (day 3, drift_day3: api-steady load +60%, as_of 2026-10-28T00:00:00Z) ==
   new=1  unchanged=7  expired=1   plans=8  verdicts={'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
   expired   shop/api-steady/cpu            目标值变化超过容差：ALLOW 0.25 -> ALLOW 0.35
   new       shop/api-steady/cpu            ALLOW，目标 0.35 core
== api-steady/cpu: ledger history ==
   P-K-shop-api-steady-cpu-0.25       expired   first_seen 2026-10-25T00:00:00Z  last_seen 2026-10-26T00:00:00Z
      k8s-day1  proposed  ALLOW，目标 0.25 core
      k8s-day3  expired   目标值变化超过容差：ALLOW 0.25 -> ALLOW 0.35
   P-K-shop-api-steady-cpu-0.35       proposed  first_seen 2026-10-28T00:00:00Z  last_seen 2026-10-28T00:00:00Z
      k8s-day3  proposed  ALLOW，目标 0.35 core
```
- 第 1 天：10 条新提案。burning-slo 此时（燃烧事件 10-25 16:00 之前）为 ALLOW。
- 第 2 天：8 条 unchanged（last_seen 前移），burning-slo 的两条因 verdict 变 DENY 而 expired。
- 第 3 天（漂移场景）：api-steady 的 cpu 提案目标 0.25 → 0.35（超过 5%），旧提案 expired，新建 0.35。
- applied 后 SLO 退化：`finops_agent/k8s/test_ledger.py::test_applied_then_slo_degrades_halts_and_stops_plans`（halted、`safe_halt`、无计划、halted 期间不新建、人工清除后恢复）与 `test_pipeline.py::test_halt_stops_plans_end_to_end`（经 CLI 与 ledger 文件的端到端）。

## 全量
```
$ python3 -W ignore -m unittest discover -t . -s finops_agent -p 'test_*.py'
Ran 257 tests in 26.444s
OK
$ python3 -W ignore -m unittest discover -t . -s eval -p 'test_*.py'
Ran 44 tests in 17.261s
OK
$ python3 -W ignore -m unittest discover -t . -s testenv -p 'test_*.py'
Ran 62 tests in 11.218s
OK
$ make eval   # 附录，数字与 eval/baseline.json 一致
{"recall": 1.0, "savings_recall": 1.0, "precision": 1.0, "savings_accuracy": 0.9167, "step_agreement": 1.0, "depth": {"shallow_recall": 1.0, "deep_recall": 1.0}, "leads": 7}
missed: 
gates: pass
$ python3 tools/check_rules.py --docs
check_rules: OK
$ python3 -W ignore -m unittest discover -t . -s finops_agent/k8s -p 'test_*.py'   # K2 的验证命令
Ran 97 tests ... OK
```

## 已知的失败（与本 change 无关）
`make test` 的第二步 `tools/test_build_index.py::test_index_lists_every_change` 失败：`reports/index.html` 还没有收进 c0029（在 c0029 的合并提交 5726e52 上同样失败）。
本任务按约束不改 `reports/index.html`，由主 agent 在合并时重新生成导读页；重新生成后该测试应转绿。其余四组测试（hooks、finops_agent、eval、testenv）全过。
