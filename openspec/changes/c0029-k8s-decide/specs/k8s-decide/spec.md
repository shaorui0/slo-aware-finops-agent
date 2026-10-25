# k8s-decide：决策内核的实现约定

本文件补充 c0027 契约中没有写死的实现细节。契约本身不改。

## ADDED Requirements

### Requirement: 取数只用契约指标，名字集中在常量

`collect.py` 里每个指标名只出现一次，是模块级常量。契约之外新增的名字（见下）单独列出，便于世界文件对齐。

- 每 pod CPU 使用量：`container_cpu_usage_cores`（gauge，单位 core）。原因：test-metrics-server 的 PromQL 子集不支持子查询，`quantile_over_time(0.99, rate(x[5m])[14d:5m])` 不可用，所以 p99 需要一个已经是 rate 的 gauge。
- 批任务 deadline：`job_last_duration_seconds` 序列上的标签 `deadline_seconds`。
- HPA 指标类型：`kube_horizontalpodautoscaler_spec_target_metric` 上的标签 `metric_name`（kube-state-metrics 的标准标签）。
- team：`kube_deployment_spec_replicas` 上的标签 `team`，缺失时为空串。

#### Scenario: 每个数字都有证据
- **WHEN** collect 为一个 (workload, resource) 建 facts
- **THEN** 其中用到的每个数字都对应一条 `{id, query, window, value, source}`，id 在该 facts 内唯一

### Requirement: 窗口、有效天数与覆盖率

窗口取 `window_days`（14）天，截止在 `as_of`（缺省取指标源的最新时间点，并在整次取数中固定）。对该 workload 的使用量指标做步长 1 小时的 range query：有数据的小时数除以 `window_days × 24` 为 `coverage`（上限 1）；某个 UTC 日里有数据的小时数 ≥ `valid_day_min_hours` 才算有效天。峰值时段是按小时均值 ≥ 该日内最大小时均值 × 0.9 的 UTC 小时。

#### Scenario: 只有 5 天数据
- **WHEN** 一个 workload 只有最近 5 天的指标
- **THEN** valid_days 为 5，条件 A 为 UNKNOWN，`unknown_needs` 含「需要至少 14 天的 CPU 使用数据（当前 5 天）」

### Requirement: 条件的缺失处理

取不到的输入让对应条件为 UNKNOWN，不为 FAIL，也不为 PASS。

- B（cpu）缺 throttling 序列，或缺 p99：UNKNOWN。B（memory）缺 working set：UNKNOWN。集群 Pending 数取不到：UNKNOWN。
- C：既没有 SLO 序列也没有带 deadline 的批任务序列时为 UNKNOWN。
- D：缺当前 request、limit 之外的快照字段（request、副本数）时为 UNKNOWN；容量不足为 FAIL。
- 没有设置 memory limit 时，「峰值 < oom_guard × limit」不适用，按 PASS 处理并在 why 里写明。

### Requirement: 装箱模拟

输入是集群里所有 pod 的 requests（cpu core、memory GiB）与单节点 allocatable。排序键是 `max(cpu/节点cpu, mem/节点mem)` 降序，同分按 `(namespace, pod)` 升序；逐个放进第一个两种资源都放得下的节点。单个 pod 超过节点 allocatable 时独占一个节点。节点数不低于 `min_nodes`（缺省 1）。`nodes_before` 与 `nodes_after` 都是模拟值，不是实际节点数。

#### Scenario: 装箱陷阱
- **WHEN** 一个 workload 降了 request，但降完后各节点仍然放不下少一个节点的总量
- **THEN** `nodes_after == nodes_before`，`realizable_usd_month == 0`，`request_value_usd_month > 0`

### Requirement: 条件 D 的容量判据

缩容后（只应用这一条变更）集群空闲 allocatable = `nodes_after × allocatable − Σ 缩容后 requests`，按被改的那种资源算。空闲 ≥ Δrequest × 副本数为 PASS。

### Requirement: 价格来源

节点小时价与 CPU/内存单价不在指标里。`decide` 的 `cluster` 参数携带 `pricing`；collect 默认取 `config/k8s_policy.yaml` 的 `pricing`，调用方可以用世界文件的 `pricing` 覆盖。

### Requirement: 确定性

同样的 facts、cluster、policy 输入，`decide` 输出的 JSON 字符串逐字节相同。decision 按 `id` 排序；浮点数统一四舍五入到 6 位。

### Requirement: 判定顺序（主 agent 修订，覆盖 c0027 原文）

1. C = FAIL → DENY；2. A ≠ PASS → UNKNOWN；3. B = FAIL 或 D = FAIL → DENY；4. 推荐值不低于当前值 → NOOP；5. 任一条件 UNKNOWN 或 HPA 联动 → REQUIRE_APPROVAL；6. ALLOW。B 的风险判据（窗口内 OOMKilled、峰值 working set ≥ oom_guard × limit、高峰 throttling ≥ max_throttle）在当前状态上评估，即使没有降配建议。

#### Scenario: 欠配的 workload
- **WHEN** 推荐值 ≥ 当前值，且窗口内有 OOMKilled
- **THEN** verdict 为 DENY，reasons 写明偏低、不应降配，并给出推荐值供人工考虑上调
