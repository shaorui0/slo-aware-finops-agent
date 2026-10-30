# k8s-fresh4：第四个预注册世界

沿用 c0027 的世界 schema、c0031 的 SLO 目录与 c0043 的发现窗口增量，不改契约。

## ADDED Requirements

### Requirement: 第四个预注册世界

`testenv/heldout/k8s_fresh4_world.yaml` 与 `testenv/heldout/k8s_fresh4_slos.yaml` 在修复落地前冻结，之后只评一次。

- 10 到 14 个 workload，名字与基础世界不重复。
- 节点类型、单价与 allocatable 不同于基础世界；`nodes.count` 等于对当前 requests（含 sidecar）按内存降序、再按 cpu 降序做 first-fit decreasing 的结果；测试同时断言换用其他排序键结果相同（本世界对排序键稳健）。
- 每个 workload 的 `expect` 含 cpu 与 memory，各有 verdict、`request_range`、一行 `why`；确定的条目加 `realizable_usd_month`（只应用这一条变更时释放的节点数 × 节点单价 × 730，在 request_range 的两端与中点、各排序键下都一致）。
- `request_range` 服从契约公式；与 SRE 判断冲突的条目标 `judgment_conflict: true`，并在 `why` 里写出分歧。
- 至少 6 个 workload 带 `hard_category`，类别不少于 6 种，每类至多 2 个。
- 世界里有 `slo` 块的 workload 恰好是目录里的那些，数值逐项相等。

#### Scenario: 世界自洽
- **WHEN** 加载世界文件
- **THEN** 每个 workload 的两个资源都有 `expect`；`lo <= hi`；`lo` 小于当前 request（NOOP 与带 `range_note` 的条目除外）

#### Scenario: realizable 可复算
- **WHEN** 对带 `realizable_usd_month` 的条目，单独把该资源 request 改到范围内任一点并重做装箱
- **THEN** 释放的节点数 × 节点单价 × 730 落在声明的区间内

#### Scenario: 基础世界不变
- **WHEN** 生成器加入 `daily_burst` 之后构建基础世界
- **THEN** 序列的 sha256 与扩展前相同

### Requirement: 生成器仅加法扩展

`testenv/k8s_world.py` 只新增 profile kind `cpu.daily_burst`，不改既有 kind 的输出。

### Requirement: 盲写

`expect` 在未读取任何 agent 评估结果、agent 代码与策略配置的前提下写成。协议见 proposal。
