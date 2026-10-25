# 验收证据：c0036-k8s-fresh2-freeze

## 冻结

- 冻结提交 sha：`10eefdbbd9f80b30410156593f3009eade492d0d`（提交标题 `c0036-k8s-fresh2-freeze [k/n freeze]: ...`）
- 冻结后的文件 sha256：
  - `testenv/heldout/k8s_fresh2_world.yaml` cb493ab62c2ec57b47a8bf8a88f2a10a853627c5f1c8855787cb9f5f0c0d700c
  - `testenv/heldout/k8s_fresh2_slos.yaml` 5b0f93587fee5c3707ae77aadf7f21e0bd6b3ef88e59d92610bf6c99a0a5bb5c
- 冻结后世界与目录不再修改；修复后只评一次。

## 没有运行任何评估

本 change 没有运行 agent，没有运行 `eval/score_k8s.py`，没有产生任何 verdict_accuracy、unsafe_allow 等指标。世界里的 `expect` 只是预先写下的标准答案。

## 盲写协议自查

| 规则 | 自查 |
|---|---|
| 不读第一个预注册世界的 yaml、SLO 目录 | 未读。唯一的接触是下面两条 |
| 不读 EXPERIMENTS.md、c0035 目录、0035 博客与报告 | 未读（见下） |
| 不读 `finops_agent/k8s/`、`config/k8s_policy.yaml` | 未读 |
| 不运行 agent 与评分器 | 未运行 |
| expect 来自 SRE 判断加契约明文 | 是；契约与判断冲突的 3 处标 `judgment_conflict: true`（report-builder/cpu、search-indexer/cpu、session-cache/memory） |

两处如实记录的接触：

1. `git log --oneline` 的提交标题里带有 c0035 的一行评估聚合指标（verdict_accuracy 约 0.90，unsafe_allow 为 1）。没有看到失败的是哪些 workload、哪些条件。这行是在设计 workload 之前看到的，所以只能说没有具体失败项的信息，不能说完全没有总体难度的印象。
2. 一条 shell 命令对 `reports/0035-*.html` 做了 `head` 且输出重定向到 /dev/null，内容没有被看到，也没有被使用。

## 测试

```
test_catalog_matches_world_slo_blocks (testenv.test_k8s_fresh2.Fresh2WorldTest.test_catalog_matches_world_slo_blocks) ... ok
test_expect_is_complete_and_consistent (testenv.test_k8s_fresh2.Fresh2WorldTest.test_expect_is_complete_and_consistent) ... ok
test_hard_situations_present (testenv.test_k8s_fresh2.Fresh2WorldTest.test_hard_situations_present) ... ok
test_judgment_conflicts_are_explained (testenv.test_k8s_fresh2.Fresh2WorldTest.test_judgment_conflicts_are_explained) ... ok
test_node_count_is_first_fit_decreasing (testenv.test_k8s_fresh2.Fresh2WorldTest.test_node_count_is_first_fit_decreasing)
Decreasing by memory then cpu, as pods (sidecar requests included). ... ok
test_pricing_and_nodes_differ_from_base (testenv.test_k8s_fresh2.Fresh2WorldTest.test_pricing_and_nodes_differ_from_base) ... ok
test_traps_point_at_workloads (testenv.test_k8s_fresh2.Fresh2WorldTest.test_traps_point_at_workloads) ... ok
test_workloads_are_new (testenv.test_k8s_fresh2.Fresh2WorldTest.test_workloads_are_new) ... ok
test_base_world_series_unchanged (testenv.test_k8s_fresh2.GeneratorExtensionTest.test_base_world_series_unchanged) ... ok
test_batch_inside_deadline (testenv.test_k8s_fresh2.GeneratorExtensionTest.test_batch_inside_deadline) ... ok
test_hpa_targets_memory (testenv.test_k8s_fresh2.GeneratorExtensionTest.test_hpa_targets_memory) ... ok
test_low_budget_with_burn_below_one (testenv.test_k8s_fresh2.GeneratorExtensionTest.test_low_budget_with_burn_below_one) ... ok
test_shrinking_memory_trend (testenv.test_k8s_fresh2.GeneratorExtensionTest.test_shrinking_memory_trend) ... ok
test_sidecar_is_a_separate_container (testenv.test_k8s_fresh2.GeneratorExtensionTest.test_sidecar_is_a_separate_container) ... ok
test_slo_metric_presence (testenv.test_k8s_fresh2.GeneratorExtensionTest.test_slo_metric_presence) ... ok
test_tail_gap_leaves_a_stale_week (testenv.test_k8s_fresh2.GeneratorExtensionTest.test_tail_gap_leaves_a_stale_week) ... ok
test_trend_rises_roughly_linearly (testenv.test_k8s_fresh2.GeneratorExtensionTest.test_trend_rises_roughly_linearly) ... ok
test_weekly_peak_is_under_one_percent_of_samples_and_throttles (testenv.test_k8s_fresh2.GeneratorExtensionTest.test_weekly_peak_is_under_one_percent_of_samples_and_throttles) ... ok
test_serves_fresh2_world (testenv.test_k8s_fresh2.ServedTest.test_serves_fresh2_world) ... ok
test_cli_serves_the_world_file (testenv.test_k8s_fresh2.SubprocessServerTest.test_cli_serves_the_world_file) ... ok

----------------------------------------------------------------------
Ran 20 tests in 10.845s

OK
```

