## ADDED Requirements

### Requirement: 账单口径与节点数来源

核实的账单是 `节点小时 x node_hour_usd`。窗口内每个小时的节点数由对该小时全部 pod 的 requests 做 first-fit decreasing 得出（`finops_agent/k8s/packing.py` 的 `pack`，节点 allocatable 与 `nodes.min` 取自世界文件）。节点数不得写进场景文件。HPA 工作负载的副本数为 `clamp(ceil(用量 / (target_utilization% x cpu request)), min, max)`，用量来自世界生成器。BEFORE 与 AFTER 两个窗口小时数相同；窗口之间留出时间让 HPA 与回滚落定。

#### Scenario: 节点数跟着 requests 走
- **WHEN** 只应用 api-steady 的 cpu 变更（1 到 0.25 core，3 个副本）
- **THEN** AFTER 窗口每个小时的节点数比 BEFORE 少 1，节点小时少 168

### Requirement: 五类原因与精确闭合

账单变化（AFTER 账单 - BEFORE 账单，$/月，按 730 小时折算）沿一条状态链分解，相邻两环只差一件事：

| 原因 | 含义 | 状态链上的一步 |
|---|---|---|
| (a) | 变更本身让节点少了 | 在 B 上按应用顺序逐条加入已应用变更（HPA 副本按原 request） |
| (b) | 与变更无关的负载或副本增长 | BEFORE 到 B：AFTER 小时、原 requests、含无关增长 |
| (c) | 变更触发的 HPA 扩容 | 让 HPA 按新 cpu request 重新推副本数 |
| (d) | 回滚，变更没有保住 | 把被回滚的变更换回原 request |
| (e) | 装箱没兑现 | request 降了但节点数没变：金额 0，仍记一条带解释的原因 |

各原因金额之和必须等于账单变化，差不超过 1e-6，否则运行失败（断言）。单条变更的 `realized_usd_month = -(a + d + c)` 中该条的份额（控制组 b 已扣除）；单条 `predicted_usd_month` 取决策的 `realizable_usd_month`；`ratio = realized / predicted`，预测为 0 时为 null。实际与预测的差拆成 (e)(b)(d)(c) 四项，各项之和等于差额。同一阶段内按应用顺序归集：每条变更只算它加入时多空出的节点。

#### Scenario: 分解闭合
- **WHEN** 对任意应用顺序运行核实
- **THEN** 各原因之和与账单变化的差小于 1e-6，每条变更的各偏差之和等于 实际 - 预测

#### Scenario: packing-trap
- **WHEN** packing-trap 的 cpu 变更已应用，其内存 request 仍钉住节点
- **THEN** 实际为 0，偏差为 (e)，解释里用白话写明账面释放多少、为什么节点数没变、账单不变

#### Scenario: 回滚
- **WHEN** 一条变更在 AFTER 窗口前被回滚
- **THEN** 实际为 0，偏差含 (d)

### Requirement: 输出

`runs/<id>/k8s_verify.json` 严格是 c0032 契约形状（逐条 `plan_id, decision_id, predicted_usd_month, realized_usd_month, ratio, nodes_before, nodes_after, deviations[{cause, usd, explanation}], slo_ok, window`），不加字段。`nodes_before`、`nodes_after` 是集群整体前后（各窗口小时数最多的节点数）。`slo_ok` 在 workload 没有 SLO 信号时为 null（UNKNOWN 不当通过）。旁边写 `k8s_verify_detail.json`：窗口、账单、原因表与闭合检查、状态链节点小时、每条变更的链上份额与账面释放。`docs/K8S_SAVINGS_VERIFICATION.md` 由数据生成（中文、表格），含方法与局限，不手改。

### Requirement: 场景覆盖层

`testenv/k8s_scenarios/after_*.yaml` 是 `scenario.base: testenv/k8s_world.yaml` 的覆盖层，`cluster.period.end` 为应用后 14 天，另含 `verify: {before, after}` 窗口。新增事件类型（写在 `add_events` 里，世界生成器与 metrics_server 忽略）：

| 类型 | 字段 | 含义 |
|---|---|---|
| `set_request` | at, plan_id, resource, request | 一条计划被应用 |
| `rollback` | at, plan_id | 该计划被回滚，request 回到原值 |
| `scale` | at, replicas | 无关的人工扩容 |
| `bump_request` | at, resource, request | 无关的 request 上调 |

`load` 事件沿用，会进入用量并驱动 HPA。`testenv/k8s_world.yaml` 与 `testenv/k8s_world.py` 不改，`make k8s-eval` 的数字不变。`after_apply.yaml` 含：api-steady 干净变更、packing-trap、人工批准后第二天应用的 hpa-managed、被回滚的 no-slo、窗口中途的无关副本增长；`after_clean.yaml` 只有 api-steady 与 packing-trap，作对照。

#### Scenario: 对照不造偏差
- **WHEN** 核实 `after_clean.yaml`
- **THEN** api-steady 实现率 100% 且无偏差；packing-trap 实际 0

### Requirement: 一致性检查

场景里应用的每条计划必须在 `k8s_plans.json` 里，对应决策必须在 `k8s_decisions.json` 里，workload 与资源一致，场景里的目标 request 等于计划 patch 里的值；不一致即报错退出，不降级。
