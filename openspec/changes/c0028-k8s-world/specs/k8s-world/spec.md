# k8s-world：合成 K8s 世界

## ADDED Requirements

### Requirement: 世界文件满足契约 schema

`testenv/k8s_world.yaml` 含 21 天周期、`nodes`、`pricing`（0.192 / 0.0316 / 0.0041）、至少 9 个 workload 和 `traps`；每个 workload 的 `expect` 给出 cpu 与 memory，`request_range` 由推荐器规则（cpu：p99 × 1.3 向上取整 0.05；memory：max ws × 1.2 向上取整 64Mi）推导，宽度约 ±10%。

#### Scenario: 世界自洽
- **WHEN** 加载世界文件
- **THEN** 两个资源都有 expect，lo ≤ hi，lo 小于当前 request（NOOP 与 mem-near-oom 的 memory 除外），范围包含由服务数据算出的推荐值

### Requirement: 九个 workload 各对应一个判定分支

api-steady、api-tidal、batch-nightly、mem-near-oom、burning-slo、new-service、no-slo、hpa-managed、packing-trap 的形状与 expect 如 tasks.md 所列。

#### Scenario: 装箱事实成立
- **WHEN** 用 first-fit decreasing 对全部 workload 的 requests × replicas 装入 3.8 core / 14.5 GiB 的节点
- **THEN** 基线 6 个节点；只应用 api-steady 的 cpu 推荐时降到 5 个；只应用 packing-trap 的 cpu 推荐时仍是 6 个（四种排序键结果一致）

### Requirement: metrics server 提供 k8s 世界

`python3 -m testenv.metrics_server --world k8s` 提供契约表里的每个指标和标签，外加 `container_cpu_usage_cores`；默认世界不变。`data_days` 小于周期的 workload 只在末尾那几天有样本。

#### Scenario: new-service 只有 5 天
- **WHEN** 以 1 天步长对 new-service 做 query_range
- **THEN** 每条序列 5 个点，其他 workload 21 个点

### Requirement: 漂移场景

`testenv/k8s_scenarios/drift_day3.yaml` 把周期延长 2 天并让 api-steady 最后一天负载 +60%；`--as-of` 或 `load(as_of=)` 截断世界。

#### Scenario: 截断不改历史
- **WHEN** 同一个世界用不同 as_of 生成
- **THEN** 共有时间段内的样本逐点相同（受 data_days 影响的 new-service 除外）
