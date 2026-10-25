## Why

c0029 的决策内核在 `--world k8s` 上 18 条里对 16 条。两条错的是 batch-nightly 的 cpu 与 memory：判成 REQUIRE_APPROVAL，标准答案是 DENY，原因是「条件 C 未知：批任务缺少 deadline」。deadline 是 SLO 的定义，不是指标，metrics 里本来就不该有它。契约第一条「Metrics / SLO / Requests 三者不能互相替代」要求把 SLO 定义做成第三个独立输入，而不是往指标标签里塞。

内核之后的几段还没连起来：判断层（LLM 叙述）、执行计划、提案台账、评估闸、连续运行。c0031 把这条主线接通，并让 CI 对着世界的 `expect` 打分。

## What Changes

- 新增 SLO 目录 `testenv/k8s_slos.yaml`（OpenSLO 风格的最小形状：每个 (namespace, workload) 的 availability、p99_seconds 目标，批任务的 deadline_seconds），agent 用 `--slos <path>` 读入。没有 SLO 的 workload 不出现在目录里。一个测试断言它与世界文件的 `slo` 块一致。
- 改 `collect.py`、`policy.py`（最小）：目标值与 deadline 取自目录；燃烧率与剩余预算仍取自指标。workload 不在目录里时，条件 C 为 UNKNOWN，即使存在 `slo_*` 指标。
- 条件 D：`nodes.max` 作为 `max_nodes` 传入；节点自动扩缩能为回滚补节点时，D 可以依赖它。`max` 未知时保持字面行为。契约文字在本 change 的 spec delta 里修订。
- 新增 `narrate.py`：每个决策一次隔离的 LLM 调用（只给该决策的 facts 与 evidence），用中文写「现状 → 证据 → 结论」、「补什么证据会改变结论」、ALLOW/REQUIRE_APPROVAL 的提案文本。LLM 不设置也不改 verdict；输出过 `judge/validate.py` 的落地规则，未取自证据的数字所在句子被抹掉。复用 `llm.py` 的 record/replay 盒带与 trace。
- 新增 `plans.py`：ALLOW 与 REQUIRE_APPROVAL 出 `runs/<id>/k8s_plans.json`，严格按契约的计划形状；停止条件由决策与 SLO 推出。
- 新增 `ledger.py`：`runs/k8s_state.json`，按契约的状态与规则（5% 去重、verdict 或目标变化则 expired、applied 后 SLO 退化则 halted 并全局 `safe_halt`）。
- 新增 `pipeline.py` 与 `python3 -m finops_agent k8s run|mark|clear-halt` 子命令，把 SLO 目录 → collect → decide → narrate → plans → ledger → `k8s_decisions.json` 串起来。
- 新增 `eval/score_k8s.py`、`eval/k8s_baseline.json`：`verdict_accuracy`、`unsafe_allow`（硬闸 0）、`unknown_recall`、`request_in_range`、`realizable_accuracy`，外加基线回归闸。
- `make k8s-eval`（起 server、回放盒带跑 agent、打分、停 server）进 CI；`make k8s-days` 连续三天（两天基线世界，第三天漂移场景）。

## 不做什么

- 不写 `act.py` 与 k3d（t3）、不写日报周报月报（后续 change）。
- 不改附录（AWS 广度扫描）的任何代码、盒带与基线。
- 不碰 README 与 `reports/index.html`。

## Impact

对集群与 AWS 全部只读，计划只落成 JSON，不执行，没有人工 gate 的变更。LLM 默认回放盒带，CI 无需 API key。DoD 对应 K3、K4、K8。
