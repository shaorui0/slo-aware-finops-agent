## Why

c0027 的契约要求 `report.py` 用同一份数据出日报、周报、月报。现在决策 JSON、计划、台账都有了，但没有一份「没参与开发的人每天能读」的东西。维护者的问题是：现在能优化什么，证据是什么，怎么优化，做完应该是什么样，做了哪些分析，证据够不够。报告就是产品界面，所以清晰比完整重要。

## What Changes

- 新增 `finops_agent/k8s/report.py`：一份数据模型，三个中文 HTML 视图（日报、周报、月报）。`render(view, inputs) -> str` 供测试和后续流水线调用；CLI `python3 -m finops_agent.k8s.report --run runs/<id> --view daily|weekly|monthly --out file.html`。
- 日报回答「今天有什么要我处理」：顶部图例、一行摘要、和上次比、每个 ALLOW/REQUIRE_APPROVAL 候选一张六段卡片、被拒与数据不足清单、无需调整折叠列表。safe_halt 时顶部红色横幅。
- 周报回答「上周做的改动真的省了吗、有没有伤到服务」：已执行变更、核实结果（预测 vs 实际）、回滚与 halt、台账流转。
- 月报回答「这套东西值不值得信」：已核实净节省、单位成本、agent 质量指标、考卷成绩的含义与位置。
- 定义并文档化一个新输入 `runs/<id>/k8s_verify.json`（由后续 change 产出），有就渲染，没有就明说。
- 固定样例：`finops_agent/k8s/fixtures/`（由 k8s 合成世界跑 collect+decide 得到的两天决策，加手写的台账、执行记录、核实、考卷）；`scripts/k8s_report_samples.sh` 生成 `reports/k8s-sample/*.html` 并提交，人可以直接打开读。

## 人工 gate 与回滚

只读渲染：不写集群，不调 AWS，不改任何输入文件。唯一写的是 `--out` 指定的 HTML。回滚即删除生成文件。

## Capabilities

### New Capabilities
- `k8s-report`：日报/周报/月报的内容契约、`k8s_verify.json` 输入形状、渲染接口。

### Modified Capabilities
（无；决策、计划、动作记录形状沿用 c0027 / c0030，不改）

## Impact

新增 `finops_agent/k8s/report.py`、`test_report.py`、`fixtures/`、`scripts/k8s_report_samples.sh`、`reports/k8s-sample/`。不改 README、`reports/index.html`、Makefile；流水线（t4）后续自己调用 `render`。
