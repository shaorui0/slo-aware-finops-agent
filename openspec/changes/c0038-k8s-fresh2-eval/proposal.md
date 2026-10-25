## Why

c0037 按第一个预注册世界（c0035）的失败修了 agent，那个世界因此被污染，只能说明修复生效，不能说明泛化。c0036 在修复之前盲写并冻结了第二个世界（冻结 sha 10eefdb，早于 c0037 的第一个实现提交 ff2931a）。本 change 对它评估一次，结果原样记录。

## What Changes

- `eval/k8s_heldout.sh` 增加 `K8S_WORLD`、`K8S_SLOS` 两个环境变量，默认值不变，这样同一个脚本可以评估第二个世界。
- 评估一次：record 模式（真实 `claude -p`），新增叙述 cassette，不修改已有 cassette。
- `eval/EXPERIMENTS.md` 追加 §9：冻结与评估的提交顺序、指标、逐条失分归因、污染披露。

## 规则（评估前写下）

- 评估之后不改世界、不改 expect、不改 agent 和阈值；失分记为发现，修复留给之后的 change，且修复后需要第三个预注册世界。
- 污染披露：主 agent 在 c0036 的回执里看到了这个世界的难例清单（各 workload 的情形概述），但没有转述给做修复的 agent（c0037），c0037 的 prompt 明确禁止读取这些文件。c0036 的作者在写之前从 `git log` 标题里看到过 c0035 的聚合数字。
- 评估由主 agent 执行，这是本次运行中唯一由主 agent 执行的评估；理由是做修复的 agent 不能碰这个世界。

## Impact

只改 `eval/k8s_heldout.sh`（加环境变量）、`eval/EXPERIMENTS.md`（追加）、`eval/cassettes/`（新增）。不涉及 AWS 或集群写操作。
