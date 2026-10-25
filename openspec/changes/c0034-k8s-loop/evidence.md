# c0034 验收证据（原始输出）

验收口径见 tasks.md 第 5 节。下面的输出都是在本分支上实际跑出来的，只删去了重复的 `Waiting for deployment` 轮询行和观察期里中间的几次采样。

## 1. 单测

```
$ python3 -m unittest finops_agent.k8s.test_act.LedgerTest finops_agent.k8s.test_demo_manifest -v
test_verified_moves_ledger_through_approved_applied_verified ... ok   （台账 proposed → approved → applied → verified）
test_rollback_moves_ledger_to_rolled_back ... ok                       （… → applied → rolled_back）
test_refused_leaves_ledger_alone ... ok                                （refused 不动状态，记录里没有 ledger 字段）
test_failed_after_apply_stays_applied ... ok                           （回滚没恢复：停在 applied，交给人）
test_missing_proposal_does_not_change_outcome ... ok                   （台账写不进去只在 ledger 字段说明，不改执行结果）
test_no_state_given_is_reported_not_fatal ... ok
test_cli_default_state_is_under_root ... ok
test_drift_only_on_touched_resources ... ok                            （漂移闸门只比较计划涉及的资源）
test_manifest_equals_the_worlds_api_steady ... ok                      （demo 清单 == 合成世界的 api-steady）
test_generated_plans_match_the_manifest_without_adaptation ... ok
test_adapt_plan_is_silent_when_equal ... ok
test_adapt_plan_is_loud_and_restores_the_demos_own_value ... ok
Ran 12 tests in 0.109s
OK
```

```
$ python3 -W ignore -m unittest discover -t . -s finops_agent -p 'test_*.py'   （含 test_report 28 条、test_pipeline 9 条）
  P-F03 [commands] reversal=medium $3.60/mo  idle_public_ip
  P-F06 [commands] reversal=low $20.00/mo  gp2_to_gp3
  P-F07 [advice_only] reversal=high $0.00/mo  savings_plan_underutilized

$ python3 -W ignore -m unittest discover -t . -s eval -p 'test_*.py'      Ran 44 tests ... OK
$ python3 -W ignore -m unittest discover -t . -s testenv -p 'test_*.py'   Ran 62 tests ... OK
$ python3 -m unittest discover -s hooks -p 'test_*.py'                    Ran 6 tests ... OK
```

`make test` 整体退出码为 2，唯一失败是已知例外：`tools/test_build_index.py` 的 `test_index_lists_every_change` 报 `c0034-k8s-loop missing from index`（`reports/index.html` 缺本 change 的行，按任务要求不提交该文件；`make test` 在这一步中断，所以后面三个 discover 是手动分别跑的，结果如上）。

## 2. make k8s-eval（数字不变）

```
$ make k8s-eval
runs/k8s-local/k8s_decisions.json
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0}
n={'expected': 18, 'decisions': 18, 'unknown_expected': 2, 'ranged': 8, 'realizable': 2} narrative={'llm': 18}
gates: pass
```

verdict_accuracy 1.0、unsafe_allow 0、unknown_recall 1.0、request_in_range 1.0、realizable_accuracy 1.0，与 c0031 相同。证据标签放在 report.py，没有动 collect.py：证据条目会进叙述提示词，加字段会让 cassette 失效。

## 3. make k8s-days、make eval、check_rules

