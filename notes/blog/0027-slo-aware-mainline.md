# c0027 改名与收窄：SLO-aware FinOps Agent

- 铺开 12 类 AWS 浪费，看起来很全，其实没有一项做到「能动手、能验证」。换成只做 CPU/Memory requests，反而能把 Decide → Act → Verify 讲透。
- 核心一句：Metrics 是实际发生了什么，SLO 是业务允许什么，Requests 是调度器预留多少。三者不能互相替代，缺一个就是 UNKNOWN，不能拿另一个去补。
- 先写契约再分波：世界 schema、指标名、决策 JSON、计划、台账都钉死，后面几个 agent 才能并行，不用互相等。
- requests 降了不等于省钱。只有节点数降下来才是真省，所以每条推荐要同时报两种口径：账面释放的预留，和装箱后能兑现的金额。
- 判定顺序本身就是设计：C 失败（正在烧预算）优先于数据不足，因为服务退化时不该动，不管数据多不多。
- 旧的广度扫描不删，冻结成附录：它的评估、陷阱和预注册方法仍是证据，只是不再当主线指标。

PROGRESS_ROW: | c0027-slo-aware-mainline | 10-26 | 改名 SLO-aware FinOps Agent；主线收窄到 K8s CPU/Memory requests；接口契约；12 类 AWS 冻结为附录 | 做深一项比铺开十二项更能证明能力；三者分离是判定的根 | [报告](reports/0027-slo-aware-mainline.html) · [笔记](notes/blog/0027-slo-aware-mainline.md) |
