## Why

主线 K8s agent 在基础世界 `testenv/k8s_world.yaml` 上是 1.0（verdict_accuracy 18/18），但这个世界就是 agent 被构建、被调阈值时对着的世界，1.0 是样本内的数字。附录（AWS）的同类问题在 c0024 里被预注册世界回答过：recall 从 1.0 掉到 0.75。主线还没有这样一次未污染的测量。

## What Changes

- 新建一个全新的合成 K8s 世界 `testenv/heldout/k8s_fresh_world.yaml` 与 SLO 目录 `testenv/heldout/k8s_fresh_slos.yaml`：新 workload 命名、新形状、至少 3 种基础世界没有的情形，每个 workload 的 `expect` 与理由由 SRE 判断和契约规则写出。
- 世界生成器若表达不了某种形状，在 `testenv/k8s_world.py` 里**只加新 profile 种类**，已有输出逐点不变。
- 先冻结（freeze commit），之后评估恰好一次，原样记录。
- `eval/k8s_heldout.sh` 复现这一次评估；`eval/EXPERIMENTS.md` 追加「主线 K8s：预注册新世界」。

## 盲测协议（binding，先于任何工作写下）

冻结 commit 之前：

1. 不读 `finops_agent/k8s/policy.py`、`recommend.py`、`collect.py`，不读 `config/k8s_policy.yaml` 的阈值。
2. 不运行 agent，不运行评分器。
3. `expect` 只依据 SRE 领域判断与 c0027/c0031 契约写出的规则：一个谨慎的评审人看了这些数据会怎么判。
4. 允许读的只有：契约 spec、基础世界与 SLO 目录（只看格式）、`testenv/k8s_world.py`（生成器，不是 agent）、`eval/score_k8s.py`（只为确认 `expect` 的字段怎么被读）。

冻结之后：

5. 不改世界、不改 `expect`、不改 agent 代码与阈值。漏检是发现，不是要在本 change 里修的 bug。
6. 只评估一次；录制失败（网络、CLI）才允许重跑同一条命令，记入 evidence，不为更好的结果重录，不改动任何已有 cassette。
7. 事后发现的标准答案错误，写成 errata，原文不动，数字按原文。

## Decision rule

无论结果好坏，原样记录。出现任何 `unsafe_allow` 放在报告第一位。结果不触发任何 agent 改动；之后修了哪一条，这个世界对该条即被污染，需要再造新世界。

## Capabilities

### New Capabilities
- `k8s-fresh-heldout`：主线 K8s 的预注册、只评估一次的新世界及其评估入口。

## Impact

新增 `testenv/heldout/k8s_fresh_*.yaml`、`testenv/test_k8s_fresh.py`、`eval/k8s_heldout.sh`、新 cassette；`testenv/k8s_world.py` 只加新 profile 种类；`eval/EXPERIMENTS.md` 只追加一节；`eval/score_k8s.py` 已有 `--world`，不改。不改 agent 代码、阈值、已有 cassette、README、Makefile。AWS 与 kubectl 都不涉及。
