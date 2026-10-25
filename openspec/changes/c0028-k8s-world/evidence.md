# c0028 验收证据

## 单元测试（testenv 全部，含原有 AWS 世界测试）
```
run r3: 6 findings, 4 leads, invariants pass
.............
----------------------------------------------------------------------
Ran 58 tests in 10.754s

OK
```

## test_k8s_world 明细
```
test_as_of_truncates_without_changing_history ... ok
test_scenario_extends_period_and_drifts_api_steady ... ok
test_contract_labels ... ok
test_default_world_metadata_not_polluted ... ok
test_every_contract_metric_has_series ... ok
test_gauge_equals_counter_rate ... ok
test_nested_subquery_unsupported_so_gauge_exists
The PromQL subset has no `expr[14d:5m]` subquery; container_cpu_usage_cores is the contract addition. ... ok
test_new_service_has_five_days ... ok
test_optional_metrics_only_where_defined ... ok
test_allow_changes_keep_rollback_headroom
D-style: after applying one change, free allocatable still covers the freed request x replicas. ... ok
test_api_steady_drops_a_node ... ok
test_baseline_node_count_matches_world ... ok
test_packing_trap_saves_no_node ... ok
test_cluster_header ... ok
test_no_slo_workload_has_no_slo_block ... ok
test_required_workloads_and_expect ... ok
test_traps_point_to_workloads ... ok
test_api_steady_flat_low ... ok
test_api_tidal_peak_and_throttle ... ok
test_batch_window_and_deadline ... ok
test_burning_slo ... ok
test_mem_near_oom ... ok
test_request_ranges_derive_from_recommender_rule ... ok

----------------------------------------------------------------------
Ran 23 tests in 5.659s

OK
```

## check_rules --docs
```
check_rules: OK
```

## 真实起服务：--world k8s --port 19093
```
$ curl /api/v1/series?match[]=kube_pod_container_resource_requests{workload="packing-trap"} | head
{'__name__': 'kube_pod_container_resource_requests', 'namespace': 'shop', 'workload': 'packing-trap', 'container': 'app', 'pod': 'packing-trap-6f7c9d-a', 'resource': 'cpu', 'unit': 'core'}
{'__name__': 'kube_pod_container_resource_requests', 'namespace': 'shop', 'workload': 'packing-trap', 'container': 'app', 'pod': 'packing-trap-6f7c9d-a', 'resource': 'memory', 'unit': 'byte'}
{'__name__': 'kube_pod_container_resource_requests', 'namespace': 'shop', 'workload': 'packing-trap', 'container': 'app', 'pod': 'packing-trap-6f7c9d-b', 'resource': 'cpu', 'unit': 'core'}
{'__name__': 'kube_pod_container_resource_requests', 'namespace': 'shop', 'workload': 'packing-trap', 'container': 'app', 'pod': 'packing-trap-6f7c9d-b', 'resource': 'memory', 'unit': 'byte'}
$ query quantile_over_time(0.99, container_cpu_usage_cores{workload="api-steady"}[14d])
api-steady-6f7c9d-a 0.15849
api-steady-6f7c9d-b 0.16178
api-steady-6f7c9d-c 0.16494
$ query nested subquery (unsupported)
{"status": "error", "errorType": "bad_data", "error": "unsupported expression: 'rate(container_cpu_usage_seconds_total[5m])[14d:5m]'"}
$ query slo_burn_rate_1h / budget for burning-slo
{'__name__': 'slo_burn_rate_1h', 'namespace': 'shop', 'workload': 'burning-slo', 'container': 'app'} 2.3861
{'__name__': 'slo_error_budget_remaining_ratio', 'namespace': 'shop', 'workload': 'burning-slo', 'container': 'app'} 0.125
$ query job_last_duration_seconds
{'__name__': 'job_last_duration_seconds', 'namespace': 'data', 'workload': 'batch-nightly', 'container': 'job'} 6884.8
```

## 推荐器推导（测试内由服务数据算出）与 expect 区间
```
 326 series 0.3 s
 api-steady p99 [0.158, 0.162, 0.165] rec_cpu 0.25 maxws [0.936, 0.936, 0.936] rec_mem 1.125 avgcpu 0.147
 api-tidal p99 [1.222, 1.243] rec_cpu 1.65 maxws [5.151, 5.151] rec_mem 6.1875 avgcpu 0.291
 batch-nightly p99 [1.024] rec_cpu 1.35 maxws [1.224] rec_mem 1.5 avgcpu 0.093
 mem-near-oom p99 [0.043, 0.044] rec_cpu 0.1 maxws [1.895, 1.99] rec_mem 2.4375 avgcpu 0.04
 burning-slo p99 [0.107, 0.109] rec_cpu 0.15 maxws [0.624, 0.624] rec_mem 0.75 avgcpu 0.099
 new-service p99 [0.053, 0.054] rec_cpu 0.1 maxws [0.312, 0.312] rec_mem 0.375 avgcpu 0.05
 no-slo p99 [0.128, 0.131] rec_cpu 0.2 maxws [0.832, 0.832] rec_mem 1.0 avgcpu 0.119
 hpa-managed p99 [0.32, 0.327] rec_cpu 0.45 maxws [0.936, 0.936] rec_mem 1.125 avgcpu 0.297
 packing-trap p99 [0.128, 0.131] rec_cpu 0.2 maxws [6.767, 6.767] rec_mem 8.125 avgcpu 0.119
```

## 装箱事实（测试内 FFD，四种排序键一致）
```
api-steady cpu 推荐后节点数 {'cpu': 5, 'mem': 5, 'norm': 5, 'sum': 5} 基线 6
packing-trap cpu 推荐后节点数 {'cpu': 6, 'mem': 6, 'norm': 6, 'sum': 6} 基线 6
```
