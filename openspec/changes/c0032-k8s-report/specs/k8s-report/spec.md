## ADDED Requirements

### Requirement: 接口

`finops_agent/k8s/report.py` 提供：
- `render(view, inputs) -> str`：`view` 取 `daily | weekly | monthly`，返回自包含 HTML（内联 CSS，无外部 JS，light/dark 随系统，手机宽度可读，表格在容器内横向滚动）。
- CLI：`python3 -m finops_agent.k8s.report --run runs/<id> [--prev runs/<prev-id>] [--state runs/k8s_state.json] [--score <eval json>] --view daily|weekly|monthly --out file.html`。

`inputs` 是一个 dict，除 `decisions` 外全部可选，缺哪个就渲染哪一块的「没有数据」说明，不抛异常：

| 键 | 来源 | 说明 |
|---|---|---|
| `decisions` | `runs/<id>/k8s_decisions.json` | 必需，c0027 决策文件 |
| `prev` | `--prev` 的 `k8s_decisions.json` | 日报「和上次比」 |
| `plans` | `runs/<id>/k8s_plans.json` | `{"plans": [...]}` 或列表 |
| `state` | `runs/k8s_state.json` | 台账，见下 |
| `actions` | `runs/<id>/k8s_actions.jsonl` | c0030 的执行记录，按行读成列表 |
| `verify` | `runs/<id>/k8s_verify.json` | 见下 |
| `score` | `--score` | eval 考卷 JSON，见下 |
| `unit_cost` | `runs/<id>/k8s_unit_cost.json` | 见下 |

#### Scenario: 缺可选输入
- **WHEN** 只给 `decisions`
- **THEN** 三个视图都能渲染；缺失的块写明缺什么、对应哪个文件

### Requirement: 日报

日报回答「今天有什么要我处理」，自上而下：
1. safe_halt 为真时，页面最顶部红色横幅（先于一切内容）。
2. 页头：as_of、集群、数据窗口；一个「怎么读这份报告」框，用白话解释五个 verdict（ALLOW 可以做 / REQUIRE_APPROVAL 需要你拍板 / DENY 不能动 / UNKNOWN 数据不够 / NOOP 无需调整）、A–D 四个条件、两种节省口径（账面释放的预留 vs 节点能缩才兑现的钱）。
3. 一行摘要：`realizable` 之和（只算 ALLOW）与 `request_value` 之和（只算 ALLOW，单独标注口径）；待你批准 / 被拒 / 数据不足 / 无需调整的条数。
4. 和上次比：新增、不变、过期、verdict 变化。来源是 `prev` 决策与台账；两者都没有时写明。
5. 每个 ALLOW 或 REQUIRE_APPROVAL 的决策一张卡片，固定六个小标题，顺序且各出现一次：现状 / 证据 / 做了哪些分析 / 怎么做 / 做完应该是什么样 / 结论。
6. 被拒与数据不足：DENY 与 UNKNOWN 全部列在一张紧凑表里，列为 workload、资源、哪个条件、为什么、什么数据或动作能改变结论（`unknown_needs`）。
7. NOOP 列表，折叠。

卡片内容规则：
- 现状：request / limit / replicas，p50/p95/p99/max，高峰小时，throttle 峰值，OOM 次数。
- 证据：表，列为证据 id / 查询 / 窗口 / 值 / 数据完整度（valid_days、coverage）。
- 做了哪些分析：A/B/C/D 各一行，带状态徽标和原因。
- 怎么做：新 request；计划存在时给 `apply_cmd`、`rollback_cmd`、观察期、停止条件、canary 范围、approval 文件路径；计划不存在时写明「还没有执行计划」。
- 做完应该是什么样：p99 利用率 = p99 / 新 request（内存另给峰值 / 新 request）；余量；`realizable` 带条件「节点数从 X 到 Y」。`realizable` 为 0 时写明「降了预留但节点数下不来，所以账单不变」。
- 结论：verdict 与一行原因；`narrative` 存在时显式标注「LLM 撰写，已做证据落地校验」，为 null 时不显示该块。
- 卡片里出现的每个数字，必须能在该决策 JSON（`current`、`usage`、`recommendation`、`savings`、`evidence`、`conditions` 的文字）或其计划里找到，或由其中两个数直接算出（利用率、余量、百分比）。

