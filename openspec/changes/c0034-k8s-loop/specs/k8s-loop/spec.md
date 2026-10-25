## ADDED Requirements

### Requirement: 执行回写台账

`python3 -m finops_agent.k8s.act apply` 多一个参数 `--state`（默认 `runs/k8s_state.json`，相对 `--root`）。`apply_plan(..., state_path=...)` 在每个结局后调用台账接口（`ledger.mark`），状态转移如下，其余结局不动状态：

| 结局 | 台账 |
|---|---|
| refused | 不变 |
| 补丁已应用（过了 context、审批、漂移三道闸） | proposed → approved（备注「approval 文件存在」）→ applied；已经是 approved 则只做 → applied |
| verified | applied → verified |
| rolled_back（停止条件命中，回滚后资源核对等于变更前） | applied → rolled_back |
| failed（apply 命令失败 / 回滚没有恢复 / 补丁与实际不符） | apply 命令失败：不变；补丁已应用之后的 failed：停在 applied，等人处理 |

写台账失败（没有这个 plan 的存活提案、转移不合法、状态文件不存在）不改变执行结果，但执行记录里的 `ledger` 字段写明原因，终端也大声打印。成功时 `ledger` 字段是 `{"status": <新状态>, "state": <路径>}`。台账用 `ref=plan_id` 定位提案。

#### Scenario: 成功路径
- **WHEN** 一个 proposed 的 plan 有审批文件，观察期内没有触发停止条件
- **THEN** 台账 history 依次出现 approved、applied、verified，执行记录最后一条 `ledger.status == "verified"`

#### Scenario: 回滚路径
- **WHEN** 观察期内命中停止条件
- **THEN** 台账 history 依次出现 approved、applied、rolled_back，该提案之后不再被提案，直到目标值变化

#### Scenario: 被拒绝
- **WHEN** 缺审批文件
- **THEN** 台账状态不变，执行记录 outcome 为 refused

### Requirement: 漂移闸门只看计划涉及的资源

`same_resources(live, before)` 只比较计划涉及的资源名（`before.requests`、`before.limits` 里出现的 key，加上补丁里出现的 key），副本数照旧比较。生成的计划一次只改 cpu 或 memory 一种，另一种资源不同不算漂移。涉及的资源值不同、副本数不同仍然拒绝。

### Requirement: 流水线出三份报告

`k8s run` 在 `runs/<id>/` 写 `k8s_daily.html`、`k8s_weekly.html`、`k8s_monthly.html`（经 `report.render`）。输入：本次决策、计划、更新后的台账、`--prev-run <dir>`（可选，上一次 run 目录）、run 目录里的 `k8s_actions.jsonl` 与 `k8s_verify.json`（有就读）、`--score <json>`（可选，缺省时读 run 目录里的 `k8s_eval.json`，有就读）。缺的输入报告里明说，不猜。生成的 HTML 不含本机绝对路径。

### Requirement: 报告读真实形状

- 台账：`proposals[]` 每条的 `status`、`history[].status`；同一个 `namespace/workload/resource` 有多条提案时，卡片上的台账状态取最后一条。
- 考卷：`eval/score_k8s.py` 写的 `k8s_eval.json`（`verdict_accuracy`、`unsafe_allow`、`unknown_recall`、`request_in_range`、`realizable_accuracy`、`n{}`、`wrong_verdicts[]`）。「正确拒绝的高风险」用 `unknown_recall × n.unknown_expected` 与 `unsafe_allow` 表述，不再需要逐条 `items`。
- 计划：`{run_id, as_of, safe_halt, plans[]}`，也接受裸列表。
- 叙述：`narrative` 是对象 `{source, why{status,evidence,conclusion}, would_change[], proposal, evidence[], redactions, model, cassette}`。「结论」段渲染 why 三句与提案，标明「由模型撰写、数字已核对」（source 为 template 时标明「模板生成」）；`would_change` 渲染为「什么情况下判定会变」；为 null 时不显示。

### Requirement: 报告打磨

- 证据表每行有「含义」列：人话标签（有效天数、覆盖率、使用量 p99、高峰期 throttle 比例、SLO 1 小时燃烧率……），由查询与窗口推导。
- 不列无值的证据（该 workload 没有这个东西，如无 HPA、非批任务的 job 时长），表下写明被省略的条数。
- 每张卡片「做完应该是什么样」的第一句直接说钱动不动、为什么：`realizable > 0`「会动：节点从 X 到 Y 才省，$…/月」；等于 0「不会动：…」；REQUIRE_APPROVAL 时同时说明要人拍板。

### Requirement: demo 用流水线生成的计划

`scripts/k8s_demo.sh` 的流程：
1. `k8s run` 对合成世界（本地 metrics server）生成 `runs/k8s-demo/k8s_plans.json` 与台账；
2. 取 shop/api-steady 的 ALLOW 计划（cpu：走回滚；memory：走 verified）；
3. demo 清单的容器名、request、limit、副本数与世界里的 api-steady 一致；live 与计划 `before` 不同时才改写 `before`，并大声打印「合成世界与 demo 集群不同，改写了什么」；
4. 缺审批文件被拒、context 不对被拒、批准后注入过载负载触发回滚、恢复正常负载后 verified；
5. 每一步后可读到台账（`runs/k8s-demo/k8s_state.json`），最终打印 proposed → approved → applied → verified 与 → rolled_back 两条流转；
6. 渲染周报（含真实 `k8s_actions.jsonl`）。

只操作集群 `finops-test`（context `k3d-finops-test`）。

#### Scenario: 停止条件
- **WHEN** 计划只降 request，观察期内注入压垮服务的负载
- **THEN** p99 超过 SLO 触发回滚；demo 与报告不把它说成「降 request 造成了退化」
