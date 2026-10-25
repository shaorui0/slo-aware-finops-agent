# k8s-pipeline：主线串联的实现约定与契约修订

本文件修订 c0027 契约中的两处（条件 C 的输入来源、条件 D 的容量判据），并补充串联层的实现细节。其余契约不变。

## MODIFIED Requirements

### Requirement: 三者分离（SLO 目录是第三个独立输入）

SLO 的定义（目标与 deadline）来自 **SLO 目录文件**，不来自指标，也不来自世界文件。agent 以 `--slos <path>` 读入。

目录形状（OpenSLO 风格的最小子集）：

```yaml
apiVersion: finops.local/v1
kind: SLOCatalog
metadata: {name: finops-k8s-synth}
slos:
  - {namespace: shop, workload: api-steady, objectives: {availability: 0.999, p99_seconds: 0.3}}
  - {namespace: data, workload: batch-nightly, deadline_seconds: 7200}
```

- 每个 (namespace, workload) 至多一条；没有 SLO 的 workload 不出现在目录里。
- `objectives.availability`、`objectives.p99_seconds`：在线服务的目标。`deadline_seconds`：批任务的完成期限。
- 燃烧率（`slo_burn_rate_1h`）与剩余预算（`slo_error_budget_remaining_ratio`）仍来自指标（recording rule）。目录给「目标」，指标给「现在烧了多少」。
- 目录里的数字也是证据：每个取自目录的数字在 `evidence[]` 里有一条 `source: "slo_catalog"` 的记录，`query` 写成 `slo_catalog:<namespace>/<workload>.<field>`。

#### Scenario: workload 不在目录里
- **WHEN** 一个 workload 没有出现在 SLO 目录里，但指标里有它的 `slo_*` 序列
- **THEN** 条件 C 为 UNKNOWN，`unknown_needs` 写明「需要在 SLO 目录里为该 workload 定义 SLO」，verdict 不是 ALLOW

#### Scenario: 批任务 deadline 来自目录
- **WHEN** batch-nightly 在目录里有 `deadline_seconds: 7200`，`job_last_duration_seconds` 的最大值超过 `deadline_guard × 7200`
- **THEN** 条件 C 为 FAIL，verdict 为 DENY

#### Scenario: 目录与世界一致
- **WHEN** 加载 `testenv/k8s_slos.yaml` 与 `testenv/k8s_world.yaml`
- **THEN** 世界里有 `slo` 块的 workload 恰好是目录里的那些，数值逐项相等

不给 `--slos` 时（`collect_facts(slos=None)`）保持 c0029 的旧行为：SLO 信息只来自指标。生产与评估一律传目录。

### Requirement: 策略引擎（条件 D 的容量判据）

条件 D 原文「按装箱模拟缩容后，集群空闲 allocatable ≥ Δrequest × 副本数」修订为：

- 缩容后的容量按 `max(nodes_after, max_nodes)` 个节点计：节点自动扩缩的上限 `nodes.max`（`max_nodes`）已知时，回滚所需容量只要 `nodes_after + 需要补的节点 ≤ max_nodes` 就由自动扩缩补上，D 可以依赖它。
- `max_nodes` 未知（调用方没传）时，按字面 `nodes_after` 个节点计，行为与 c0029 相同。
- agent 通过 `--nodes-from <yaml>`（读其中的 `nodes.min` 与 `nodes.max`）或 `--min-nodes`、`--max-nodes` 得到这两个数；它们是集群自动扩缩的配置，不是指标。

#### Scenario: max 已知
- **WHEN** 缩容后集群空闲容量小于回滚所需，但 `max_nodes` 个节点的容量足够
- **THEN** D 为 PASS

#### Scenario: max 未知
- **WHEN** 同上，但没有传 `max_nodes`
- **THEN** D 为 FAIL

## ADDED Requirements

### Requirement: 决策文件的追加字段

`k8s_decisions.json` 的每条决策在契约字段之外追加两个字段，其余不变：

- `container`：容器名（取自 `kube_deployment_spec_replicas` 的 `container` 标签），计划的 patch 需要它。
- `slo`：`{defined, objectives, deadline_seconds, burn_rate_1h, budget_remaining, job_max_duration}`。`defined` 为 true 表示目录里有该 workload。数值都有对应的 evidence。

`narrative` 不再是 null，是对象：

```json
{"source": "llm|template",
 "why": {"status": "现状", "evidence": "证据", "conclusion": "结论"},
 "would_change": ["补什么证据会改变结论"],
 "proposal": "ALLOW/REQUIRE_APPROVAL 才有，其余为 null",
 "evidence": ["E1"], "redactions": 0, "model": "sonnet", "cassette": "<key 前 12 位>"}
```

#### Scenario: LLM 不改 verdict
- **WHEN** 叙述完成
- **THEN** 每条决策除 `narrative` 外与叙述前逐字段相同；LLM 回复里的 verdict 字样被忽略，与决策 verdict 不一致的句子被抹掉

### Requirement: 叙述的落地与回放

