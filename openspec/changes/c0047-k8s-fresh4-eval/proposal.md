## Why

c0046 修了「近期阶跃后仍 ALLOW」（条件 B 加近期用量子项），并给打分器加了 `allow_below_recent` 硬闸。第三个预注册世界正是发现这个缺陷的世界，已被污染。c0045 盲写并冻结了第四个世界（冻结 `abda3c3`），冻结早于 c0046 的派发与全部实现提交，c0046 的分支不含这个世界。本 change 对它评估一次，结果原样记录。

## What Changes

- 评估一次：`K8S_WORLD=testenv/heldout/k8s_fresh4_world.yaml K8S_SLOS=testenv/heldout/k8s_fresh4_slos.yaml K8S_RUN=k8s-fresh4 K8S_LABEL=k8s-fresh4`，record 模式（真实 `claude -p`），只新增叙述 cassette。
- `eval/EXPERIMENTS.md` 追加 §11；README 与 NARRATIVE 成绩单加第四个世界一列。

## 规则（评估前写下）

- 评估之后不改世界、不改 expect、不改 agent、不改阈值、不改打分器；失分记为发现。
- 裁决由确定性代码给出，叙述不影响打分。
- 污染披露：
  - 第四个世界的作者看到过 change 目录名（其中 c0037 的名字暗示过「样本外有漏判」）与外层仓库的提交标题，没有看到任何逐 workload 的失败。作者读不到 `config/`，阈值靠「把数据放在远离任何合理阈值的地方」规避，这是假设。
  - 做修复的 agent（c0046）在第一次派发时用错了仓库、被停下，没有产生文件；第二次派发在冻结之后，工作树分支不含该世界，prompt 禁止读取。
  - 主 agent 在合并前看到了这个世界的 workload 清单与期望裁决，合并与评估之间没有改动任何 agent 代码、阈值或打分器。

## Impact

只改 `eval/EXPERIMENTS.md`、`eval/cassettes/`（新增）、README / NARRATIVE 成绩单与本 change 目录。
