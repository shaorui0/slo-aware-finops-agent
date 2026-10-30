## Why

c0043 修了「静默丢 workload」（缺数据在发现阶段就消失）。第二个预注册世界正是发现这个缺陷的世界，已被污染，只能说明修复生效。c0042 在修复之前盲写并冻结了第三个世界（冻结 sha 6450a8f）。本 change 对它评估一次，结果原样记录。

## What Changes

- 评估一次：`K8S_WORLD=testenv/heldout/k8s_fresh3_world.yaml K8S_SLOS=testenv/heldout/k8s_fresh3_slos.yaml K8S_RUN=k8s-fresh3 K8S_LABEL=k8s-fresh3`，record 模式（真实 `claude -p`），新增叙述 cassette，不修改已有 cassette。
- `eval/EXPERIMENTS.md` 追加 §10：冻结与修复的提交顺序、指标、逐条失分归因、污染披露。

## 规则（评估前写下）

- 评估之后不改世界、不改 expect、不改 agent 和阈值；失分记为发现，修复留给之后的 change，且修复后需要第四个预注册世界。
- 裁决由确定性代码给出，叙述只影响解释文字；record 模式不影响打分。
- 污染披露：
  - 第三个世界的作者（c0042）越线读到过 c0036 evidence 的前 80 行，里面有 c0035 的一行聚合指标；还看到过 change 目录名。没有看到逐 workload 的失败。
  - 做修复的 agent（c0043）与世界作者并行工作，在世界作者交回之前就已完成，从未接触该世界。
  - 主 agent 在 c0042 的回执里看到了这个世界的 workload 清单与期望裁决，在合并与评估之间没有改动任何 agent 代码或阈值。
- 评估由主 agent 执行。

## Impact

只改 `eval/EXPERIMENTS.md`（追加）、`eval/cassettes/`（新增）与本 change 目录。不涉及 AWS 或集群写操作。
