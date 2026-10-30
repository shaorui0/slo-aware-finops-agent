# c0043 验收证据

## 1. 修复前：测试失败（实现 commit 之前跑的）

测试 `finops_agent/k8s/test_stale_discovery.py` 先于实现写成。stale 夹具 `late`：窗口前段有样本，最后样本距 as_of 5 天（`stale_days` = 3）。夹具按 Prometheus 的行为：as_of 的瞬时查询查不到它，窗口查询（`max_over_time(...[14d])`）能查到。

```
$ python3 -m unittest finops_agent.k8s.test_stale_discovery
ERROR: test_stale_workload_is_unknown_with_an_explicit_no_data_need
ERROR: test_template_narrative_renders_without_none
FAIL: test_daily_report_lists_it_in_the_unknown_table
AssertionError: 'shop/late' not found in "...<tr><td>shop/web ... UNKNOWN 数据不够 ..."   (UNKNOWN 表里只有 web)
FAIL: test_stale_workload_is_discovered_for_both_resources
AssertionError: Lists differ: ['K-shop-api-cpu', 'K-shop-api-memory', 'K-shop-web-cpu', 'K-shop-web-memory'] != [... 'K-shop-late-cpu', 'K-shop-late-memory' ...]
Ran 7 tests in 0.009s
FAILED (failures=2, errors=2)
```

（ERROR 是因为决策根本不存在，`StopIteration`。`test_never_allow...` 修复前空转通过，因为 late 不存在；它的作用是修复后钉住「A 之外全部完美也不能 ALLOW」。）

## 2. 修复后

```
$ python3 -m unittest finops_agent.k8s.test_stale_discovery testenv.test_k8s_world.StaleScenario
Ran 9 tests in 2.413s
OK
```

端到端（合成指标服务器，`testenv/k8s_scenarios/stale_workload.yaml` = 基础世界 + `edge-sync`，最后 5 天无样本）：

```
K-ops-edge-sync-cpu    UNKNOWN 最近 3 天无数据（最后一个样本在 5 天前）；缺少当前 request；有效天数 9 不足 14；样本覆盖率 0.65 低于 0.90
K-ops-edge-sync-memory UNKNOWN 最近 3 天无数据（最后一个样本在 5 天前）；缺少当前 request；有效天数 9 不足 14；样本覆盖率 0.65 低于 0.90
unknown_needs[0]: 最近 3 天无数据（最后一个样本在 5 天前）：需要恢复CPU使用数据的采集，或确认该 workload 已下线
```

日报（`k8s_daily.html`，`--narrate template`）「被拒与数据不足」表里：

```
ops/edge-sync  UNKNOWN 数据不够  CPU   A、C、D  A 最近 3 天无数据（最后一个样本在 5 天前）；缺少当前 request；…
ops/edge-sync  UNKNOWN 数据不够  内存  A、C、D  A 最近 3 天无数据（最后一个样本在 5 天前）；缺少当前 request；…
```

## 3. 闸门

```
$ make k8s-eval
k8s run k8s-local (as_of 2026-10-25T23:55:00Z): 18 decisions {'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}, plans 8, ... narrative {'llm': 18}
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0}
gates: pass
```

`narrative {'llm': 18}` 在回放模式下通过，说明提示词没变、盒带没缺（基础世界的决策、证据逐字节不变）。

```
$ make k8s-days    # exit 0
day1 new=8 ... | day2 new=0 unchanged=8 expired=2 | day3 new=1 unchanged=7 expired=1   （与修复前相同）
$ make check       # check_rules: OK
$ make test        # 仅 1 项失败，见下
```

`make test`：唯一失败 `tools/test_build_index.py::test_index_lists_every_change`：导读页要求每个 openspec change 在 README 进度表里出现，而 README 由主 agent 集成（本任务不许改）。加上 `PROGRESS_ROW` 后即通过；其余全部通过。

## 4. 台账

`test_open_proposal_expires_when_the_workload_goes_stale`：open 提案遇到同 key 的 UNKNOWN 决策，status = expired，备注含「不再是可提案」，不含「不在指标」。`ledger.py` 无需改动。

## 5. 盲测与污染

没有读取或运行 `testenv/heldout/k8s_fresh2_*` 与 `testenv/test_k8s_fresh2.py` 的评分；没有以它为目标调参。没有触碰 `k8s_fresh3*`。`stale_days = 3` 取自「一个采集周期故障的常见长度」的判断，没有对任何世界做扫描。
