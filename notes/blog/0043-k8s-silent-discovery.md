- 「缺数据要显式」不只是条件里写 UNKNOWN：如果 workload 在发现阶段就消失，所有下游的 UNKNOWN 逻辑根本没机会运行。沉默发生在最上游。
- 瞬时查询的发现口径有隐含假设：「现在还有样本」。采集挂了、scrape 丢了，正是最该报告的情形，却被这个假设吞掉。
- 修法很小：发现用窗口 `max_over_time(...[14d])`，条件 A 多一个「最近 N 天无数据」子项；判定顺序一个字不改，A 非 PASS 自然落到 UNKNOWN，永远不会是 ALLOW。
- 有数据的 workload 的决策逐字节不变，所以 LLM 盒带不用重录：新文字只在触发时写，不加证据条目。
- 台账不用改：过期 workload 现在是 UNKNOWN，走的是已有的「不再是可提案」路径；「workload 已不在指标里」那条路径反而成了死代码的候选。
- 做不到的：窗口内一个样本都没有的 workload，任何窗口查询都发现不了。「下线」与「采集挂了」在指标侧不可区分，要外部清单对照。
- 权衡：正常下线的 workload 会在日报里挂 14 天 UNKNOWN。宁可多一行，也不沉默。
- 先写测试再改代码，修复前的失败输出留在 evidence 里；夹具刻意模仿 Prometheus（瞬时查询查不到、窗口查询查得到），否则测试在修复前也会过。
- 第二个预注册世界对这个缺陷已污染，只当回归，没有拿它调参；`stale_days = 3` 是判断出来的，不是扫出来的。

PROGRESS_ROW: | c0043-k8s-silent-discovery | 10-30 | 修「静默丢 workload」：发现改窗口口径，条件 A 增加「最近 N 天无数据」子项（`stale_days`），过期 workload 判 UNKNOWN 并进日报；基础世界决策与盒带不变，unsafe_allow 0 | 缺数据要显式要从发现阶段开始，沉默发生在最上游 | [报告](reports/0043-k8s-silent-discovery.html) · [笔记](notes/blog/0043-k8s-silent-discovery.md) |
