# c0029 K8s 决策内核

- 子查询不被测试用的 Prometheus 子集支持，所以 p99 没法从 rate 现算。解法是让世界直接给一个每 pod 的 CPU 使用量 gauge，指标名收成代码里的一个常量。接口要迁就查询能力，而不是反过来。
- 判定顺序里 C 在 A 前面：服务在烧预算时，数据多少都不该动。测试专门钉了「C FAIL 压过 A UNKNOWN」。
- NOOP 必须排在 B 之后：欠配的 workload 推荐值反而高于当前，NOOP 在前就看不见它正在 OOM 或被 throttle。现在这种情况判 DENY，并写明「偏低，不应降配，推荐值仅供人工考虑上调」。
- 条件 D 按字面写（缩容后集群空闲 ≥ 回滚所需）会和真节省打架：节点降下来，空闲就被一并吃掉，D 判 FAIL。字面实现下 ALLOW 里 realizable 大于 0 的几乎出不来。这是契约问题，不是代码问题，已留可选的 `max_nodes` 旋钮并上报。
- 装箱陷阱可以用三个 3.5 core 的 pod 精确构造：每个节点剩 0.5 core，缩容的 pod 进不去，节点数不变，账面省钱但实际一分没省。
- 缺数据一律 UNKNOWN，不补默认值。连 Pending 都是：kube-state-metrics 在没有 Pending 时可能不吐该序列，所以先看别的 phase 是否存在，存在才当 0。
- 确定性靠三件事：按 id 排序、浮点统一四舍五入到 6 位、装箱同分按名字。输入反序后 JSON 逐字节一致。

PROGRESS_ROW: | c0029-k8s-decide | 10-26 | K8s 决策内核：PromQL 取数 → facts → 推荐器 → 装箱节省 → 策略引擎（A–D、六步判定顺序）；51 个单测覆盖每条规则 | 子查询不可用就让世界给每 pod gauge；条件 D 字面实现会与真节省冲突，需契约拍板 | [报告](reports/0029-k8s-decide.html) · [笔记](notes/blog/0029-k8s-decide.md) |
