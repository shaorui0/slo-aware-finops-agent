## Why

c0027 把主线收窄到 K8s CPU/Memory requests，并钉死了世界文件 schema 和指标名。后面的 change（推荐器、策略引擎、执行、评估）都要有一份「标准答案已知」的数据来跑，否则只能互相用假数据对着写。本 change 先把这份合成世界做出来，并让 test-metrics-server 以 `--world k8s` 的方式把它当成 Prometheus 提供出去。

## What Changes

- 新增 `testenv/k8s_world.yaml`：21 天、6 个节点、9 个 workload，每个 workload 的 cpu 与 memory 各有 `expect`（verdict、request_range），另有 `traps` 清单。
- 新增 `testenv/k8s_world.py`：确定性生成器（每条序列一个种子随机数，每步抽数固定，延长周期不改变历史）。
- `testenv/metrics_server.py` 新增 `--world k8s` 与 `--as-of`：提供契约表里的全部指标；默认 AWS 世界的行为不变。
- 新增 `testenv/k8s_scenarios/drift_day3.yaml`：第 3 天场景，周期延长 2 天，api-steady 最后一天负载 +60%。
- 新增 `testenv/test_k8s_world.py`：自洽、指标、形状、推荐器推导、装箱事实（自带 first-fit decreasing）、漂移。

## 契约变更请求（主 agent 合并时处理）

1. **新增指标 `container_cpu_usage_cores`**（gauge，每 pod 5 分钟平均核数）。现有 PromQL 子集不支持子查询 `rate(...[5m])[14d:5m]`，`quantile_over_time` 只接受选择器，所以 p99 要用 `quantile_over_time(0.99, container_cpu_usage_cores[14d])`。数值等于 `rate(container_cpu_usage_seconds_total[10m])`，测试里有对照。
2. **rate 的窗口至少写 `[10m]`**：子集的区间是左开右闭，`[5m]` 在 5 分钟网格上只含一个点，rate 与 increase 返回空。这是现有行为，没有改。
3. **mem-near-oom 的 memory 无法在「推荐值 < 当前值」的前提下成为 DENY。** 推荐值是 max ws × 1.2；若 max ws ≥ 0.9 × limit 且 limit ≥ request，则推荐值 ≥ 1.08 × request，判定规则第 1 条（NOOP）会先触发，B 的 OOM 守卫永远走不到。建议把规则改成：NOOP 仅在 B/C 均未 FAIL 时才命中，或者把 memory 的 OOM 检查放在 NOOP 之前。世界里仍按任务要求写 `expect.memory.verdict: DENY`，`request_range` 写成推荐器会给的值（高于当前），自洽测试对这一条单独豁免。
4. **batch-nightly 的「2 小时 100%」按单线程满载解释**：窗口内用 1.0 核（request 2.0 的 50%）。若用 request 的 100%，推荐值（×1.3）高于当前值，同样会先落入 NOOP，拿不到 DENY（C）。
5. **api-tidal 的峰值用窄尖峰**：日峰值 1.8 核（request 的 90%）只持续 2 个采样点，p99 落在宽峰肩部（约 1.23 核），推荐值 1.65 < 2.0，才会出现「推荐器想降、B（throttling）要拦」的陷阱。

## 人工 gate 与回滚

只写测试夹具和测试服务，没有 AWS 或 kubectl 操作；回滚是 revert 该分支。

## Capabilities

### New Capabilities
- `k8s-world`：合成 K8s 世界及其指标服务。

### Modified Capabilities
（无；契约变更只是请求，见上）

## Impact

只改 `testenv/` 与本 change 目录。默认世界与现有测试不变。
