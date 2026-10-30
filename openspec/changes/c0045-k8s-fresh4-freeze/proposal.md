## Why

前几个预注册世界已经被评估过，其中暴露的缺陷正在被修复。用来发现 bug 的世界不能再用来度量修复，否则是在教考卷。修复正在另一个 worktree 并行编写，所以需要第四个盲世界：在不知道 agent 目前错在哪里的前提下写成并冻结，修复合入后只评一次。它的价值完全取决于作者没有被任何结果引导。

## 盲写协议（binding）

本 change 的作者按下列规则工作，验收时逐条自查（见 evidence.md）：

1. 只读：c0027 契约、c0031 与 c0043 的 specs（c0043 只读契约增量：发现窗口与 `stale_days`，不读其 proposal 与 evidence）、c0036 的 proposal / tasks / specs（模板）、`testenv/k8s_world.py`、`testenv/k8s_world.yaml`、`testenv/k8s_slos.yaml`、`testenv/metrics_server.py`、`testenv/test_k8s_fresh2.py`、`tools/check_rules.py`、`finops-change-loop` skill。
2. 不读：`finops_agent/`、`config/`、`eval/` 的任何内容；`testenv/heldout/*` 里已有的世界；`test_k8s_fresh.py`、`test_k8s_fresh3.py`；README、docs、notes、reports、runs；任何 `evidence.md`；c0028–c0035、c0037–c0042、c0044 及之后的 change；任何 `git log` / `git show`。
3. 不运行 agent，不运行任何评分器。本 change 不产生评估结果。
4. 每条 `expect` 来自 SRE 领域判断加契约明文规则。契约公式（cpu = p99 每 pod × 1.3 向上取 0.05；memory = 最大 working set × 1.2 向上取 64Mi）与判断冲突时，`request_range` 服从契约，`why` 写明分歧，标 `judgment_conflict: true`。契约没有规定的地方（例如阈值的具体数值在 `config/` 里，作者看不到），世界按「离任何合理阈值都足够远」来构造，并在 `why` 里说明。
5. 世界冻结后不再改动。修复后只评一次，结果原样记录，不回头改 expect。

## What Changes

- 新增 `testenv/heldout/k8s_fresh4_world.yaml`：14 个 workload，节点类型与单价不同于基础世界；其中 11 个是自动推荐器难处理的情形，分属 8 个类别，每类至多 2 个。
- 新增 `testenv/heldout/k8s_fresh4_slos.yaml`：SLO 目录。
- `testenv/k8s_world.py` 仅加法扩展：cpu profile `daily_burst`（每天定时的短脉冲）。既有 kind 的输出字节级不变。
- 新增 `testenv/test_k8s_fresh4.py`：加载、自洽、目录一致、节点数与 realizable 的装箱复算、各难点形状、指标服务可提供、基础世界不变。
- 冻结提交，记录 sha。

## 人工 gate 与回滚

只写测试夹具与测试，没有 AWS 或 kubectl 操作；回滚是 revert 该分支。

## Capabilities

### New Capabilities
- `k8s-fresh4`：第四个预注册世界。

### Modified Capabilities
（无）

## Impact

只改 `testenv/` 与本 change 的文件。默认世界、已有预注册世界与现有测试不变。
