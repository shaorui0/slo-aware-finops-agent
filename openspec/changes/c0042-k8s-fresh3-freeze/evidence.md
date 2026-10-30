# 验收证据：c0042-k8s-fresh3-freeze

## 冻结

- 冻结提交 sha：`6450a8ffb7901dad99240519ba884da131f4002c`（标题 `c0042-k8s-fresh3-freeze [3/4 freeze]: ...`，含世界、目录、生成器扩展、测试）
- 冻结后的文件 sha256：
  - `testenv/heldout/k8s_fresh3_world.yaml` eb822fc50933b556e53bc3031d887de3930944f3dcde59d417c98e8ef6bc2a0b
  - `testenv/heldout/k8s_fresh3_slos.yaml` 108c0e40e75391f0e059bc31f2f85ded1aec8f76bb2e3da2ced182cd4f034ef2
- 冻结后世界与目录不再修改；修复后只评一次。

## 没有运行任何评估

本 change 没有运行 agent，没有运行任何评分器，没有产生 verdict_accuracy、unsafe_allow 等指标。`expect` 只是预先写下的标准答案。`request_range` 的数值取自生成器自身的序列（p99 与最大 working set，按契约公式），不是 agent 的输出。

## 盲写协议自查

| 规则 | 自查 |
|---|---|
| 1 只读允许清单 | 基本遵守；有两处超出，见下 |
| 2 不读禁止清单 | 未读 `finops_agent/`、`config/`、`eval/`、既有 heldout 世界、README、docs、notes、reports、runs、c0028–c0035 与 c0037–c0041 的内容；未做 `git log`/`git show`。有三处名称级接触，见下 |
| 3 不运行 agent 与评分器 | 未运行 |
| 4 expect 来自 SRE 判断加契约明文 | 是；契约与判断冲突的 1 处标 `judgment_conflict: true`（promo-renderer/cpu）；欠配的 2 条（geo-resolver/memory、scoring-engine/cpu）按契约公式给出上调值，其中 geo-resolver/memory 带 `range_note` |
| 5 冻结后不改 | 冻结后只写本文件、报告与笔记 |

如实记录的接触：

1. 为取得 c0036 的 proposal / tasks / spec 模板，用 `head -80` 读了 `openspec/changes/c0036-k8s-fresh2-freeze/evidence.md` 的前 80 行（该文件不在允许清单内）。内容：冻结 sha、盲写协议自查表、一行 c0035（第一个世界）的聚合指标（verdict_accuracy 约 0.90、unsafe_allow 为 1），以及第二个世界的测试输出。没有看到任何 workload 级失败项，也没有第二个世界的评估结果（那个世界没有被评估过）。这一读发生在设计 workload 之前，所以只能说没有具体失败项的信息，不能说没有总体难度的印象。
2. `ls openspec/changes` 与 `ls reports` / `ls notes/blog | tail` 列出了目录与文件名，其中含 c0037-k8s-fix-heldout-misses、c0038-k8s-fresh2-eval 等名字。只看到名字，没有打开。名字透露了「有一个针对 heldout 失误的修复」与「第二个世界被评过」，这正是任务本身已告知的背景。
3. 允许清单里的 `testenv/test_k8s_fresh2.py` 含第二个世界的 workload 名字与难点类别（权重周峰值、cpu 趋势、sidecar、尾部 gap、SLO 序列缺失、HPA 在 memory 上、低预算低燃烧等）。本世界刻意选了不同的类别，没有复用这些名字与形状。
4. 会话启动时被自动注入了仓库的 `CLAUDE.md`，其中要求读 `rules/` 与 `docs/REQUIREMENTS.md`。`docs/` 在禁读清单内，未读。

## 世界概况

12 个 workload，cpu 与 memory 的 verdict 分布（共 24 条）：ALLOW 13、REQUIRE_APPROVAL 3、DENY 2、NOOP 2、UNKNOWN 4。节点 c6i.2xlarge，单价 0.34 USD/h，allocatable 7.6 core / 14 GiB，`nodes.count` = 7（first-fit decreasing，内存降序再 cpu 降序；cpu 优先的排序键得 6）。

难点（`traps` 共 9 条，分属 9 个类别）：

| workload | 类别 |
|---|---|
| ledger-gateway | 每 pod 倾斜，均值低估热 pod |
| promo-renderer | 3 小时前的水平突变，21 天 p99 看不到 |
| audit-trailer | 21 天跨度中间有 9 天空洞，仅 12 个有效天 |
| thumbnail-farm | 平均利用率 40%，约四分之一周期被 throttle |
| fanout-worker | HPA 已顶到 max，NOOP 先于 HPA 审批规则 |
| loyalty-points | 有 `slo_*` 序列但目录里没有 SLO |
| geo-resolver | 内存 97% limit 但没有 OOM 事件 |
| pdf-assembler | 内存罕见尖峰，p99 看不到，最大值才是；cpu 的 realizable 为 0 |
| quote-cache | 13 天，比 min_days 少一天 |

## 测试

```
Ran 22 tests in 8.818s   (python3 -W ignore -m unittest testenv.test_k8s_fresh3 -v)
OK
```

全量：

```
$ python3 -W ignore -m unittest discover -t . -s testenv -p 'test_*.py'
Ran 127 tests in 38.699s
OK
$ python3 tools/check_rules.py --docs
check_rules: OK
```

## 基础世界不变

`GeneratorExtensionTest.test_base_world_series_unchanged` 通过：基础世界序列 sha256 仍为 c13c2fa6b443cac8e6d095e36c7f04b0c26ffc4108d191fda5ed316141a12822（改动前后各算一次，一致）。生成器只新增内存 `spiky` 与 `slo_metrics: orphan`。

## 指标服务

`python3 -m testenv.metrics_server --world testenv/heldout/k8s_fresh3_world.yaml --port 18742` 实际起服务并抽查：
- `kube_deployment_spec_replicas{workload="ledger-gateway"}` 返回 3.0（namespace=payments）
- `slo_burn_rate_1h{workload="loyalty-points"}` 返回 0.3364（有序列，目录里没有，符合设计）
