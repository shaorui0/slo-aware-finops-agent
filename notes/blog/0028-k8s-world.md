# c0028 合成 K8s 世界：先有标准答案，再谈判定

- 先造一个「答案已知」的世界，再让推荐器和策略引擎去考。9 个 workload 各对应一条判定分支，cpu 与 memory 分别给 expect，所有数字都能从服务的指标里重新算出来。
- 测试里自带一份 first-fit decreasing，验证「requests 降了不等于省钱」：packing-trap 的 cpu 降到 0.2 核，节点数仍是 6，因为 8 GiB 的内存把节点占住了；api-steady 同样的降幅却能少一个节点。节点装箱是内存和 CPU 两个维度的事，只看 CPU 会误判。
- 契约里有一处自相矛盾：推荐值 = max ws × 1.2，只要工作集贴近 limit，推荐值就高于当前 request，判定规则第 1 条（NOOP）先命中，memory 的 OOM 守卫永远走不到。mem-near-oom 的 memory 因此做不成「推荐想降、B 来拦」的 DENY。
- 「峰值 100%」不能是 request 的 100%：p99 × 1.3 会高于当前值，同样落入 NOOP。要让「推荐器想降、守卫拦住」成立，高峰必须窄（api-tidal 的尖峰只占 2 个采样点）或者按核数解释（batch 单线程满载 = request 的 50%）。
- 现有 PromQL 子集不支持子查询，`rate()[5m]` 在 5 分钟网格上还是空的（区间左开右闭，只含一个点）。所以多加了一个 `container_cpu_usage_cores` 的 gauge，让 `quantile_over_time(0.99, ...[14d])` 直接可用。
- 确定性生成器每步抽数固定，延长周期不改历史；漂移场景因此只是「把周期延长 2 天再加一个事件」，不需要第二份世界文件。

PROGRESS_ROW: | c0028-k8s-world | 10-26 | 合成 K8s 世界：9 个 workload（cpu/memory 各有 expect）、`--world k8s` 提供契约全部指标、drift_day3 场景、自带 FFD 的装箱事实测试 | 节点装箱是 CPU 与内存两个维度，requests 降了节点数可以纹丝不动；契约里 NOOP 规则会先于 OOM 守卫命中 | [报告](reports/0028-k8s-world.html) · [笔记](notes/blog/0028-k8s-world.md) |
