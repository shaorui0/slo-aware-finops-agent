# c0033 验收证据（原始输出）

## 1. make k8s-verify
```
$ make k8s-verify
bash scripts/k8s_verify.sh
k8s run k8s-verify (as_of 2026-10-25T23:55:00Z): 18 decisions {'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}, plans 8, ledger {'new': 8, 'unchanged': 0, 'expired': 0, 'halted': 0, 'approved': 0, 'suppressed': 0, 'held': 0}, narrative {'template': 18}
k8s verify [main] after_apply.yaml: predicted 280.32 realized 140.16 bill delta -70.08 closure diff 4.26e-14
k8s verify [control] after_clean.yaml: predicted 140.16 realized 140.16 bill delta -140.16 closure diff -2.84e-14
runs/k8s-verify/k8s_verify.json
wrote runs/k8s-verify/k8s_verify.json and docs/K8S_SAVINGS_VERIFICATION.md
```

### runs/k8s-verify/k8s_verify.json（契约形状）
```
[
  {
    "plan_id": "P-K-shop-api-steady-cpu-0.25",
    "decision_id": "K-shop-api-steady-cpu",
    "predicted_usd_month": 140.16,
    "realized_usd_month": 140.16,
    "ratio": 1.0,
    "nodes_before": 6,
    "nodes_after": 5,
    "deviations": [],
    "slo_ok": true,
    "window": "7d"
  },
  {
    "plan_id": "P-K-shop-packing-trap-cpu-0.2",
    "decision_id": "K-shop-packing-trap-cpu",
    "predicted_usd_month": 0.0,
    "realized_usd_month": 0.0,
    "ratio": null,
    "nodes_before": 6,
    "nodes_after": 5,
    "deviations": [
      {
        "cause": "(e) 装箱没兑现",
        "usd": 0.0,
        "explanation": "request 降了，账面释放 36.91 美元/月，但装箱（FFD）后节点数没变，账单不变，所以实际节省是 0。节点仍被另一类资源钉住（packing-trap 每个 pod 还预留 8 GiB，另一类资源没动），降这一条不会让任何一台节点空出来。"
      }
    ],
    "slo_ok": true,
    "window": "7d"
  },
  {
    "plan_id": "P-K-shop-hpa-managed-cpu-0.45",
    "decision_id": "K-shop-hpa-managed-cpu",
    "predicted_usd_month": 0.0,
    "realized_usd_month": 0.0,
    "ratio": null,
    "nodes_before": 6,
    "nodes_after": 5,
    "deviations": [
      {
        "cause": "(b) 无关增长",
        "usd": 70.08,
        "explanation": "无关的副本增长本来要新开节点，这条变更腾出的容量把它吸收了，所以多省了一台。"
      },
      {
        "cause": "(c) HPA 联动扩容",
        "usd": -70.08,
        "explanation": "hpa-managed 带 HPA（cpu 利用率目标 70%）：cpu request 降到 0.45 core，同样的用量利用率变高，HPA 多开副本。多出的副本需要更多节点，账单 +70.08 美元/月。"
      }
    ],
    "slo_ok": true,
    "window": "7d"
  },
  {
    "plan_id": "P-K-tools-no-slo-cpu-0.2",
    "decision_id": "K-tools-no-slo-cpu",
    "predicted_usd_month": 140.16,
    "realized_usd_month": 0.0,
    "ratio": 0.0,
    "nodes_before": 6,
    "nodes_after": 5,
    "deviations": [
      {
        "cause": "(b) 无关增长",
        "usd": -70.08,
        "explanation": "无关的副本增长占用了容量，这条变更腾出的位置被吃掉一部分。"
      },
      {
        "cause": "(d) 回滚",
        "usd": -70.08,
        "explanation": "变更在 2026-10-29T06:00:00Z 被回滚，AFTER 窗口里 request 已是原值，节省没有保住。"
      }
    ],
    "slo_ok": null,
    "window": "7d"
  }
]
```

