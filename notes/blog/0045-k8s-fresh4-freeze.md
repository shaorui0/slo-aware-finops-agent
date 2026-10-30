- 第四个盲世界：不知道 agent 错在哪里，才能度量修复。作者能做的只有从自己的 SRE 经验里想「真实集群里什么东西最容易判错」。
- 先定类别再造 workload：数据质量、流量形状、自动扩缩耦合、SLO、装箱、生命周期、request 方向、时间制度，各至多两个，防止同一类题刷分。
- 最有意思的几个：中间有 10 天空洞（跨度 21 天，有效 11 天）；HPA 已顶到 max 且节流（B 先于 HPA 规则，所以是 DENY 不是 REQUIRE_APPROVAL）；批任务均值和上一次都正常，只有最坏一次到 deadline 的 98%；12 小时前的流量台阶被窗口平均淹没；sticky 路由下 pod 均值掩盖热点 pod。
- 装箱：realizable 不要凭感觉写，用 FFD 在 range 两端与中点、六种排序键下复算，一致才写进 expect。结果反直觉：最大的内存削减 realizable 为 0，0.6 core 的小削减能腾出一个节点（节点是 cpu 瓶颈，且 2.0+1.8 能并排）。
- 阈值在 config/ 里看不到，所以造数据时离任何合理阈值都远一点，并让结论不依赖窗口长度（14 天还是 21 天都一样）。
- 契约公式与判断冲突只有一处（cron 脉冲低于 p99）：range 服从契约，why 写分歧。冲突少不是坏事，说明契约基本讲得通。
- 生成器只加了一个 kind（daily_burst），基础世界 sha256 不变；其余难点全用既有的 gaps、load、per_pod_spread、slo_burn、hpa 拼出来。
- 无意接触要如实记：ls 出来的目录名也会泄漏信息。
PROGRESS_ROW: | c0045-k8s-fresh4-freeze | 10-30 | 第四个预注册（盲写）K8s 世界：14 个 workload、11 个难点分属 8 类，FFD 复算 realizable，冻结后只评一次 | 第四个盲世界：不知道 agent 错在哪里，才能度量修复 | [报告](reports/0045-k8s-fresh4-freeze.html) · [笔记](notes/blog/0045-k8s-fresh4-freeze.md) |
