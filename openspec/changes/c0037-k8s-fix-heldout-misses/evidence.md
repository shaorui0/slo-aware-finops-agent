# c0037 验收证据

所有输出为原样，本机路径前缀已去掉。第一个预注册世界已被这次修复污染，§8.5 的数字只证明修复生效。

## 修了什么、怎么证明

| 项 | 代码 | 单测 |
|---|---|---|
| 趋势守卫（B 子项，仅内存） | `finops_agent/k8s/collect.py`（`daily_trend`）、`policy.py`（`cond_b`）、`config/k8s_policy.yaml` | `test_policy.TrendGuardTest`（上升 FAIL、平稳 PASS、下降无 FAIL、请求已覆盖外推、未触发的高外推被忽略）；`test_collect.TrendCollectTest`（含 cpu 默认关闭、可开启、噪声序列、点数不足） |
| 按容器过滤 | `collect.py`（`resource_measures`） | `test_collect.ContainerFilterTest`（sidecar 夹具：sidecar 把未指名容器的查询放大 10 倍，决策里的 usage / request / limit 不变；所有相关查询都带 `container="app"`；无 `container` 标签时选择器不变） |
| 定价通道 | `pipeline.py`（`pricing_from`） | `test_pipeline`：世界有 `pricing` 覆盖、缺省回落、世界定价翻倍时 `realizable_usd_month` 精确翻倍 |

## 基础世界：verdict、推荐值、节省金额、条件状态与理由逐条不变

模板叙述下，修复前（c0037 前一个提交的代码）与修复后各跑一次基础世界，比较每条决策的 verdict、推荐值、savings、usage、条件状态、reasons：

```
old == new: True (18 decisions)
```

所以 `eval/k8s_baseline.json` 没改（基础世界没有数字变化）。变化的只有证据列表里的选择器（多了 `container=`），因此叙述提示词变了，盒带重录。

```
$ make k8s-eval
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0}
n={'expected': 18, 'decisions': 18, 'unknown_expected': 2, 'ranged': 8, 'realizable': 2} narrative={'llm': 18}
gates: pass
```

## 其他验收命令

```
$ make k8s-days
== k8s-day1 (day 1, base world, as_of 2026-10-25T00:00:00Z) ==
   new=10  unchanged=0  expired=0   plans=10  verdicts={'ALLOW': 7, 'DENY': 4, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
== k8s-day2 (day 2, base world, one day later, as_of 2026-10-26T00:00:00Z) ==
   new=0  unchanged=8  expired=2   plans=8  verdicts={'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
== k8s-day3 (day 3, drift_day3: api-steady load +60%, as_of 2026-10-28T00:00:00Z) ==
   new=1  unchanged=7  expired=1   plans=8  verdicts={'ALLOW': 5, 'DENY': 6, 'NOOP': 2, 'REQUIRE_APPROVAL': 3, 'UNKNOWN': 2}
== api-steady/cpu: ledger history ==
$ make k8s-verify
k8s verify [control] after_clean.yaml: predicted 140.16 realized 140.16 bill delta -140.16 closure diff -2.84e-14
runs/k8s-verify/k8s_verify.json
wrote runs/k8s-verify/k8s_verify.json and docs/K8S_SAVINGS_VERIFICATION.md
$ make eval   # appendix
{"recall": 1.0, "savings_recall": 1.0, "precision": 1.0, "savings_accuracy": 0.9167, "step_agreement": 1.0, "depth": {"shallow_recall": 1.0, "deep_recall": 1.0}, "leads": 7}
missed: 
gates: pass
$ python3 tools/check_rules.py --docs
check_rules: OK
$ bash eval/k8s_heldout.sh   # replay
GATE FAILED: request_in_range 0.9231 is worse than baseline 1.0
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 0.9231, "realizable_accuracy": 1.0}
n={'expected': 20, 'decisions': 20, 'unknown_expected': 2, 'ranged': 13, 'realizable': 3} narrative={'llm': 20}
```

单测：`python3 -W ignore -m unittest discover -t . -s finops_agent -p 'test_*.py'` 通过（356 个）；`-s eval` 通过（44 个）；`testenv` 下除被盲测的 `test_k8s_fresh2.py` 之外的全部文件通过（该文件没有运行，见下）。`make test` 里 `tools/` 一段只有 `test_build_index.test_index_lists_every_change` 失败：`reports/index.html` 没有本 change 的行；本地 `python3 tools/build_index.py` 后通过，该文件不提交（按约定由维护者整合时生成）。

## 盒带

- 基础世界、`k8s-days`、第一个预注册世界：真实 `claude -p` 重录（`FINOPS_LLM_MODE=record`），共 88 个新盒带；叙述全部来自 LLM，`narrative_ungrounded` 为 0。
- 旧盒带 88 个 `git mv` 到 `eval/cassettes/archive/`，`MANIFEST.md` 列出每个文件上一次被哪个命令引用。没有删除任何文件。
- 过程中有两轮被废弃的重录（第一轮：生成的夹具里一个 12 位十六进制短键恰好全是数字，被泄漏扫描当成账号号；改选择器顺序后整批重录。第二轮：趋势证据改为只在触发时出现），它们的盒带原样放在 `archive/intermediate/` 与 `archive/intermediate_r2/`，`MANIFEST.md` 有说明。

## 第一个预注册世界：修复前后（已污染）

`bash eval/k8s_heldout.sh`（原始输出 `eval_record_output.txt`、`k8s_fresh_decisions.json`、`k8s_fresh_eval.json`）。

| 指标 | 修复前（c0035） | 修复后 |
|---|---|---|
| verdict_accuracy | 0.90（18/20） | 1.0（20/20） |
| unsafe_allow | 1 | 0 |
| unknown_recall | 1.0 | 1.0 |
| request_in_range | 0.7692（10/13） | 0.9231（12/13） |
| realizable_accuracy | 0.6667（2/3） | 1.0（3/3） |

session-store/memory 的 B：

```
使用量持续爬升：每日峰值的最小二乘斜率 0.076798 GiB/天（R² 0.999978），按此外推 14 天后峰值约 3.48859 GiB，推荐 2.9375 GiB 低于 3.48859 × (1+0.1)，先查泄漏或增长
```

逐条前后对比与诚实的限制见 `eval/EXPERIMENTS.md` §8.5。route-quote/cpu 没有修（契约 p99 × 1.3 与 `expect` 的分歧，留给维护者）：它仍是唯一的区间外项。

## 盲测

没有读取、打开或运行 `testenv/heldout/k8s_fresh2_*`、`testenv/test_k8s_fresh2.py`、c0036 的任何产物、`notes/blog/0036-*`、`reports/0036-*`。`testenv` 的测试逐文件运行并跳过 `test_k8s_fresh2.py`；`make test` 的 testenv 一段（`discover -s testenv`）会包含该文件，本次没有用它。

## 没做的

- route-quote/cpu（契约问题）。
- 趋势证据的人话标签：`finops_agent/k8s/report.py` 不在本 change 范围。趋势触发的决策，报告证据表里这两行显示「（未标注）」；基础世界与样例报告不受影响（趋势没有触发，没有证据行）。
- 趋势守卫对 cpu 默认关闭；阈值在两个世界上没有测过灰色地带。
