# c0045 验收证据

## 冻结

- 冻结提交（含世界、SLO 目录、生成器扩展、测试）：`abda3c37ca0d1e670b893c1507381caa110f0cce`
- `testenv/heldout/k8s_fresh4_world.yaml` sha256 `05ef97dae2c202f510c20fcfc12659e4ceb950f371d2740f78797997b46b8a09`（git blob `4b34d9e02c9556eb3dcd4ee727666720076e6f6c`）
- `testenv/heldout/k8s_fresh4_slos.yaml` sha256 `f2466b2cc1d7e3449b5f1349c98abf2f016577333d4a0477850bbdf736eb58d5`
- 冻结后世界与目录不再改动。本 change 没有运行 agent，也没有运行任何评分器，没有评估结果。

## 验收命令

```
$ python3 -W ignore -m unittest discover -t . -s testenv -p 'test_*.py'
Ran 151 tests in 51.487s
OK
$ python3 tools/check_rules.py && python3 tools/check_rules.py --docs
check_rules: OK
check_rules: OK
```

基础世界序列 sha256 不变：`test_k8s_fresh4.GeneratorExtensionTest.test_base_world_series_unchanged` 断言 `c13c2fa6b443cac8e6d095e36c7f04b0c26ffc4108d191fda5ed316141a12822`（与 test_k8s_fresh2 同一摘要，生成器加 `daily_burst` 前后相同）。

指标服务实起并抽查（空闲端口）：

```
$ python3 -m testenv.metrics_server --world testenv/heldout/k8s_fresh4_world.yaml --port <free>
kube_deployment_spec_replicas{workload="fraud-scorer"}  -> 4.0
slo_burn_rate_1h{workload="settle-api"}                 -> 1.7478
```

## 世界概况

14 个 workload，`nodes.count` = 14（r6i.xlarge，allocatable 3.9 core / 29.5 GiB，0.252 USD/h）。FFD 节点数对六种排序键都是 14。11 个 workload 带 `hard_category`，8 个类别：data_quality、traffic_shape（2）、autoscaler_coupling、slo_situation（2）、packing（2）、lifecycle、request_direction、time_regime。

`realizable_usd_month` 由作者的装箱模拟在 request_range 两端与中点、六种排序键下复算，全部一致（测试 `test_realizable_ranges_match_single_change_packing`）。一个节点 = 183.96 USD/月。

## 阈值不可见的说明

`config/k8s_policy.yaml` 里的数值（`max_throttle`、`oom_guard`、`deadline_guard`、`min_budget`、`window_days`、B 的 margin）按协议不读。世界按「离任何合理取值都足够远」构造：节流 0.3 以上、批任务最坏一次在 deadline 的 0.98、没有依赖窗口长度的事件（空洞与过期尾部在 14 天与 21 天窗口下结论相同，最坏的一次 job 在 as_of 前约 9 天，落在 14 天窗口内）。这是作者的假设，不是对阈值的了解。

## 盲写协议自查

1. 只读允许清单：kept。读了 c0027 proposal / spec / tasks、c0031 spec、c0043 specs、c0036 proposal / tasks / specs、`testenv/k8s_world.py`、`k8s_world.yaml`、`k8s_slos.yaml`、`metrics_server.py`（头部与参数部分）、`test_k8s_fresh2.py`、`tools/check_rules.py`（头部）、finops-change-loop skill、仓库 CLAUDE.md。
2. 禁读清单：kept。没有打开 `finops_agent/`、`config/`、`eval/`、`testenv/heldout/` 已有文件、`test_k8s_fresh.py`、`test_k8s_fresh3.py`、README、docs、notes、reports、runs、任何 `evidence.md`、禁止的 change 目录，没有 `git log` / `git show`（`git status`、`git diff --stat` 只看本分支改动）。
   诚实记录两处无意接触：(a) `ls openspec/changes` 与 `ls testenv testenv/heldout` 列出了目录名，其中 c0037 的目录名（fix-heldout-misses）暗示此前有过未命中的 workload，没有看到是哪些；(b) 会话启动时 harness 附带的 gitStatus 是外层 context-infrastructure 仓库的提交标题，与本仓库评估无关。
3. 不运行 agent 与评分器：kept。
4. `expect` 来自契约与 SRE 判断：kept。唯一一条判断分歧是 thumbnail-svc cpu（标 `judgment_conflict: true`，range 服从契约，verdict 服从 A–D）。契约没有规定、由作者判断的点：reconcile-nightly 的 C 看窗口内最坏一次 job（契约写的是 `job_last_duration_seconds` 最大值）；edge-gateway 的 B 先于 HPA 规则（契约规则顺序）。
5. 冻结后不改：kept。冻结提交之后只写 evidence、报告、笔记与 tasks 勾选。
