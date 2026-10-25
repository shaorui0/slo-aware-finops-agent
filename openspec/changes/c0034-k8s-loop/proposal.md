## Why

c0030（执行）、c0031（流水线、台账、计划）、c0032（报告）各自能跑，但还没有接成一条线：执行完不回写台账，流水线不出报告，报告读的台账与考卷形状是当时猜的，演示用的是手写计划而不是流水线生成的计划。一次真实运行应该给读者留下全部东西：决策、计划、执行记录、台账流转、三份报告。这个 change 把环接上，并把报告打磨到没参与开发的人也能读懂每个数字的含义。

## What Changes

- `act.py` 在每个结局后写台账：plan 过了审批闸门开始改时 proposed → approved → applied；观察期过关 → verified；命中停止条件并回滚成功 → rolled_back；`refused` 不动状态；`failed`（回滚没恢复）停在 applied，交给人。执行记录里多一个 `ledger` 字段说明写了什么（写失败不改变执行结果，但会写明）。
- `act.py` 的漂移闸门只比较计划涉及的资源（计划 `before` 里写了的 key），因为生成的计划只带 cpu 或 memory 一种资源，不该因为另一种资源不同而误判漂移。
- `k8s run` 在 `runs/<id>/` 额外写 `k8s_daily.html`、`k8s_weekly.html`、`k8s_monthly.html`。可选 `--prev-run`（上一次 run 目录，日报对比用）、`--score`（考卷 JSON）；`k8s_actions.jsonl`、`k8s_verify.json` 在 run 目录里有就读。
- `report.py` 对齐真实形状：台账是 `proposals[].status` 与 `history[].status`；考卷是 `eval/score_k8s.py` 的 `k8s_eval.json`；计划是 `{run_id, as_of, safe_halt, plans[]}`；`narrative` 是对象，在「结论」里渲染，标明「由模型撰写、数字已核对」，`would_change` 渲染为「什么情况下判定会变」。
- 报告打磨：证据表每行带人话标签（E8 是「有效天数」，E9 是「覆盖率」）；不显示与该 workload 无关或为空的证据（非批任务的 job 时长、空值）；每张卡片的「做完应该是什么样」第一句直接说钱动不动、为什么。
- `collect.py` 给证据条目加 `label`（只加字段，不改任何数值，`make k8s-eval` 数字不变）。
- `k8s-demo` 改用流水线生成的计划：先对合成世界跑 `k8s run`，从 `k8s_plans.json` 取 shop/api-steady 的 ALLOW 计划（cpu 走回滚路径，memory 走 verified 路径），demo 清单的 request/limit/副本数/容器名与世界里的 api-steady 一致，因此不需要改写 `before`；不一致时才改写并大声打印。演示结束渲染含真实 `k8s_actions.jsonl` 的周报，并打印台账流转。
- `reports/k8s-sample/*.html` 改为真实运行产物：k8s-days 的 day1/day2、真实台账、`k8s-eval` 的考卷、demo 的真实执行记录（拷进夹具以便复现）。t6 的 `k8s_verify.json` 不在本分支，沿用夹具里的核实文件并注明。

## 停止条件怎么处理（throttle 来自 limit，不来自 request）

c0030 的 demo 里，回滚是被 CPU limit 压到 200m 引起的 throttle 触发的。生成的计划只改 request，而 CFS throttle 由 limit 决定，所以降 request 本身不会让 `throttle_ratio` 变高。两条路：(a) 计划策略上也降 limit；(b) 停止条件靠 p99 延迟。

选 (b)。理由：降 limit 会改变 c0027–c0031 已经固定的计划契约和策略（plans.py、policy.py 不在本 change 范围），而且「省钱」靠的是 request，limit 不省钱，凭空多加一个风险动作不对。p99 是 SLO 里本来就有的、用户能感知的信号，计划里已经有 `p99_seconds > SLO` 这一条，任何金丝雀期内的退化（不管原因）都会触发回滚，这正是停止条件该有的语义。

诚实的边界：在一台 12 核空闲的机器上，单独把 request 从 1 降到 0.25 并不会让延迟变差。demo 的回滚路径靠的是「观察期内注入压垮服务的负载，p99 超过 SLO」，证明的是「守卫在退化时会触发并且回滚到变更前状态」，不是「降 request 导致了退化」。报告和 evidence 里都这样写，不把它说成因果。throttle 的停止条件保留在计划里，demo 里不会触发，不假装测过。

## 人工 gate 与回滚

- 改集群的唯一入口仍是 `act.py`：审批文件由人创建，agent 不会创建；上下文只能是 `k3d-finops-test`；漂移就拒绝；停止条件命中自动回滚并核对资源等于变更前。本 change 不放宽任何闸门。
- 台账写入只记录结局，不会触发任何动作；`failed` 不会被当成成功。
- 本 change 的演示只用集群 `finops-test`，结束后 `make k8s-down` 删除；从不碰 `cre6630`。
- 回滚本 change：revert 这几个 commit；`runs/` 下的产物都是生成物。

## Capabilities

### New Capabilities
- `k8s-loop`：执行回写台账、流水线出三份报告、demo 用生成的计划、报告的真实输入形状。

### Modified Capabilities
- `k8s-act`（c0030）：多了台账回写与按涉及资源比较的漂移闸门。
- `k8s-pipeline`（c0031）：多了三份 HTML 输出和 `--prev-run` / `--score`。
- `k8s-report`（c0032）：输入形状对齐真实文件；narrative 对象渲染；证据标签。

## Impact

`finops_agent/k8s/{act,report,pipeline,collect,ledger}.py` 与测试、`finops_agent/__main__.py`（k8s 子命令）、`finops_agent/k8s/fixtures/`、`scripts/k8s_demo.sh`、`scripts/k8s_report_samples.sh`、`ops/k3d/`、`reports/k8s-sample/`。不改 README、`reports/index.html`、Makefile。