- 每条决策一次独立 LLM 调用，prompt 只含该决策的 facts 与 evidence，不含其他决策。
- 数字落地走 `judge/validate.py`（G1–G9）：叙述里的每个数字必须出现在该决策的 `current`、`usage`、`recommendation`、`savings`、`hpa`、`slo`、`conditions[].why`、`reasons`、`evidence[]` 里；含未取自证据数字的句子整句替换为 `[已移除：含未取自证据的数字]`，原文只进 trace。
- 复用 `llm.py` 的盒带：`FINOPS_LLM_MODE=replay` 缺盒带即报错退出，不降级；`record` 用真实 `claude -p` 录制；`live` 不写盒带。
- `record` 或 `live` 下 LLM 调用失败或回复不合格时，该决策退回确定性模板叙述（`source: "template"`），trace 里记录原因。`replay` 下盒带缺失不退回。
- 每次 LLM 调用写一行 trace：`source=llm, op=narrate, mode, tokens_in, tokens_out, latency_ms, cost_usd, key`。

### Requirement: 执行计划的字段取值

`k8s_plans.json` 为 `{"run_id", "as_of", "safe_halt", "plans": [...]}`，每个计划严格是契约形状，没有追加字段：

- `plan_id = P-<decision id>-<目标 request>`，目标取 `%g` 格式，如 `P-K-shop-api-steady-cpu-0.25`。
- `before`：只含被改的那一个资源，`requests`/`limits` 各一个键（没设 limit 则 `limits` 为空对象），`replicas` 为副本数。数量用 kubectl 读回的写法：cpu 整数核写 `"2"`，否则 `"250m"`；memory 整数 GiB 写 `"2Gi"`，否则 `"1152Mi"`。只含单资源是为了同一 workload 的 cpu 与 memory 两个计划互不使对方的 `before` 失效。
- `patch` 只含该资源的 `requests`；`rollback_cmd` 用同样的 patch 形状写回 `before` 的 request。
- `stop_conditions`：cpu 计划含 `throttle_ratio > max_throttle`；SLO 目录有 `p99_seconds` 时含 `p99_seconds > 该目标`；memory 计划含 `oom_killed > 0`；都含 `restarts > 0`。
- `canary: {"scope": "one workload"}`，`observe_seconds: 120`，`approval_file: runs/approvals/<plan_id>.approved`。
- DENY、UNKNOWN、NOOP 不出计划；台账处于 `safe_halt` 时不出任何计划；已 applied/halted/rolled_back 的提案不再出计划。

### Requirement: 提案台账

`runs/k8s_state.json`：

```json
{"version": 1, "safe_halt": false, "halt": null,
 "last_run": {"run_id": "...", "as_of": "..."},
 "proposals": [{"plan_id": "...", "key": "<ns>/<workload>/<resource>", "decision_id": "...",
                "namespace": "...", "workload": "...", "resource": "...", "verdict": "ALLOW",
                "target": {"request": 0.25, "unit": "core"}, "current_request": 1.0,
                "status": "proposed", "created_run": "...", "first_seen": "<as_of>", "last_seen": "<as_of>",
                "history": [{"run_id": "...", "as_of": "...", "status": "...", "note": "..."}]}]}
```

时间戳用决策的 `as_of`，不用墙钟，使连续运行可重放。规则：

- **去重**：同一 key 下有 proposed/approved 提案，本轮 verdict 相同且目标值相差 ≤ `same_target_tolerance`（5%）：只更新 `last_seen`（unchanged）。
- **过期**：verdict 变了，或目标相差 > 5%，或该 key 本轮不再是 ALLOW/REQUIRE_APPROVAL，或 workload 已消失：旧提案转 `expired`；本轮仍是 ALLOW/REQUIRE_APPROVAL 则新建提案（new）。
- **批准**：`runs/approvals/<plan_id>.approved` 存在时，proposed 转 approved（只检查存在，不替人批准）。
- **状态推进**：approved→applied→verified、applied→rolled_back 由 `python3 -m finops_agent k8s mark` 写入（执行层 t3 的 act 完成后调用），不由本轮自动推断。
- **halted**：某 workload 有 applied 的提案，而本轮该 workload 任一资源的条件 C 为 FAIL（燃烧率 > 1 或剩余预算 < min_budget）：该提案转 `halted`，全局 `safe_halt: true`，`halt` 记原因。`safe_halt` 期间照常写决策文件，但不出计划、不新建、不过期提案，直到 `k8s clear-halt` 人工清除。
- **回滚后不复提**：rolled_back 的提案，同 key 同 verdict 且目标相差 ≤ 5% 时不再新建（suppressed），目标变化后才可再提。

#### Scenario: applied 后 SLO 退化
- **WHEN** 一个提案已被 mark 为 applied，下一轮该 workload 的条件 C 为 FAIL
- **THEN** 提案为 halted，`safe_halt` 为 true，本轮 `k8s_plans.json` 的 `plans` 为空

#### Scenario: 持续运行
- **WHEN** 连续三天运行，第三天用漂移场景
- **THEN** 前两天同一提案 unchanged（`last_seen` 前移），第三天漂移 workload 的旧提案 expired 并新建，打印每天的 new / unchanged / expired 计数

### Requirement: 评估

`eval/score_k8s.py` 按契约出五个指标。`make k8s-eval` 的闸：`verdict_accuracy ≥ 0.9`、`unsafe_allow = 0`（非 0 退出）、`unknown_recall = 1.0`、`request_in_range ≥ 0.8`，并对 `eval/k8s_baseline.json` 做回归闸（任一指标低于基线即失败）。`realizable_accuracy` 只对 `expect` 写了 `realizable_usd_month` 的条目计。CI 没有 API key 与 `claude`：LLM 一律回放，缺盒带直接失败。
