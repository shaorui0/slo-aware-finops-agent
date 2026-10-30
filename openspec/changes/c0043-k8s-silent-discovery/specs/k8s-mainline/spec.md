## MODIFIED Requirements

### Requirement: 取数（workload 发现）

workload 发现 MUST 用窗口口径：`max by (namespace, workload, team)(max_over_time(kube_deployment_spec_replicas[<window_days>d]))` 在 as_of 求值。「窗口」即 `config/k8s_policy.yaml` 的 `window_days`（与用量取数同一窗口）。窗口内出现过的 workload 每个资源各产出一条决策；不得因 as_of 时刻没有样本而被丢弃。

#### Scenario: 最近没有数据的 workload
- **WHEN** 某 workload 在窗口前段有指标，as_of 前最后 `stale_days` 天没有
- **THEN** decisions 里有它的 cpu 与 memory 两条决策，不得缺席

### Requirement: 策略引擎（条件 A 的近期数据子项）

条件 A 原文（有效天数 ≥ `min_days`、覆盖率 ≥ `min_coverage`；不足为 UNKNOWN）增加一个子项：

- **「近期有数据」**的定义：该资源的逐小时用量序列（同一窗口，1h 步长）中，最后一个样本距 as_of 不超过 `stale_days` 天。
- 不满足（最后样本更早，或窗口内没有样本）则 A = UNKNOWN：`why` 含「最近 N 天无数据」（N = `stale_days`；有旧样本时同时写最后样本距今天数）；`unknown_needs` 含「需要最近 N 天的 <CPU|内存> 使用数据（…无数据）」。
- 判定顺序不变。A 不是 PASS，故 verdict = UNKNOWN（除非条件 C = FAIL → DENY），**永不为 ALLOW / REQUIRE_APPROVAL**。没有用量数字的决策，`usage` 里对应字段为 null，模板叙述与日报显示「未知」/「—」，不出现 None。
- 有数据的 workload：A 的 `why`、证据、decision JSON 与此前逐字节相同（新子项只在触发时写文字，不加证据条目）。
- 阈值 `stale_days`（默认 3）在 `config/k8s_policy.yaml`。

#### Scenario: 窗口前段有数据、最近无数据
- **WHEN** 最后样本距 as_of 5 天，`stale_days` = 3
- **THEN** A = UNKNOWN，`unknown_needs` 含「最近 3 天无数据」，verdict = UNKNOWN

#### Scenario: 数据新鲜
- **WHEN** 最后样本距 as_of 不超过 `stale_days`
- **THEN** 子项不触发，A 的结果与此前相同

### Requirement: 台账（过期 workload）

台账规则不变：open 提案对应的 key 本轮 verdict 不再是 ALLOW / REQUIRE_APPROVAL 时过期，备注「本轮 verdict 为 UNKNOWN，不再是可提案」。workload 因数据过期变成 UNKNOWN 属于此路径，不再是「workload 已不在指标里」。
