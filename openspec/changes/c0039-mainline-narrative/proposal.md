## Why

主线（c0027–c0038）已经做完，但 README、NARRATIVE、MATURITY 仍以广度扫描为主体，对外叙事和仓库现状不一致（DoD K11）。

## What Changes

- README 以主线为主体：快速开始、我怎么思考、第一部分（三类输入、四个条件与判定顺序图、日报长什么样、核实、四个世界的成绩单）、第二部分（主线架构图与分层、原则）。原第一部分与原 2.1 移到 `docs/APPENDIX_BREADTH.md`。
- NARRATIVE：主线八项能力声明（主张、证据、边界）与主线成绩单在前；原十二项与原成绩单作为附录证据保留。
- MATURITY：K1–K10 写入复核证据；K11、K12 写明待什么。
- BUILD_PROCESS：第二次调度运行一节。
- 报告：趋势守卫的两条证据加中文标签（c0037 留下的「未标注」），加单测。

## Impact

文档为主；代码只改 `finops_agent/k8s/report.py` 的标签映射，不影响任何判定与评分。
