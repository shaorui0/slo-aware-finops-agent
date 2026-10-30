# 0042 k8s-fresh3-freeze 笔记

- 第三个盲世界：修复落地前写成并冻结，修复后只评一次；被用来发现 bug 的世界不能再度量修复
- 盲写的价值全在「没被 agent 当前的错误引导」：只读契约、生成器与世界 schema，不读 agent 代码、配置、评估结果
- 难点按 SRE 经验选，不按 agent 弱点选：每 pod 倾斜、近期水平突变、数据空洞、隐藏 throttling、HPA 顶 max、SLO 指标不等于 SLO 定义、内存贴 limit 无 OOM、内存尾部尖峰、差一天的历史
- 反直觉：平均利用率 40% 的容器可以每个周期有四分之一被 throttle（limit 等于 request，突发短于 5 分钟均值）
- 反直觉：HPA 顶到 max 时 verdict 是 NOOP 而不是 REQUIRE_APPROVAL，契约的判定顺序里 NOOP 在 HPA 规则之前
- 一个 first-fit decreasing 的坑：排序键不同，节点数可差 1，甚至降配后反而多一个节点；realizable 只写两种排序都成立的那一条（结论为 0）
- 契约公式与判断冲突只有 1 处（promo-renderer/cpu），verdict 取判断，range 取契约
- 自查时发现越线：读了 c0036 evidence 的前 80 行，里面有第一个世界的一行聚合指标，如实记进 evidence
- 没有运行任何评估
PROGRESS_ROW: | c0042-k8s-fresh3-freeze | 10-30 | 第三个盲写预注册 K8s 世界（12 个 workload，9 类难点）并冻结 | 盲写世界的价值取决于作者没有被 agent 当前的错误引导 | [报告](reports/0042-k8s-fresh3-freeze.html) · [笔记](notes/blog/0042-k8s-fresh3-freeze.md) |
