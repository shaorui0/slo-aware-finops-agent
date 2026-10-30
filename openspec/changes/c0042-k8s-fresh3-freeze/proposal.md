## Why

前两个预注册世界都已经被用来发现缺陷，缺陷即将被修复。用来发现 bug 的世界不能再用来度量修复，否则是在教考卷。需要第三个盲世界：在修复落地之前写成并冻结，修复之后只评一次。它的价值完全取决于作者没有被 agent 当前做错什么所引导。

## 盲写协议（binding）

作者按下列规则工作，验收时逐条自查（见 evidence.md）：

1. 只读允许清单：c0027 契约、c0031 的 SLO 目录与串联契约、c0036 的 proposal / tasks / spec（作为协议与结构模板）、`testenv/k8s_world.py`、`testenv/k8s_world.yaml`、`testenv/k8s_slos.yaml`、`testenv/metrics_server.py`、`testenv/test_k8s_fresh2.py`、`tools/check_rules.py`、change-loop skill。
2. 不读：`finops_agent/` 与 `config/` 下任何文件；`eval/` 全部（含 EXPERIMENTS.md）；`testenv/heldout/` 下既有的两个世界；README、docs、notes、reports、runs；change 目录 c0028–c0035 与 c0037–c0041；不对这些做 `git log -p` / `git show`，也不看 `git log --oneline`。
3. 不运行 agent（`python -m finops_agent ...`），不运行任何评分器。本 change 不产生任何评估结果。
4. 每条 `expect` 来自 SRE 领域判断加契约明文规则（verdict 顺序与条件 A–D 按 c0027 原文）。契约公式（cpu = p99 每 pod x 1.3 向上取 0.05；memory = 最大 working set x 1.2 向上取 64Mi）与判断冲突时，`request_range` 服从契约，`why` 写明分歧，并标 `judgment_conflict: true`。
5. 世界冻结后不再改动。修复后只评一次，结果原样记录，不回头改 `expect`。

## What Changes

- 新增 `testenv/heldout/k8s_fresh3_world.yaml`：12 个 workload，节点类型 c6i.2xlarge、单价与可分配量都不同于基础世界；其中 9 个是自动推荐器难以处理的情形，分属不同类别（每 pod 倾斜、近期水平突变、数据质量、隐藏的 throttling、HPA 联动与判定顺序、SLO 指标不等于 SLO 定义、内存贴近 limit 但未 OOM、内存尾部尖峰、历史天数差一天）。
- 新增 `testenv/heldout/k8s_fresh3_slos.yaml`：SLO 目录（第三个独立输入）。
- `testenv/k8s_world.py` 仅加法扩展：内存 profile `spiky`（罕见尖峰），`slo_metrics: orphan`（有 `slo_*` 序列但没有 SLO 定义）。既有序列字节级不变。
- 新增 `testenv/test_k8s_fresh3.py`：加载、自洽、目录与世界一致、各难点的形状、指标服务可提供、基础世界不变。
- 冻结提交，记录 sha。

## 人工 gate 与回滚

只写测试夹具与测试，没有 AWS 或 kubectl 操作；回滚是 revert 该分支。

## Capabilities

### New Capabilities
- `k8s-fresh3`：第三个预注册世界。

### Modified Capabilities
（无）

## Impact

只改 `testenv/`、本 change 的目录、一篇博客笔记与一份报告。默认世界、前两个预注册世界与现有测试不变。
