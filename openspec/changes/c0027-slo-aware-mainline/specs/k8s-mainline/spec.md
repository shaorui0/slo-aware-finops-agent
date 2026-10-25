# k8s-mainline：主线接口契约

c0028 起的各个 change 按这份契约并行实现。改契约需要先改这个文件，再改代码。

## ADDED Requirements

### Requirement: 三者分离

Metrics（实际发生了什么）、SLO（业务允许发生什么）、Requests（调度器预留多少）是三类独立输入，不能互相替代。任何一类缺失，都必须表现为某个条件 UNKNOWN，不能用另一类去推断补齐。

#### Scenario: 没有 SLO 时不拿利用率替代
- **WHEN** 一个 workload 的 CPU 利用率长期很低，但没有定义 SLO
- **THEN** 条件 C 为 UNKNOWN，verdict 不是 ALLOW

### Requirement: 合成世界文件

`testenv/k8s_world.yaml`（以及同 schema 的预注册世界、场景文件）至少包含以下字段：

```yaml
cluster: {name, period: {start, end}, step_seconds: 300}
pricing: {node_type: m6i.xlarge, node_hour_usd: 0.192, cpu_core_hour_usd: 0.0316, mem_gib_hour_usd: 0.0041}
nodes: {count, min, max, allocatable: {cpu: 3.8, memory_gib: 14.5}}   # 节点自动扩缩的上下限
workloads:
  - name, namespace, team, kind: Deployment
    replicas: N
    container: {name, requests: {cpu: "2", memory: "4Gi"}, limits: {cpu: "4", memory: "6Gi"}}
    hpa: {metric: cpu, target_utilization: 70, min: 2, max: 10}        # 可选
    slo: {availability: 0.999, p99_seconds: 0.2, deadline_seconds: 7200}  # 可选；缺失即「未定义 SLO」
    profile: {...}            # 生成 usage 的形状：基线、日周期、批窗口、内存峰、OOM 时刻等
    data_days: 21             # 有指标的天数，少于 period 时从 period 末尾往前算
    events: [...]             # 可选：漂移、OOM、错误预算燃烧等，带时间
    expect:                   # 标准答案，按资源分别给
      cpu:    {verdict: ALLOW|REQUIRE_APPROVAL|DENY|UNKNOWN|NOOP, request_range: [lo, hi], why: "..."}
      memory: {verdict: ..., request_range: [lo, hi], why: "..."}
traps: [...]                  # 看着能降、其实不能的那几条，各自指向 workload
```

#### Scenario: 世界自洽
- **WHEN** 加载世界文件
- **THEN** 每个 workload 的 `expect` 两个资源都有值，`request_range` 的 lo ≤ hi，并且 lo 小于当前 request（NOOP 除外）

### Requirement: 指标名与标签

test-metrics-server 以 `--world k8s` 方式启动时，提供下列序列。标签至少包含 `namespace`、`workload`、`pod`、`container`；节点级指标带 `node`。`workload` 标签视为抓取时已做 relabel。

| 指标 | 类型 | 说明 |
|---|---|---|
| `container_cpu_usage_seconds_total` | counter | cAdvisor |
| `container_memory_working_set_bytes` | gauge | cAdvisor |
| `container_cpu_cfs_periods_total` / `container_cpu_cfs_throttled_periods_total` | counter | throttling 比例 = throttled / periods |
| `kube_pod_container_resource_requests` / `kube_pod_container_resource_limits` | gauge | 带 `resource="cpu"\|"memory"`、`unit="core"\|"byte"` |
| `kube_pod_container_status_restarts_total` | counter | |
| `kube_pod_container_status_last_terminated_reason` | gauge | 带 `reason="OOMKilled"` |
| `kube_deployment_spec_replicas` | gauge | |
| `kube_horizontalpodautoscaler_spec_target_metric` / `kube_horizontalpodautoscaler_status_current_replicas` / `kube_horizontalpodautoscaler_spec_max_replicas` | gauge | 只有带 hpa 的 workload 才有 |
| `kube_node_status_allocatable` | gauge | 带 `resource`、`node` |
| `kube_pod_status_phase` | gauge | 带 `phase="Pending"` 等 |
| `http_requests_total` | counter | 带 `code` |
| `http_request_duration_p99_seconds` | gauge | 已预聚合（相当于 recording rule），避免实现 histogram_quantile |
| `slo_error_budget_remaining_ratio` / `slo_burn_rate_1h` | gauge | 只有定义了 SLO 的 workload 才有 |
| `job_last_duration_seconds` | gauge | 批任务每次运行的完成时长 |