### 分解闭合与状态链（k8s_verify_detail.json 摘录）
```
closure {"sum_causes_usd_month": -70.08, "bill_delta_usd_month": -70.08000000000004, "diff": 4.263256414560601e-14}
bill {"before_usd_month": 840.9599999999999, "after_usd_month": 770.8799999999999, "delta_usd_month": -70.08000000000004, "before_node_hours": 1008, "after_node_hours": 924}
stages_node_hours {"S0_before": 1008, "S0_after_hours_no_growth": 1008, "B_growth": 1092, "A_applied": 756, "D_after_rollbacks": 840, "C_after_hpa": 924}
b cluster 70.08
a P-K-shop-api-steady-cpu-0.25 -140.16
e P-K-shop-packing-trap-cpu-0.2 0.0
a P-K-shop-hpa-managed-cpu-0.45 -70.08
a P-K-tools-no-slo-cpu-0.2 -70.08
d P-K-tools-no-slo-cpu-0.2 70.08
c P-K-shop-hpa-managed-cpu-0.45 70.08
P-K-shop-api-steady-cpu-0.25 book 51.903 book_ratio 2.70042194092827 terms {'e': 0.0, 'b': -0.0, 'd': -0.0, 'c': -0.0}
P-K-shop-packing-trap-cpu-0.2 book 36.9088 book_ratio 0.0 terms {'e': -0.0, 'b': -0.0, 'd': -0.0, 'c': -0.0}
P-K-shop-hpa-managed-cpu-0.45 book 25.3748 book_ratio 0.0 terms {'e': -0.0, 'b': 70.08, 'd': -0.0, 'c': -70.08}
P-K-tools-no-slo-cpu-0.2 book 36.9088 book_ratio 0.0 terms {'e': 0.0, 'b': -70.08, 'd': -70.08, 'c': -0.0}
control closure {"sum_causes_usd_month": -140.16, "bill_delta_usd_month": -140.15999999999997, "diff": -2.842170943040401e-14}
```

