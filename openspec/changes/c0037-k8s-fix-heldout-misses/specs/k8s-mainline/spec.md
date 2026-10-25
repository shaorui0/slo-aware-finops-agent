## MODIFIED Requirements

### Requirement: 策略引擎（条件 B 的趋势子项）

条件 B 原文（cpu：新 request ≥ p99 × (1+margin) 且高峰 throttling < `max_throttle`；memory：新 request ≥ 峰值 working set × (1+margin)、无 OOMKilled、峰值 < `oom_guard` × limit；集群无 Pending）增加一个子项：

- **趋势子项。** 对 `trend_resources` 里的资源（默认只有 memory），取窗口内每天的最大使用量（1d 步长），做最小二乘线性回归，得到斜率（单位/天）、R²、拟合线末端值。预测峰值 = 拟合末端值 + 斜率 × `trend_horizon_days`。
- 触发条件：点数 ≥ `trend_min_points`，斜率 > 0，R² ≥ `trend_min_r2`，窗口内拟合涨幅（斜率 × 窗口天数）≥ `trend_min_rise` × 拟合起点。不触发的序列不影响 B。
- 触发后，若 推荐 request < 预测峰值 × (1+margin)，则 B = FAIL（verdict DENY），`why` 写中文：每日峰值的斜率、R²、外推天数与预测峰值、推荐值，并提示先查泄漏或增长。
- 证据：趋势触发时，斜率与外推峰值各一条（`trend_slope`、`trend_proj`），B 的 `evidence` 引用它们。没有触发的序列不加证据条目（与批任务的 job 行只在批任务上出现同理），所以基础世界里的证据列表只因按容器过滤的选择器而变。
- 阈值（`trend_horizon_days`、`trend_min_points`、`trend_min_r2`、`trend_min_rise`、`trend_resources`）在 `config/k8s_policy.yaml`。
- 判定顺序不变：B = FAIL → DENY。趋势子项与其他 B 子项一样按当前状态评估，推荐值不低于当前值时同样适用（理由写「配额不足，不能降」）。

#### Scenario: 持续爬升
- **WHEN** 内存每日最大值线性上升，预测峰值 × (1+margin) 高于推荐 request
- **THEN** B 为 FAIL，verdict 为 DENY，`why` 含斜率与预测峰值

#### Scenario: 平稳
- **WHEN** 每日最大值无趋势（含昼夜噪声）
- **THEN** 趋势子项不触发，B 的结果与之前相同

#### Scenario: 下降
- **WHEN** 每日最大值下降
- **THEN** 趋势子项不触发，不产生 FAIL

#### Scenario: cpu 默认不适用
- **WHEN** `trend_resources` 只含 memory，cpu 每日最大值上升
- **THEN** 不产生趋势证据，B 不因趋势失败

### Requirement: 取数（按容器过滤）

用量（p50/p95/p99/max 与逐小时序列）、CFS throttling、request、limit 的选择器 MUST 在已知容器名时加 `container="<name>"`。容器名取自同一轮 `kube_deployment_spec_replicas` 的 `container` 标签；该标签缺失时沿用不带 `container` 的选择器。

#### Scenario: sidecar
- **WHEN** 同一 workload 的 pod 里有一个用量更大的 sidecar 容器
- **THEN** 决策里的 usage、request、limit 只来自应用容器

## MODIFIED Requirements (k8s-pipeline)

### Requirement: 定价输入

`--nodes-from <yaml>` 在读取 `nodes.min` 与 `nodes.max` 之外，也读取 `pricing` 块（如 `node_hour_usd`），按键覆盖 `config/k8s_policy.yaml` 的默认牌价，用于节省金额的装箱估算。没有 `pricing` 块时用默认牌价，行为与 c0031 相同。

#### Scenario: 世界有定价
- **WHEN** 世界文件的 `pricing.node_hour_usd` 是 0.384
- **THEN** `realizable_usd_month` 按 0.384 计

#### Scenario: 世界没有定价
- **WHEN** 世界文件没有 `pricing`
- **THEN** 用默认 0.192