```
$ make k8s-days
bash scripts/k8s_days.sh
== k8s-day1 (day 1, base world, as_of 2026-10-25T00:00:00Z) ==
   new=10  unchanged=0  expired=0   plans=10  verdicts={'ALLOW': 7, 'DENY': 4, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
   new       shop/api-steady/cpu            ALLOW，目标 0.25 core
   new       shop/api-steady/memory         ALLOW，目标 1.125 GiB
   new       shop/burning-slo/cpu           ALLOW，目标 0.15 core
   new       shop/burning-slo/memory        ALLOW，目标 0.75 GiB
   new       shop/hpa-managed/cpu           REQUIRE_APPROVAL，目标 0.45 core
   new       shop/hpa-managed/memory        ALLOW，目标 1.125 GiB
   new       shop/mem-near-oom/cpu          ALLOW，目标 0.1 core
   new       shop/packing-trap/cpu          ALLOW，目标 0.2 core
   new       tools/no-slo/cpu               REQUIRE_APPROVAL，目标 0.2 core
   new       tools/no-slo/memory            REQUIRE_APPROVAL，目标 1 GiB
== k8s-day2 (day 2, base world, one day later, as_of 2026-10-26T00:00:00Z) ==
   new=0  unchanged=8  expired=2   plans=8  verdicts={'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
   expired   shop/burning-slo/cpu           本轮 verdict 为 DENY，不再是可提案
   expired   shop/burning-slo/memory        本轮 verdict 为 DENY，不再是可提案
== k8s-day3 (day 3, drift_day3: api-steady load +60%, as_of 2026-10-28T00:00:00Z) ==
   new=1  unchanged=7  expired=1   plans=8  verdicts={'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
   expired   shop/api-steady/cpu            目标值变化超过容差：ALLOW 0.25 -> ALLOW 0.35
   new       shop/api-steady/cpu            ALLOW，目标 0.35 core
== api-steady/cpu: ledger history ==
   P-K-shop-api-steady-cpu-0.25       expired   first_seen 2026-10-25T00:00:00Z  last_seen 2026-10-26T00:00:00Z
      k8s-day1  proposed  ALLOW，目标 0.25 core
      k8s-day3  expired   目标值变化超过容差：ALLOW 0.25 -> ALLOW 0.35
   P-K-shop-api-steady-cpu-0.35       proposed  first_seen 2026-10-28T00:00:00Z  last_seen 2026-10-28T00:00:00Z
      k8s-day3  proposed  ALLOW，目标 0.35 core
```

```
$ make eval
{"recall": 1.0, "savings_recall": 1.0, "precision": 1.0, "savings_accuracy": 0.9167, "step_agreement": 1.0, "depth": {"shallow_recall": 1.0, "deep_recall": 1.0}, "leads": 7}
missed: 
gates: pass
$ python3 tools/check_rules.py --docs
check_rules: OK
```

## 4. make k8s-demo：全新集群，端到端（仅 finops-test）

先 `make k8s-down` 删掉旧集群，再 `make k8s-demo` 从零创建，总耗时 3 分 48 秒。以下是 `runs/k8s-demo/demo.log` 的关键部分。

### 4.1 流水线生成计划，demo 清单与合成世界一致，不需要改写 before
```
=== [3] k8s run against the synthetic metrics server: decisions, plans, ledger, three reports
k8s run k8s-demo (as_of 2026-10-26T00:00:00Z): 18 decisions {'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}, plans 8, ledger {'new': 8, 'unchanged': 0, 'expired': 0, 'halted': 0, 'approved': 0, 'suppressed'
runs/k8s-demo/k8s_decisions.json
generated plans for shop/api-steady (verdict ALLOW both): P-K-shop-api-steady-cpu-0.25  P-K-shop-api-steady-memory-1.125
  K-shop-api-steady-cpu: ALLOW  1 -> 0.25 core
  K-shop-api-steady-memory: ALLOW  2 -> 1.125 GiB
check: does the demo deployment equal what the plans assumed?
P-K-shop-api-steady-cpu-0.25: demo deployment == synthetic world for ['cpu'] and replicas, no adaptation needed
P-K-shop-api-steady-memory-1.125: demo deployment == synthetic world for ['memory'] and replicas, no adaptation needed
--- ledger after the run (nothing is approved yet)
ledger: 8 proposals, safe_halt=off
  proposed    P-K-shop-api-steady-cpu-0.25           proposed
  proposed    P-K-shop-api-steady-memory-1.125       proposed
  proposed    P-K-shop-hpa-managed-cpu-0.45          proposed
  proposed    P-K-shop-hpa-managed-memory-1.125      proposed
  proposed    P-K-shop-mem-near-oom-cpu-0.1          proposed
  proposed    P-K-shop-packing-trap-cpu-0.2          proposed
  proposed    P-K-tools-no-slo-cpu-0.2               proposed
  proposed    P-K-tools-no-slo-memory-1              proposed
```

### 4.2 两次拒绝，集群和台账都没动
```
=== [4] refusal 1: no approval file
[act 17:10:58] outcome=refused reason=approval file missing: runs/approvals/P-K-shop-api-steady-cpu-0.25.approved
exit=3 (3 = refused)

=== [5] refusal 2: wrong context (copy of the plan edited)
[act 17:10:58] outcome=refused reason=context 'k3d-wrong-context' != k3d-finops-test
exit=3 (3 = refused)
resources unchanged: {"limits":{"cpu":"2","memory":"3Gi"},"requests":{"cpu":"1","memory":"2Gi"}}
--- ledger unchanged by refusals
ledger: 8 proposals, safe_halt=off
  proposed    P-K-shop-api-steady-cpu-0.25           proposed
  proposed    P-K-shop-api-steady-memory-1.125       proposed
  proposed    P-K-shop-hpa-managed-cpu-0.45          proposed
  proposed    P-K-shop-hpa-managed-memory-1.125      proposed
  proposed    P-K-shop-mem-near-oom-cpu-0.1          proposed
  proposed    P-K-shop-packing-trap-cpu-0.2          proposed
  proposed    P-K-tools-no-slo-cpu-0.2               proposed
  proposed    P-K-tools-no-slo-memory-1              proposed
```