## 2. 目标单测
```
$ python3 -W ignore -m unittest finops_agent.k8s.test_verify testenv.test_k8s_scenarios -v
test_clean_change_realizes_the_prediction (finops_agent.k8s.test_verify.CauseTest.test_clean_change_realizes_the_prediction) ... ok
test_control_scenario_invents_no_deviation (finops_agent.k8s.test_verify.CauseTest.test_control_scenario_invents_no_deviation) ... ok
test_hpa_scale_out_is_cause_c (finops_agent.k8s.test_verify.CauseTest.test_hpa_scale_out_is_cause_c) ... ok
test_packing_trap_realizes_zero_with_cause_e_in_plain_chinese (finops_agent.k8s.test_verify.CauseTest.test_packing_trap_realizes_zero_with_cause_e_in_plain_chinese) ... ok
test_prediction_is_the_decisions_realizable (finops_agent.k8s.test_verify.CauseTest.test_prediction_is_the_decisions_realizable) ... ok
test_ratio_not_100_percent_with_a_classified_cause (finops_agent.k8s.test_verify.CauseTest.test_ratio_not_100_percent_with_a_classified_cause) ... ok
test_rollback_is_cause_d_and_ratio_is_zero (finops_agent.k8s.test_verify.CauseTest.test_rollback_is_cause_d_and_ratio_is_zero) ... ok
test_unrelated_growth_is_cause_b_and_eats_part_of_the_bill_saving (finops_agent.k8s.test_verify.CauseTest.test_unrelated_growth_is_cause_b_and_eats_part_of_the_bill_saving) ... ok
test_bill_change_is_unrelated_growth_minus_realized (finops_agent.k8s.test_verify.ClosureTest.test_bill_change_is_unrelated_growth_minus_realized) ... ok
test_causes_sum_to_the_bill_change_exactly (finops_agent.k8s.test_verify.ClosureTest.test_causes_sum_to_the_bill_change_exactly) ... ok
test_closes_for_every_application_order (finops_agent.k8s.test_verify.ClosureTest.test_closes_for_every_application_order) ... ok
test_contract_rounding_stays_within_a_cent_per_entry (finops_agent.k8s.test_verify.ClosureTest.test_contract_rounding_stays_within_a_cent_per_entry) ... ok
test_each_plan_gap_is_the_sum_of_its_deviations (finops_agent.k8s.test_verify.ClosureTest.test_each_plan_gap_is_the_sum_of_its_deviations) ... ok
test_cli_writes_contract_detail_and_doc (finops_agent.k8s.test_verify.ContractTest.test_cli_writes_contract_detail_and_doc) ... ok
test_document_is_chinese_generated_and_has_no_local_paths (finops_agent.k8s.test_verify.ContractTest.test_document_is_chinese_generated_and_has_no_local_paths) ... ok
test_exact_contract_shape (finops_agent.k8s.test_verify.ContractTest.test_exact_contract_shape) ... ok
test_fails_loud_on_inconsistent_inputs (finops_agent.k8s.test_verify.ContractTest.test_fails_loud_on_inconsistent_inputs) ... ok
test_hpa_formula (finops_agent.k8s.test_verify.NodesFromFfdTest.test_hpa_formula) ... ok
test_node_count_is_ffd_over_the_after_state_requests (finops_agent.k8s.test_verify.NodesFromFfdTest.test_node_count_is_ffd_over_the_after_state_requests) ... ok
test_node_hours_change_with_the_requests_not_by_hand (finops_agent.k8s.test_verify.NodesFromFfdTest.test_node_hours_change_with_the_requests_not_by_hand) ... ok
test_marginals_telescope (finops_agent.k8s.test_verify.SequentialTest.test_marginals_telescope) ... ok
test_new_events_are_known_types_and_the_base_file_has_none (testenv.test_k8s_scenarios.OverlayTest.test_new_events_are_known_types_and_the_base_file_has_none) ... ok
test_no_node_count_is_typed_into_an_overlay (testenv.test_k8s_scenarios.OverlayTest.test_no_node_count_is_typed_into_an_overlay) ... ok
test_overlays_are_scenarios_of_the_base_world_with_14_days_after (testenv.test_k8s_scenarios.OverlayTest.test_overlays_are_scenarios_of_the_base_world_with_14_days_after) ... ok
test_rollbacks_follow_a_set_request_of_the_same_plan (testenv.test_k8s_scenarios.OverlayTest.test_rollbacks_follow_a_set_request_of_the_same_plan) ... ok
test_the_five_phenomena_are_all_present_in_after_apply (testenv.test_k8s_scenarios.OverlayTest.test_the_five_phenomena_are_all_present_in_after_apply) ... ok
test_there_are_overlays (testenv.test_k8s_scenarios.OverlayTest.test_there_are_overlays) ... ok
test_windows_fit_inside_the_period_and_have_equal_length (testenv.test_k8s_scenarios.OverlayTest.test_windows_fit_inside_the_period_and_have_equal_length) ... ok
test_base_world_series_are_unchanged_by_the_overlay_machinery (testenv.test_k8s_scenarios.ServedTest.test_base_world_series_are_unchanged_by_the_overlay_machinery) ... ok
test_overlay_is_served_and_hpa_load_shows_up_in_usage (testenv.test_k8s_scenarios.ServedTest.test_overlay_is_served_and_hpa_load_shows_up_in_usage) ... ok

----------------------------------------------------------------------
Ran 30 tests in 9.963s

OK
```

## 3. 全量单测
```
$ python3 -W ignore -m unittest discover -t . -s finops_agent -p 'test_*.py'
Ran 316 tests in 35.258s
OK
$ python3 -W ignore -m unittest discover -t . -s testenv -p 'test_*.py'
Ran 71 tests in 13.111s
OK
$ python3 tools/check_rules.py --docs
check_rules: OK
```

## 4. 基础世界不变
```
$ make k8s-eval
k8s run k8s-local (as_of 2026-10-25T23:55:00Z): 18 decisions {'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}, plans 8, ledger {'new': 8, 'unchanged': 0, 'expired': 0, 'halted': 0, 'approved': 0, 'suppressed': 0, 'held': 0}, narrative {'llm': 18}
./runs/k8s-local/k8s_decisions.json
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0}
n={'expected': 18, 'decisions': 18, 'unknown_expected': 2, 'ranged': 8, 'realizable': 2} narrative={'llm': 18}
gates: pass
$ git diff --stat a574880 -- testenv/k8s_world.yaml testenv/k8s_world.py README.md reports/index.html
(无输出：四个文件均无改动)
```
