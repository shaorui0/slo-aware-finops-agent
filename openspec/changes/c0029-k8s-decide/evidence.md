# c0029 验收证据

## 全量单测（含原有测试）
```
$ python3 -W ignore -m unittest discover -t . -s finops_agent -p 'test_*.py'
Ran 211 tests in 21.747s
OK
```

## K2 验证命令（finops_agent/k8s）
```
$ python3 -W ignore -m unittest discover -t . -s finops_agent/k8s -p 'test_*.py' -v
test_as_of_defaults_to_source_time_and_is_fixed (finops_agent.k8s.test_collect.CollectTest.test_as_of_defaults_to_source_time_and_is_fixed) ... ok
test_cluster_facts (finops_agent.k8s.test_collect.CollectTest.test_cluster_facts) ... ok
test_cpu_facts (finops_agent.k8s.test_collect.CollectTest.test_cpu_facts) ... ok
test_every_number_has_evidence (finops_agent.k8s.test_collect.CollectTest.test_every_number_has_evidence) ... ok
test_hpa_oom_and_job_facts (finops_agent.k8s.test_collect.CollectTest.test_hpa_oom_and_job_facts) ... ok
test_memory_facts_in_gib (finops_agent.k8s.test_collect.CollectTest.test_memory_facts_in_gib) ... ok
test_no_data_means_error_not_guess (finops_agent.k8s.test_collect.CollectTest.test_no_data_means_error_not_guess) ... ok
test_nothing_unsupported_in_queries (finops_agent.k8s.test_collect.CollectTest.test_nothing_unsupported_in_queries) ... ok
test_one_facts_per_workload_and_resource (finops_agent.k8s.test_collect.CollectTest.test_one_facts_per_workload_and_resource) ... ok
test_queries_only_use_contract_metric_names (finops_agent.k8s.test_collect.CollectTest.test_queries_only_use_contract_metric_names) ... ok
test_short_history_valid_days_and_coverage (finops_agent.k8s.test_collect.CollectTest.test_short_history_valid_days_and_coverage) ... ok
test_trace_records_every_call_and_errors (finops_agent.k8s.test_collect.CollectTest.test_trace_records_every_call_and_errors) ... ok
test_collect_feeds_decide (finops_agent.k8s.test_collect.EndToEndTest.test_collect_feeds_decide) ... ok
test_default_urlopen_transport_against_local_http (finops_agent.k8s.test_collect.HttpTransportTest.test_default_urlopen_transport_against_local_http) ... ok
test_both_resources_must_fit (finops_agent.k8s.test_packing.PackTest.test_both_resources_must_fit) ... ok
test_deterministic_regardless_of_input_order (finops_agent.k8s.test_packing.PackTest.test_deterministic_regardless_of_input_order) ... ok
test_ffd_beats_naive_order (finops_agent.k8s.test_packing.PackTest.test_ffd_beats_naive_order) ... ok
test_oversize_pod_gets_own_node_and_min_nodes (finops_agent.k8s.test_packing.PackTest.test_oversize_pod_gets_own_node_and_min_nodes) ... ok
test_free_after (finops_agent.k8s.test_packing.SavingsTest.test_free_after) ... ok
test_memory_unit_price (finops_agent.k8s.test_packing.SavingsTest.test_memory_unit_price) ... ok
test_node_count_drops_gives_realizable (finops_agent.k8s.test_packing.SavingsTest.test_node_count_drops_gives_realizable) ... ok
test_packing_trap_realizable_zero_but_request_value_positive (finops_agent.k8s.test_packing.SavingsTest.test_packing_trap_realizable_zero_but_request_value_positive) ... ok
test_chinese_why (finops_agent.k8s.test_policy.OutputTest.test_chinese_why) ... ok
test_contract_shape (finops_agent.k8s.test_policy.OutputTest.test_contract_shape) ... ok
test_determinism_same_input_identical_json (finops_agent.k8s.test_policy.OutputTest.test_determinism_same_input_identical_json) ... ok
test_inputs_not_mutated (finops_agent.k8s.test_policy.OutputTest.test_inputs_not_mutated) ... ok
test_policy_has_all_thresholds (finops_agent.k8s.test_policy.OutputTest.test_policy_has_all_thresholds) ... ok
test_write_decisions (finops_agent.k8s.test_policy.OutputTest.test_write_decisions) ... ok
test_1_noop_when_recommendation_not_lower (finops_agent.k8s.test_policy.VerdictOrderTest.test_1_noop_when_recommendation_not_lower) ... ok
test_2_c_fail_beats_a_unknown (finops_agent.k8s.test_policy.VerdictOrderTest.test_2_c_fail_beats_a_unknown) ... ok
test_3_a_unknown_beats_b_fail (finops_agent.k8s.test_policy.VerdictOrderTest.test_3_a_unknown_beats_b_fail) ... ok
test_3_a_unknown_gives_unknown_with_needs (finops_agent.k8s.test_policy.VerdictOrderTest.test_3_a_unknown_gives_unknown_with_needs) ... ok
test_3_noop_but_current_state_unsafe_is_deny (finops_agent.k8s.test_policy.VerdictOrderTest.test_3_noop_but_current_state_unsafe_is_deny) ... ok
test_4_b_fail_pending_pods (finops_agent.k8s.test_policy.VerdictOrderTest.test_4_b_fail_pending_pods) ... ok
test_4_b_fail_throttling (finops_agent.k8s.test_policy.VerdictOrderTest.test_4_b_fail_throttling) ... ok
test_4_memory_oom_denied_while_cpu_allowed_on_same_workload (finops_agent.k8s.test_policy.VerdictOrderTest.test_4_memory_oom_denied_while_cpu_allowed_on_same_workload) ... ok
test_4_memory_peak_near_limit_guard (finops_agent.k8s.test_policy.VerdictOrderTest.test_4_memory_peak_near_limit_guard) ... ok
test_5_hpa_coupling_requires_approval (finops_agent.k8s.test_policy.VerdictOrderTest.test_5_hpa_coupling_requires_approval) ... ok
test_5_unknown_never_allow (finops_agent.k8s.test_policy.VerdictOrderTest.test_5_unknown_never_allow) ... ok
test_5_unknown_pending_and_missing_pods (finops_agent.k8s.test_policy.VerdictOrderTest.test_5_unknown_pending_and_missing_pods) ... ok
test_6_allow_packing_trap_realizable_zero (finops_agent.k8s.test_policy.VerdictOrderTest.test_6_allow_packing_trap_realizable_zero) ... ok
test_6_realizable_positive_needs_rollback_capacity (finops_agent.k8s.test_policy.VerdictOrderTest.test_6_realizable_positive_needs_rollback_capacity) ... ok
test_baseline_is_allow (finops_agent.k8s.test_policy.VerdictOrderTest.test_baseline_is_allow) ... ok
test_c_fail_low_budget_and_batch_deadline (finops_agent.k8s.test_policy.VerdictOrderTest.test_c_fail_low_budget_and_batch_deadline) ... ok
test_verdict_of_table (finops_agent.k8s.test_policy.VerdictOrderTest.test_verdict_of_table) ... ok
test_cpu_formula_ceil_to_step (finops_agent.k8s.test_recommend.RecommendTest.test_cpu_formula_ceil_to_step) ... ok
test_cpu_method_string (finops_agent.k8s.test_recommend.RecommendTest.test_cpu_method_string) ... ok
test_helpers (finops_agent.k8s.test_recommend.RecommendTest.test_helpers) ... ok
test_memory_formula_gib_and_step (finops_agent.k8s.test_recommend.RecommendTest.test_memory_formula_gib_and_step) ... ok
test_missing_usage_gives_none (finops_agent.k8s.test_recommend.RecommendTest.test_missing_usage_gives_none) ... ok
test_noop_when_not_below_current (finops_agent.k8s.test_recommend.RecommendTest.test_noop_when_not_below_current) ... ok

----------------------------------------------------------------------
Ran 51 tests in 0.553s

OK
```

## check_rules --docs
```
$ python3 tools/check_rules.py --docs
check_rules: OK
```