查询只用 metrics_server 现有的 PromQL 子集。默认 `--world` 仍是原来的 AWS 世界，附录不受影响。

### Requirement: 推荐器（确定性）

`finops_agent/k8s/recommend.py` 对每个 workload 的 cpu 和 memory 各出一条推荐，参数从 `config/k8s_policy.yaml` 读取：
- cpu：`quantile_over_time(0.99)` 的每 pod 使用量（core）乘 `(1 + cpu_headroom)`，向上取整到 `cpu_step`。
- memory：窗口内最大 working set 乘 `(1 + mem_headroom)`，向上取整到 `mem_step`。
- 推荐值不低于当前值时，verdict 为 NOOP（无需调整）。

### Requirement: 两种节省口径

每条推荐给出两个金额，报告里两者都显示并写明口径：
- `request_value_usd_month`：Δrequest × 单价 × 730 × 副本数，即「账面上释放了多少预留」。
- `realizable_usd_month`：对整个集群的 requests 做装箱模拟（first-fit decreasing，节点 allocatable 来自世界文件），算出只应用这一条变更时能少开几个节点，再乘以节点单价 × 730。节点数下不来时这个值为 0，这是 packing-trap 要测的点。

### Requirement: 策略引擎（确定性，LLM 不参与 verdict）

`finops_agent/k8s/policy.py` 对每条推荐评四个条件，每个条件输出 `{status: PASS|FAIL|UNKNOWN, why, evidence: [evidence id]}`：

| 条件 | 含义 | PASS 的判据（阈值来自 `config/k8s_policy.yaml`） |
|---|---|---|
| A 数据足够 | 历史数据覆盖峰值与周期 | 有效天数 ≥ `min_days`（14），样本覆盖率 ≥ `min_coverage`；不足为 UNKNOWN |
| B 资源余量 | 降配后仍能扛住峰值 | cpu：新 request ≥ p99 × (1+margin)，且高峰期 throttling 比例 < `max_throttle`；memory：新 request ≥ 峰值 working set × (1+margin)，窗口内无 OOMKilled，峰值 < `oom_guard` × limit；集群无 Pending |
| C 服务健康 | SLO 允许变更 | 定义了 SLO，`slo_burn_rate_1h` ≤ 1，`slo_error_budget_remaining_ratio` ≥ `min_budget`；批任务的 `job_last_duration_seconds` 最大值 ≤ `deadline_guard` × deadline；未定义 SLO 时为 UNKNOWN |
| D 可恢复 | 能回滚，且回滚时有容量 | 有变更前快照和回滚命令；按装箱模拟缩容后，集群空闲 allocatable ≥ Δrequest × 副本数 |

判定规则按顺序执行，命中第一条即停：
1. 推荐值不低于当前值 → NOOP
2. C = FAIL → DENY（服务正在退化，冻结优化）
3. A ≠ PASS → UNKNOWN，`unknown_needs` 写明补什么数据才能判
4. B = FAIL 或 D = FAIL → DENY
5. 任一条件为 UNKNOWN，或存在 HPA 联动（hpa.metric 是本资源）→ REQUIRE_APPROVAL
6. 其余 → ALLOW

UNKNOWN 永远不被当成通过。

### Requirement: 决策文件

每次运行写 `runs/<run_id>/k8s_decisions.json`：

```json
{"as_of": "...", "world": "...", "decisions": [{
  "id": "K-<namespace>-<workload>-<resource>",
  "workload": "...", "namespace": "...", "team": "...", "resource": "cpu|memory",
  "current": {"request": 2.0, "limit": 4.0, "replicas": 3, "unit": "core|GiB"},
  "usage": {"p50": 0, "p95": 0, "p99": 0, "max": 0, "valid_days": 0, "coverage": 0, "peak_hours_utc": [..], "throttle_peak": 0, "oom_events": 0},
  "recommendation": {"request": 0, "method": "p99*(1+0.3) ceil 0.05", "headroom": 0.3},
  "savings": {"request_value_usd_month": 0, "realizable_usd_month": 0, "nodes_before": 0, "nodes_after": 0},
  "conditions": {"A": {"status": "PASS", "why": "...", "evidence": ["E1"]}, "B": {}, "C": {}, "D": {}},
  "verdict": "ALLOW", "reasons": ["..."], "unknown_needs": [],
  "hpa_coupled": false,
  "evidence": [{"id": "E1", "query": "<PromQL>", "window": "14d", "value": 0.0, "source": "prometheus"}],
  "narrative": null
}]}
```

