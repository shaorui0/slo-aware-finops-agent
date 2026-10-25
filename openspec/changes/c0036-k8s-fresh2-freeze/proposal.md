## Why

第一个预注册世界已经被评估过，暴露了真实缺陷，这些缺陷即将被修复。用来发现 bug 的世界不能再用来度量修复，否则是在教考卷。需要第二个盲世界：在修复之前写成并冻结，修复落地后只评一次。

## 盲写协议（binding）

本 change 的作者按下列规则工作，验收时逐条自查：

1. 不读第一个预注册世界的 yaml 与 SLO 目录、`eval/EXPERIMENTS.md`、c0035 的 change 目录、博客笔记与报告。
2. 不读 `finops_agent/k8s/` 下任何代码，不读 `config/k8s_policy.yaml`。推荐器与策略引擎的行为只通过契约（c0027、c0031 的 spec）得知。
3. 不运行 agent，不运行 `eval/score_k8s.py`。本 change 不产生任何评估结果。
4. 每条 `expect` 来自 SRE 领域判断加契约明文规则。契约公式（cpu = p99 x 1.3 向上取 0.05，memory = 最大 working set x 1.2 向上取 64Mi）与判断冲突时，`request_range` 服从契约，在 `why` 里写明分歧，并标 `judgment_conflict: true`。
5. 世界冻结后不再改动。修复后只评一次，结果原样记录，不回头改 expect。

作者在盲写开始后做过一次越线的无意接触：`git log --oneline` 的提交标题里带有 c0035 的一行评估汇总指标（verdict_accuracy 与 unsafe_allow 的聚合数字）。没有看到是哪些 workload 失败，也没有打开对应文件。这一点在验收里如实记录。

## What Changes

- 新增 `testenv/heldout/k8s_fresh2_world.yaml`：12 个 workload，换节点类型与单价，覆盖易与难的混合。
- 新增 `testenv/heldout/k8s_fresh2_slos.yaml`：SLO 目录（第三个独立输入）。
- `testenv/k8s_world.py` 仅加法扩展：新增 cpu profile `trend`（线性漂移）。现有序列字节级不变。
- 新增 `testenv/test_k8s_fresh2.py`：加载、自洽、目录与世界一致、指标服务可提供、基础世界不变。
- 冻结提交，记录 sha。

## 人工 gate 与回滚

只写测试夹具与测试，没有 AWS 或 kubectl 操作；回滚是 revert 该分支。

## Capabilities

### New Capabilities
- `k8s-fresh2`：第二个预注册世界。

### Modified Capabilities
（无）

## Impact

只改 `testenv/` 与本 change 的文件。默认世界、第一个预注册世界与现有测试不变。