### 4.3 过载时执行 memory 计划：p99 超过 SLO，自动回滚，台账 applied → rolled_back
```
=== [7] overload the service during the canary (2 load generators x 200 rps), apply the MEMORY plan: expect stop condition p99 and rollback
note: the plans only lower REQUESTS. A lower request does not slow this service down on an idle node (and CPU throttling comes from the
      LIMIT, which no plan changes, so the throttle stop condition stays quiet). The guard that fires is p99 > SLO: any regression inside
      the canary window rolls the change back, whatever caused it. The overload is injected to show that; it is not caused by the lower request.
# INTENT: set loadgen to 200 rps x 2 generators to drive the canary
deployment.apps/loadgen env updated
# INTENT: run 2 load generator(s)
deployment.apps/loadgen scaled
deployment "loadgen" successfully rolled out
metrics under overload before patch: http_request_duration_p99_seconds 0.7434 cpu_throttle_ratio 0.0000 http_requests_total 1042 container_cpu_cfs_periods_total 168 container_cpu_cfs_throttled_periods_total 0 
[act 17:11:22] gates passed for P-K-shop-api-steady-memory-1.125; applying
[act 17:11:22] ledger P-K-shop-api-steady-memory-1.125 -> applied
[act 17:11:22] outcome=applied reason=patch applied, observing
[act 17:11:25] observe {'p99_seconds': 0.0883, 'restarts': 0, 'oom_killed': 0}
[act 17:11:31] observe {'p99_seconds': 0.7506, 'restarts': 0, 'oom_killed': 0}
[act 17:11:31] STOP condition hit: {'signal': 'p99_seconds', 'op': '>', 'value': 0.3, 'observed': 0.7506}; rolling back
[act 17:11:36] ledger P-K-shop-api-steady-memory-1.125 -> rolled_back
[act 17:11:36] outcome=rolled_back reason=stop condition p99_seconds > 0.3 (observed 0.7506); resources verified equal to before
exit=4 (4 = rolled_back)
resources after rollback: {"limits":{"cpu":"2","memory":"3Gi"},"requests":{"cpu":"1","memory":"2Gi"}}
--- ledger
ledger: 8 proposals, safe_halt=off
  proposed    P-K-shop-api-steady-cpu-0.25           proposed
  rolled_back P-K-shop-api-steady-memory-1.125       proposed -> approved -> applied -> rolled_back
  proposed    P-K-shop-hpa-managed-cpu-0.45          proposed
  proposed    P-K-shop-hpa-managed-memory-1.125      proposed
  proposed    P-K-shop-mem-near-oom-cpu-0.1          proposed
  proposed    P-K-shop-packing-trap-cpu-0.2          proposed
  proposed    P-K-tools-no-slo-cpu-0.2               proposed
  proposed    P-K-tools-no-slo-memory-1              proposed
```

### 4.4 恢复正常负载后执行 cpu 计划：观察 120 秒无停止条件，台账 applied → verified
```
=== [8] back to normal load (5 rps), apply the CPU plan: expect verified
# INTENT: set loadgen to 5 rps x 1 generators to drive the canary
deployment.apps/loadgen env updated
# INTENT: run 1 load generator(s)
deployment.apps/loadgen scaled
deployment "loadgen" successfully rolled out
[act 17:12:00] gates passed for P-K-shop-api-steady-cpu-0.25; applying
[act 17:12:01] ledger P-K-shop-api-steady-cpu-0.25 -> applied
[act 17:12:01] outcome=applied reason=patch applied, observing
[act 17:12:06] observe {'throttle_ratio': 0.0, 'p99_seconds': 0.0121, 'restarts': 0, 'oom_killed': 0}
[act 17:12:11] observe {'throttle_ratio': 0.0, 'p99_seconds': 0.0121, 'restarts': 0, 'oom_killed': 0}
[act 17:12:16] observe {'throttle_ratio': 0.0, 'p99_seconds': 0.0134, 'restarts': 0, 'oom_killed': 0}
[act 17:14:09] ledger P-K-shop-api-steady-cpu-0.25 -> verified
[act 17:14:09] outcome=verified reason=observed 120s without a stop condition; live resources match the patch
exit=0 (0 = verified)
resources after canary: {"limits":{"cpu":"2","memory":"3Gi"},"requests":{"cpu":"250m","memory":"2Gi"}}
final metrics: http_request_duration_p99_seconds 0.0199 cpu_throttle_ratio 0.0000 http_requests_total 210 container_cpu_cfs_periods_total 563 container_cpu_cfs_throttled_periods_total 0
```

