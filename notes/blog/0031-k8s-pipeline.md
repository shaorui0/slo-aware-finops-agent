# c0031 K8s 主线串联

- 16/18 到 18/18 不靠调阈值，靠把 SLO 定义挪到它该在的地方。batch-nightly 的 deadline 不是指标，塞进指标标签是在让 Metrics 去替代 SLO。做成第三个独立输入（目录文件）后，条件 C 自然能判，「三者不能互相替代」从口号变成了一个会失败的测试：workload 不在目录里，即使有 slo_* 指标，C 也是 UNKNOWN。
- 目录给「目标」，指标给「现在烧了多少」。两者分工清楚，证据里也分来源（`slo_catalog` 与 `prometheus`）。
- LLM 只写叙述，不碰 verdict。落地靠两层：数字走现成的 G1–G9，含未取自证据数字的句子整句抹掉；再加一层，句子里声称另一个 verdict 的也抹掉。UNKNOWN 既是 verdict 又是条件状态，这个词不能一刀切。
- 隔离调用的代价是每条决策一次调用：18 条约 0.69 USD、约 14.8 万 token 输入。换来的是 prompt 里没有别的决策，叙述不会串味。
- 真实模型第一轮 18 条里有 1 条 JSON 嵌套错位。严格解析拒绝它，没有「修一修」。这是 record 与 replay 分开的价值：线上降级成模板，CI 只回放最终盒带，缺一条就非零退出。
- 评分闸多加了一条：对每条叙述重跑数字落地检查，不是只信流水线当时的校验。
- 台账用决策的 as_of 当时间，不用墙钟，三天模拟才可重放。去重时保留旧目标而不是跟着新推荐走：approval 文件按 plan_id 命名，目标每天漂 1% 就会让批准失效。
- 计划里的 `before` 只写被改的那一个资源。同一 workload 的 cpu 与 memory 两个计划如果都写整个容器，先执行的那个会让后一个的 `before` 作废，act 会判状态漂移。
- halted 期间只报告：不出计划、不新建、不过期。冻结比继续推进安全，代价是别的 workload 的提案也暂停，直到人工清除。
- 连续运行里最有意思的不是漂移，是 burning-slo：第 1 天它因燃烧事件尚未发生而是 ALLOW，第 2 天 DENY，两条提案过期。台账的价值就在这里：昨天的建议今天不能再用。
- 导读页测试 `test_index_lists_every_change` 在 c0029 合并后就是红的，因为导读页要在合并时重新生成。按约束不碰它，交给主 agent。

PROGRESS_ROW: | c0031-k8s-pipeline | 10-26 | K8s 主线串联：SLO 目录作为第三输入 → collect → decide → 隔离 LLM 叙述（盒带回放、数字落地）→ 计划 → 提案台账；`make k8s-eval` 进 CI（18/18 判对、unsafe_allow 0），`make k8s-days` 三天连续运行 | deadline 属于 SLO 定义而不是指标，补上第三输入就从 16/18 到 18/18；LLM 只写叙述，句子级抹除而不是信任 | [报告](reports/0031-k8s-pipeline.html) · [笔记](notes/blog/0031-k8s-pipeline.md) |