全量：

```
Ran 96 tests in 27.965s
OK
```

`python3 tools/check_rules.py --docs`：`check_rules: OK`

## 基础世界不变

`GeneratorExtensionTest.test_base_world_series_unchanged` 通过：基础世界序列 sha256 仍为 c13c2fa6b443cac8e6d095e36c7f04b0c26ffc4108d191fda5ed316141a12822。生成器只新增 cpu `trend`。

## 指标服务

`python3 -m testenv.metrics_server --world testenv/heldout/k8s_fresh2_world.yaml --port 18736` 实际起服务：
- `kube_deployment_spec_replicas{workload="notify-svc"}` 返回 3.0
- `slo_burn_rate_1h{workload="invoice-api"}` 返回空（该 workload 有目录 SLO 但没有 slo 序列，符合设计）

## 世界概况

| workload | 情形 | cpu | memory |
|---|---|---|---|
| checkout-web | 日周期，易 | ALLOW | ALLOW |
| report-builder | 每周一 1 小时峰值，低于 1% 采样，request == limit，峰值节流（难） | DENY | ALLOW |
| search-indexer | cpu 线性增长（难） | ALLOW | ALLOW |
| session-cache | 内存线性下降，窗口最大值陈旧（难） | ALLOW | ALLOW |
| ingest-worker | 主容器加 sidecar 共用 workload 标签（难） | ALLOW | ALLOW |
| edge-collector | 末尾 7 天缺数据，缺口内有负载阶跃（难） | UNKNOWN | UNKNOWN |
| queue-consumer | HPA 以 memory 为指标 | ALLOW | REQUIRE_APPROVAL |
| nightly-etl | 批任务，deadline 有余量 | ALLOW | ALLOW |
| pricing-engine | 无 OOM 但峰值约 95% limit | ALLOW | DENY |
| admin-console | 没有 SLO | REQUIRE_APPROVAL | REQUIRE_APPROVAL |
| notify-svc | 燃烧率 < 1 但剩余预算约 10% | DENY | DENY |
| invoice-api | 目录有 SLO，缺 slo_* 指标 | REQUIRE_APPROVAL | REQUIRE_APPROVAL |

节点类型 c6i.2xlarge，单价 0.34 美元/小时，allocatable 7.6 核 / 13.9 GiB，8 个节点（min 5，max 14）。没有写 `realizable_usd_month`：first-fit decreasing 的排序键不同，节点数可差 1，写下去会变成对实现细节的预注册。
