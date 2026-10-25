## Why

c0035 在预注册的新世界上暴露了 agent 一侧的三类缺陷（`eval/EXPERIMENTS.md` §8.4）：一条 unsafe ALLOW（内存持续爬升被放行）、sidecar 污染用量、定价输入通道不通。这个 change 修这三条。第一个预注册世界（c0035）因为它的失败驱动了这次修复，此后只能当回归测试，不能再当泛化证据；泛化要靠另一个更晚冻结的世界（由别人评估一次）。

## What Changes

1. **趋势守卫（条件 B 增加一个子项）。** 取窗口内每天的最大 working set（`max by (workload)(max_over_time(<metric>{...}[1d]))`，1d 步长的 range query），做最小二乘线性回归；按拟合线外推 `trend_horizon_days`（默认 14 天）得到预测峰值，若 `预测峰值 × (1+margin) > 推荐 request` 则 B FAIL（verdict DENY），理由用中文并写明斜率、R²、预测值。触发时证据新增两条：斜率（GiB/天）与外推峰值；未触发的序列不加证据条目。只有同时满足「斜率为正、R² ≥ `trend_min_r2`、窗口内拟合涨幅 ≥ `trend_min_rise`、点数 ≥ `trend_min_points`」才触发，避免昼夜与周周期的噪声造成误判。
2. **按容器过滤。** 用量（p50/p95/p99/max、逐小时序列）、throttling、requests、limits 的选择器加 `container="<name>"`，容器名取自本轮早先 `kube_deployment_spec_replicas` 的 `container` 标签。没有该标签时沿用旧选择器。
3. **定价通道。** `--nodes-from <world.yaml>` 同时读取 `pricing` 块，按键覆盖 `config/k8s_policy.yaml` 的默认牌价；没有 `pricing` 时回落到默认，行为与现在相同。
4. 阈值全部进 `config/k8s_policy.yaml`；契约的 B 增加趋势子项（见 specs 增量）。

## 趋势守卫是否也用于 cpu：只用于内存，配置可开

选择：默认 `trend_resources: [memory]`。理由：

- 内存不可压缩。泄漏或持续增长的后果是 OOMKilled 与重启，且推荐值（窗口峰值 × 1.2）一周内可被追上。CPU 可压缩，超出 request 时先表现为 throttling，而 B 已有「高峰期 throttling 比例」守卫，能在后果发生的第一时间抓到，代价是延迟而不是进程被杀。
- CPU 的每日最大值受流量周期（工作日 / 周末、周一峰值）支配，14 天窗口刚好两个周期，窗口起点相位不同就会拟合出虚假斜率。误报的代价是放弃一次合法降配，在 cpu 上这个误报面比内存大得多。
- 真实的 CPU 持续增长（业务增长）对容量规划重要，但它的正确处理是规划口径（预测需求），不是降配前的安全门；本 change 不扩展到那里。
- 代码对资源没有硬编码：把 `cpu` 加进 `trend_resources` 即启用同一逻辑，单测覆盖了开启与关闭两种配置。

## 不修：route-quote/cpu

§8.4 把它归因为「契约规则（p99 × 1.3）与 `expect`（按尖峰定容）的分歧」，不是实现错误。要不要让推荐值或 B 看 p99.9 / 窗口最大值，会改变基础世界里已验证的推荐值，属于契约问题，留给维护者决定，本 change 不动。session-store/memory 的推荐值（2.9375）也不改：趋势守卫让它 DENY，但推荐公式本身未动，所以 `request_in_range` 对这一条仍可能不命中，如实记录。

## 盲测与污染

- 不读、不运行 `testenv/heldout/k8s_fresh2_*`、`testenv/test_k8s_fresh2.py` 及 c0036 的任何产物。修复只依据 §8 分析与 SRE 判断。
- 第一个预注册世界（`k8s_fresh_world.yaml`）在修复后重跑，结果记入 `eval/EXPERIMENTS.md` §8.5，并标注「已污染，不能当成泛化证据」。

## 人工 gate 与回滚

纯本地、只读：不触 AWS、不触集群。回滚：revert 本 change 的 commit；盒带只新增（旧盒带 git mv 到 `eval/cassettes/archive/`），可复原。

## Capabilities

### Modified Capabilities
- `k8s-mainline`（c0027）：条件 B 增加趋势子项；取数按容器过滤。
- `k8s-pipeline`（c0031）：`--nodes-from` 同时读取 `pricing`。