#### Scenario: 卡片六段
- **WHEN** 渲染日报，决策里有 ALLOW 或 REQUIRE_APPROVAL
- **THEN** 每张卡片每个小标题恰好出现一次，顺序固定

#### Scenario: realizable 为 0
- **WHEN** 一个 ALLOW 决策的 `realizable_usd_month` 为 0
- **THEN** 卡片写明账单不变的原因，并仍显示账面释放的预留，标注它不等于账单减少

### Requirement: 周报

周报回答「上周做的改动真的省了吗、有没有伤到服务」：
- 已执行变更：来自 `actions`，每条带 outcome（applied / verified / rolled_back / refused / failed）、reason、观察值、时间。
- 核实结果：来自 `verify`，每个 plan 的预测与实际 $/月、实现率、节点数前后、SLO 是否保持、按原因拆开的偏差。没有 `verify` 或为空时写「本周没有已核实的变更」。
- 回滚与 halt：回滚的 plan 与触发的停止条件，台账里 halted 的提案，当前 safe_halt 状态。
- 台账流转：proposed → approved → applied → verified 各到达多少条，另列 rolled_back / expired / halted 的条数。

### Requirement: 月报

月报回答「这套东西值不值得信」：
- 已核实净节省：只加总有 `verify` 记录且 `slo_ok` 为真的 `realized_usd_month`；没有核实记录就写 0 并说明预测不算节省。
- 单位成本：有 `unit_cost` 时给每百万请求成本，没有时写明不可得与缺什么。
- agent 质量：verdict 条数分布；`unsafe_allow`（来自 `score`，没有就写未提供）；实现率 = Σ实际 / Σ预测；回滚率 = 最终 outcome 为 rolled_back 的 plan 数 / 已触达集群的 plan 数；安全执行率 = 已触达集群的 plan 中最终为 verified 或 applied 且没有回滚、失败、SLO 退化的占比；正确拒绝的高风险条数（`score.items` 有时按考卷核对，没有时按 DENY/UNKNOWN 条数并标注「未核对」）。
- 一段话说明考卷式评分表的含义，以及去哪里看（`eval/EXPERIMENTS.md`）。

### Requirement: 新增输入形状

`runs/<id>/k8s_verify.json`（由后续 change 产出）：

```json
[{"plan_id": "...", "decision_id": "...",
  "predicted_usd_month": 140.16, "realized_usd_month": 131.5, "ratio": 0.94,
  "nodes_before": 6, "nodes_after": 5,
  "deviations": [{"cause": "...", "usd": -8.66, "explanation": "..."}],
  "slo_ok": true, "window": "7d"}]
```

`runs/<id>/k8s_unit_cost.json`：`{"query": "sum(increase(http_requests_total[30d]))", "requests_total": 0, "window_days": 30, "cluster_cost_usd": 0.0}`。每百万请求成本 = `cluster_cost_usd / requests_total × 1e6`。

`runs/k8s_state.json` 台账按渲染需要读取以下字段，其余忽略：`safe_halt`（bool）、`proposals`（列表，或以 `namespace/workload/resource` 为键的 dict），每条含 `namespace`、`workload`、`resource`、`state`（proposed|approved|applied|verified|rolled_back|expired|halted）、可选 `plan_id`、`history: [{state, at}]`。

`--score` 的 eval JSON 读取：`verdict_accuracy`、`unsafe_allow`、`unknown_recall`、`request_in_range`、可选 `realizable_accuracy`，可选 `items: [{id, expect, got}]`。

#### Scenario: 没有核实文件
- **WHEN** 渲染周报且没有 `verify`
- **THEN** 页面含「本周没有已核实的变更」，不抛异常
