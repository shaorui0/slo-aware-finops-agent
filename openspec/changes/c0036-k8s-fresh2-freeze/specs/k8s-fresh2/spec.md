# k8s-fresh2：第二个预注册世界

沿用 c0027 的世界 schema 与 c0031 的 SLO 目录，不改契约。

## ADDED Requirements

### Requirement: 第二个预注册世界

`testenv/heldout/k8s_fresh2_world.yaml` 与 `testenv/heldout/k8s_fresh2_slos.yaml` 在修复落地前冻结，之后只评一次。

- 8 到 12 个 workload，名字与基础世界不重复。
- 节点类型与单价不同于基础世界；`nodes.count` 等于对当前 requests 做 first-fit decreasing 的结果。
- 每个 workload 的 `expect` 含 cpu 与 memory，各有 verdict、`request_range`、一行 `why`。
- `request_range` 服从契约公式；与 SRE 判断冲突的条目标 `judgment_conflict: true`，并在 `why` 里写出分歧。
- 至少 4 个 workload 属于自动推荐器难以处理的情形。
- 世界里有 `slo` 块的 workload 恰好是目录里的那些，数值逐项相等。

#### Scenario: 世界自洽
- **WHEN** 加载世界文件
- **THEN** 每个 workload 的两个资源都有 `expect`；`lo <= hi`；`lo` 小于当前 request（NOOP 与带 `range_note` 的条目除外）

#### Scenario: 基础世界不变
- **WHEN** 生成器加入 `trend` 之后构建基础世界
- **THEN** 序列的 sha256 与扩展前相同

### Requirement: 生成器仅加法扩展

`testenv/k8s_world.py` 只新增 profile kind `cpu.trend`，不改既有 kind 的输出。

### Requirement: 盲写

`expect` 在未读取任何 agent 评估结果、agent 代码与策略配置的前提下写成。协议见 proposal。