### 4.5 台账与执行记录
```
=== [9] ledger: the proposals that moved, and the actions
ledger: 8 proposals, safe_halt=off
  verified    P-K-shop-api-steady-cpu-0.25           proposed -> approved -> applied -> verified
  rolled_back P-K-shop-api-steady-memory-1.125       proposed -> approved -> applied -> rolled_back
--- k8s_actions.jsonl (time, outcome, ledger, reason)
2026-10-25T17:10:58+00:00  P-K-shop-api-steady-cpu-0.25       refused     ledger=-           approval file missing: runs/approvals/P-K-shop-api-steady-cpu-0.25.approved
2026-10-25T17:10:58+00:00  P-K-shop-api-steady-cpu-0.25       refused     ledger=-           context 'k3d-wrong-context' != k3d-finops-test
2026-10-25T17:11:22+00:00  P-K-shop-api-steady-memory-1.125   applied     ledger=applied     patch applied, observing
2026-10-25T17:11:36+00:00  P-K-shop-api-steady-memory-1.125   rolled_back ledger=rolled_back stop condition p99_seconds > 0.3 (observed 0.7506); resources verified equal to 
2026-10-25T17:12:00+00:00  P-K-shop-api-steady-cpu-0.25       applied     ledger=applied     patch applied, observing
2026-10-25T17:14:09+00:00  P-K-shop-api-steady-cpu-0.25       verified    ledger=verified    observed 120s without a stop condition; live resources match the patch

=== [10] render the reports again, now with the real k8s_actions.jsonl
wrote runs/k8s-demo/k8s_daily.html runs/k8s-demo/k8s_weekly.html runs/k8s-demo/k8s_monthly.html
```

### 4.6 结果
```
=== [11] summary
DEMO OK: refusals, rollback and verified path all behaved; the ledger followed
```

`make k8s-down` 之后 `k3d cluster list` 只剩 `cre6630`（本 change 没有碰它，它处于停止状态）。

## 5. 样例报告

`bash scripts/k8s_report_samples.sh` 重新生成 `reports/k8s-sample/{daily,weekly,monthly}.html`，三个文件均不含本机绝对路径（对本机路径前缀的 grep 计数为 0）：
- 日报：真实运行的 day2 对 day1，台账是 k8s-days 前两天的台账；
- 周报：day2 决策 + 上面这次 demo 的真实 `k8s_actions.jsonl` 与台账（拷进 `finops_agent/k8s/fixtures/demo/`，可复现）；
- 月报：加上 `make k8s-eval` 同口径的考卷分数（`eval.score_k8s` 对 day2 的输出）。
- t6 的 `k8s_verify.json` 不在本分支。周报与月报里的「核实结果」仍用 `fixtures/day2/k8s_verify.json`，那是手写夹具；每一行带 `note`，报告会把「手写夹具，不是真实核实结果」显示在核实表下面。
- 周报里的台账状态与执行记录一致：cpu 计划 已提案 → 已批准 → 已应用 → 已核实，memory 计划 → … → 已回滚；两次被闸门拦下的尝试单独列出。

## 6. 停止条件：诚实的边界

- 计划只降 request。CFS throttle 由 limit 决定，demo 里 `throttle_ratio` 全程 0，所以 throttle 这个停止条件没有被测到，计划里保留它。
- 回滚由 `p99_seconds > 0.3` 触发（SLO 目录里 api-steady 的 p99 目标）。在空闲机器上，单独降 request 并不会让延迟变差；demo 的回滚路径靠的是观察期内注入过载（2 个压测器 × 200 rps），证明的是「守卫在退化时触发并回滚到变更前」，不是「降 request 导致退化」。
- 为了让过载如实反映在 p99 上，`ops/k3d/app.py` 改成单 worker（一次处理一个请求，其他的在锁上排队，延迟从排队前开始计）。之前的版本在 GIL 下接不进去的连接堵在内核 backlog 里，p99 在 saturation 下也只有 0.07 秒。第一次 demo（尚未改 app）两条计划都走了 verified，没有触发回滚，这就是改 app 的原因。
