## MODIFIED Requirements

### Requirement: 策略引擎（条件 B 的近期用量子项）

条件 B 原文（推荐值不低于 CPU p99 / 内存窗口 max 的 (1+`margin`) 倍，throttling、OOM、Pending、内存趋势守卫；任一 FAIL 则 B = FAIL）增加一个子项，**CPU 与内存都适用**：

- **「近期用量」**的定义：该资源在最近 `recent_hours` 小时内的观测最大值 `recent_max`。取法与窗口 max 同口径：`max by (workload)(max_over_time(<usage metric>{<按容器钉住的选择器>}[<recent_hours>h]))` 在 as_of 求值（CPU 用 `container_cpu_usage_cores`，内存用 `container_memory_working_set_bytes`；多个 pod 取最大值）。
- 若 `recommendation.request < recent_max × (1 + margin)`（与 p99、窗口 max 检查同一个容差规则：相等通过），则 B **不得为 PASS**：B = **UNKNOWN**。
  - `why` 含「近期用量高于推荐值」，写出 `recent_hours`、`recent_max`、推荐值与 `margin`。
  - `unknown_needs` 含「近期（最近 N 小时）的用量变化」需要人工确认是一次性还是新常态。
  - 新增一条证据（见下），条件 B 的 `evidence` 引用它。
- 最近 `recent_hours` 小时内没有任何样本（`recent_max` 缺失）：B = UNKNOWN，`unknown_needs` 含「需要最近 N 小时的 <CPU|内存> 使用量数据」。（窗口更长的「最近无数据」仍由条件 A 的 `stale_days` 子项处理。）
- B 里其它子项为 FAIL 时，B 仍是 FAIL（FAIL 优先于 UNKNOWN，`_combine` 的既有规则）。判定顺序不变，所以：B = UNKNOWN 时 verdict = REQUIRE_APPROVAL（C 不为 FAIL、A 为 PASS、D 不为 FAIL、非 NOOP）；NOOP 仍先于 REQUIRE_APPROVAL（推荐值不低于当前 request 就不降配，不需要审批）。
- **为什么是 UNKNOWN 而不是 FAIL。** 近期值高于推荐值说明「14 天统计可能已经过时」，但几小时的数据区分不了「新常态」（上线、流量切换）和「一次性尖峰」（压测、重试风暴）。FAIL 会 DENY，等于 agent 替人判了「这是新常态」；UNKNOWN 落到 REQUIRE_APPROVAL，提案和近期证据一起交给人，由人决定是等数据、上调，还是按旧口径降。这与 A 子项的处理一致：证据不足是 UNKNOWN，证据明确（throttling、OOM、窗口 max 之上）才是 FAIL。
- **内存的说明。** 内存 B 本来比的是窗口 max，近期窗口是它的子集，所以真实数据里 `recent_max ≤ max`，内存推荐（max × (1+`mem_headroom`)）恒不低于 `recent_max × (1+margin)`，子项对内存在正常数据下不会触发；保留它是对称与防御（窗口口径日后改变、或窗口 max 来自不同聚合时仍有守卫），并有合成事实的单测钉住。
- **证据条目。** `{query, window, value}`：`query` 是上面的 `max_over_time` 查询，`window` 为 `<recent_hours>h`，`value` 为 `recent_max`。**只在子项构成判定因素时写入**（推荐值低于 `recent_max × (1+margin)`），否则不写、不改任何文字：其余决策的 decision JSON、证据列表、条件文本与 LLM 提示逐字节不变。日报的 `evidence_labels` 给它中文标签「近期使用量 max（最近 N 小时内的最大值）」。
- 配置：`config/k8s_policy.yaml` 的 `recent_hours`（默认 6，判断值，未对任何世界调参）。键不存在则子项关闭（不取数、不判）。
- **已知限制。** 6 小时到约 3.4 天前发生的阶跃，既不在近期窗口内，又不足 p99 的 1% 样本，本子项抓不到；窗口越长，日周期内的正常高峰越容易被误当作「近期变化」，这是有意的取舍，不在本 change 解决。
- 推荐公式不变（CPU p99 × (1+`cpu_headroom`)，内存 max × (1+`mem_headroom`)）。

#### Scenario: CPU 近期阶跃
- **WHEN** CPU 14 天 p99 = 0.21、推荐 0.3 core，最近 6 小时观测最大 0.63
- **THEN** B = UNKNOWN，`why` 含「近期用量高于推荐值」与 0.63，verdict = REQUIRE_APPROVAL，不是 ALLOW

#### Scenario: 近期用量没有超过推荐值
- **WHEN** `recent_max × (1+margin) ≤ recommendation.request`
- **THEN** 子项不触发，B 的 `why`、证据、decision JSON 与此前逐字节相同

#### Scenario: 其它 FAIL 优先
- **WHEN** 近期子项触发，且高峰期 throttling 超过 `max_throttle`
- **THEN** B = FAIL，verdict = DENY

### Requirement: 评估（危险放行指标 allow_below_recent）

`eval/score_k8s.py` 增加指标与硬闸门：

- `allow_below_recent`：verdict 为 ALLOW 且 `recommendation.request` 低于该 workload 的**真实近期用量**的 decision 个数。真实近期用量 = 最近 6 小时（打分器自己的常量 `RECENT_HOURS`，不读 `config/k8s_policy.yaml`）内各 pod 用量的最大值，**由世界文件用 `testenv/k8s_world.py` 的生成器重新生成**（截至决策文件的 `as_of`），不读 agent 的 decision、证据或任何 agent 数字，也不依赖 `expect`。
- 硬闸门：`allow_below_recent` 必须为 0；`eval/k8s_baseline.json` 记 0，比基线更差即失败（与 `unsafe_allow` 同向）。
- REQUIRE_APPROVAL **不计入**该指标：它前面有人工闸门，人看得到近期证据；只作为信息项 `approval_below_recent`（及 id 列表）输出。理由：该指标衡量「不经人看就放行的错误」，不是「推荐值是否偏低」。
- 世界文件不含可重新生成的序列（单测里的最小世界）时，指标为 null，不参与闸门与基线比较。
- 与 `unsafe_allow` 的区别：`unsafe_allow` 看期望裁决（expect 是 DENY/UNKNOWN 却 ALLOW），本指标看世界的真实用量；两者互补，原有指标与基线语义不变。

#### Scenario: ALLOW 低于近期用量
- **WHEN** 世界里某 workload 近期 CPU 最大 0.63，decision 为 ALLOW、推荐 0.3，而 expect 是 REQUIRE_APPROVAL
- **THEN** `unsafe_allow` = 0（旧指标看不见），`allow_below_recent` = 1，闸门失败

#### Scenario: 同样的推荐值，REQUIRE_APPROVAL
- **WHEN** 同上，但 verdict 为 REQUIRE_APPROVAL
- **THEN** `allow_below_recent` = 0，`approval_below_recent` = 1
