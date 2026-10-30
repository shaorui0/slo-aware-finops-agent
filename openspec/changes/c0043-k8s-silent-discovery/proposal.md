## Why

第二个预注册世界（c0036/c0038，`eval/EXPERIMENTS.md` §9.3）暴露了一个沉默缺陷：`finops_agent/k8s/collect.py` 用 as_of 时刻的**瞬时查询**发现 workload。窗口前段有数据、最近几天没有数据的 workload 在发现阶段就消失了：没有决策，没有 UNKNOWN，日报里也找不到。没有放行，但沉默了，直接违反项目规则「缺数据必须显式，不能沉默」。

## What Changes

1. **发现改成窗口口径。** `workloads()` 改用 `max by (namespace, workload, team)(max_over_time(kube_deployment_spec_replicas[<window_days>d]))`：窗口内出现过的 workload 都进入决策。
2. **条件 A 增加「近期有数据」子项。** 取每种资源的逐小时用量序列，最后一个样本距 as_of 超过 `stale_days`（新键，`config/k8s_policy.yaml`，默认 3）天，或窗口内没有样本，则 A = UNKNOWN，`why` 与 `unknown_needs` 写明「最近 N 天无数据」。A 不是 PASS，所以 verdict 一定是 UNKNOWN（判定顺序不变），永远不会是 ALLOW / REQUIRE_APPROVAL。
3. **台账不改代码。** 过期 workload 现在以 UNKNOWN 出现在 decisions 里，已有的 open 提案按「本轮 verdict 为 UNKNOWN，不再是可提案」过期；加单测钉住。
4. **不改 LLM 提示。** 新增内容只出现在过期 workload 的条件 A 文本里；有数据的 workload 的 decision JSON、证据列表、提示词逐字节不变，盒带不需重录。
5. 日报：UNKNOWN 表本来就列 `unknown_needs`，预期不用改 `report.py`；用单测 + 渲染样例确认。

## 不做

- 窗口内**一个样本都没有**的 workload 任何窗口查询都发现不了（Prometheus 里它已不存在），这个 change 不覆盖；「已下线」与「采集挂了」在指标侧不可区分，需要外部清单（如 SLO 目录对照），留给后续。
- 14 天 vs 28 天内存窗口的契约歧义（§9.3 第二行）不动。
- 不重跑、不对照第二个预注册世界调参：它对这个缺陷已污染，只作回归，证据里注明未用于调参。

## 权衡

被正常下线的 workload 在窗口内（默认 14 天）会以 UNKNOWN 出现在日报，之后自然消失。这是「宁可多报一条数据不足，也不沉默」的有意选择。

## 人工 gate 与回滚

纯本地、只读，不触 AWS / 集群。回滚：revert 本 change 的 commit。

## Capabilities

### Modified Capabilities
- `k8s-mainline`（c0027）：workload 发现的窗口口径；条件 A 增加近期有数据子项。