报告里出现的每个数字，都必须能在 `evidence[].value`、`current`、`usage`、`recommendation` 或 `savings` 里找到。`narrative` 由判断层填写，受 `judge/validate.py` 的落地规则约束。

### Requirement: 执行计划与 k3d 执行

只有 ALLOW 和 REQUIRE_APPROVAL 的决策会生成计划，写入 `runs/<run_id>/k8s_plans.json`：

```json
{"plan_id": "P-<decision id>-<target>", "decision_id": "...", "context": "k3d-finops-test",
 "before": {"requests": {}, "limits": {}, "replicas": 0},
 "patch": {"spec": {"template": {"spec": {"containers": [{"name": "...", "resources": {"requests": {}}}]}}}},
 "apply_cmd": "kubectl --context k3d-finops-test -n <ns> patch deploy <w> --type strategic -p '<json>'",
 "rollback_cmd": "...",
 "canary": {"scope": "one workload"}, "observe_seconds": 120,
 "stop_conditions": [{"signal": "throttle_ratio|p99_seconds|oom_killed|restarts", "op": ">", "value": 0}],
 "approval_file": "runs/approvals/<plan_id>.approved"}
```

`finops_agent/k8s/act.py` 必须满足以下五条：
- approval 文件不存在就不执行。
- `context` 不是 `k3d-finops-test` 就不执行。
- 执行前重新读取当前资源；与 `before` 不一致时视为状态漂移，不执行。
- 观察期内命中任一停止条件就执行 `rollback_cmd`，并验证 rollout status。
- 每条 kubectl 写命令前带 `# INTENT:` 行。

项目 hook 拦截所有其他 context 上的 kubectl 写操作。

### Requirement: 提案台账与持续运行

`runs/k8s_state.json` 按 `(namespace, workload, resource)` 记一条提案，状态取值为 `proposed | approved | applied | verified | rolled_back | expired | halted`。规则：
- 目标值相同（差异 ≤ 5%）的重复运行不新增提案，只更新 `last_seen`。
- 新一轮的 verdict 变了，或目标值相差超过 5%，旧提案转为 expired 并新建提案。
- 已 applied 的 workload 观察到 SLO 退化时，转为 halted，并在全局置 `safe_halt: true`。之后的运行只报告不出计划，直到人工清除。

### Requirement: 定期报告

`finops_agent/k8s/report.py` 用同一份数据输出三个中文视图：日报、周报、月报。

日报的每个候选一张卡片，固定六段：
1. 现状
2. 证据
3. 做了哪些分析
4. 怎么做
5. 做完应该是什么样
6. 结论

日报另有一行摘要、和上次比的差异，以及被拒清单。

周报包含：
- 已执行变更的预测与实际对比、实现率、偏差原因
- 回滚与 halt 记录
- 台账流转

月报包含：
- 已验证净节省、单位成本
- agent 自身质量：考卷成绩、unsafe ALLOW 数、实现率、回滚率

### Requirement: 评估

`eval/score_k8s.py` 读决策文件和世界文件的 `expect`，输出以下指标：

| 指标 | 含义 |
|---|---|
| `verdict_accuracy` | 按 (workload, resource) 计 |
| `unsafe_allow` | expect 为 DENY 或 UNKNOWN 却给出 ALLOW 的条数。硬闸为 0，非 0 时退出码非 0 |
| `unknown_recall` | expect 为 UNKNOWN 的条数里判成 UNKNOWN 的比例 |
| `request_in_range` | ALLOW 与 REQUIRE_APPROVAL 的推荐值落在 `request_range` 内的比例 |
| `realizable_accuracy` | 可选。只有 expect 写了 realizable 区间时才计 |

预注册世界 `testenv/heldout/k8s_fresh_world.yaml` 先冻结、提交，之后只评一次，评估结果原样写进 `eval/EXPERIMENTS.md`。
