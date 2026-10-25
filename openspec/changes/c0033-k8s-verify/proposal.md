## Why

主线到 c0032 为止只回答「该改什么」。维护者真正要的是「改了之后真的省钱了吗」。requests 降了不等于账单降了：只有集群少开节点，或者腾出的容量吸收了本来要新开节点的增长，钱才是真省。没有核实这一步，月报里的「已验证净节省」只能写 0。

附录里的 `verify_savings.py` 已经证明过方法：前后两个窗口、对照组、账单精确分解、偏差原因分类。这次把同样的方法用在 K8s 主线上，口径换成「节点小时 x 节点单价」。

## What Changes

- 新增 `finops_agent/k8s/verify.py`：对一批已执行的计划，比较 BEFORE 与 AFTER 两个窗口的节点小时账单，把账单变化精确分解为五类原因：(a) 变更本身让节点少了、(b) 与变更无关的负载或副本增长、(c) 变更触发的 HPA 扩容、(d) 回滚、(e) 装箱没兑现（request 降了，节点没少）。输出 `runs/<id>/k8s_verify.json`（c0032 契约形状，逐字段一致），旁边另写 `k8s_verify_detail.json`（分解明细与闭合检查）。
- 节点数不手填：每个小时的节点数都由对该小时全部 pod 的 requests 做 first-fit decreasing 得出；HPA 副本数由世界生成的用量按 HPA 公式推出。
- 新增场景覆盖层 `testenv/k8s_scenarios/after_apply.yaml`（应用后 14 天，五种现象齐全）和 `after_clean.yaml`（对照：只有两条干净变更，核实器不应凭空造偏差）。变更、回滚、无关增长都写成世界文件里的带时间事件，不改 `testenv/k8s_world.py` 与基础世界。
- 新增 `scripts/k8s_verify.sh` 与 Makefile 目标 `k8s-verify`：跑决策、跑核实、生成 `docs/K8S_SAVINGS_VERIFICATION.md`（中文，表格，由数据生成，不手改）。

## 人工 gate 与回滚

只读：核实器读决策、计划和场景文件，不写集群，不调 AWS，不执行任何 kubectl。唯一写的是 `runs/k8s-verify/` 与生成的文档。回滚即删除这些文件。

## Capabilities

### New Capabilities
- `k8s-verify`：核实窗口、五类原因的定义与归集顺序、闭合要求、输出形状、场景覆盖层的事件约定。

### Modified Capabilities
（无；`k8s_verify.json` 形状沿用 c0032，场景文件只新增事件类型）

## Impact

新增 `finops_agent/k8s/verify.py`、`test_verify.py`、`testenv/k8s_scenarios/after_*.yaml`、`testenv/test_k8s_scenarios.py`、`scripts/k8s_verify.sh`、`docs/K8S_SAVINGS_VERIFICATION.md`。Makefile 末尾追加一个目标。不改 README、`reports/index.html`、`k8s_world.yaml`、`k8s_world.py`；`k8s-eval` 的数字不变。t7 之后把 `k8s_verify.json` 接进周报与月报。
