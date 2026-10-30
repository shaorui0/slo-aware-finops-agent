## Why

第三个预注册世界（c0044，`eval/EXPERIMENTS.md` §10）暴露了一个窗口代表性缺陷：promo-renderer 的 CPU 在窗口最后 3 小时翻了三倍（一次上线）。14 天 p99 = 0.212 core 描述的是旧状态，窗口 max = 0.636，推荐值 0.3 core 只有当前用量的一半，verdict 却是 ALLOW。

根因在条件 B（`finops_agent/k8s/policy.py` 的 `cond_b`）：

1. CPU 一侧只拿推荐值和 p99 × (1+margin) 比，再看 throttling；内存一侧比的是窗口 max，CPU 没有这一层。
2. 趋势守卫只对内存启用，且看的是逐日最大值的斜率，几小时的阶跃进不了它。
3. 打分器 `unsafe_allow` 只统计「期望 DENY/UNKNOWN 却 ALLOW」，这条期望是 REQUIRE_APPROVAL，所以一次把 request 降到当前用量一半的 ALLOW 没有计入任何危险指标。

## What Changes

1. **条件 B 增加「近期用量」子项（两种资源都适用）。** 取最近 `recent_hours`（新键，默认 6）小时内的观测最大值（按容器、各 pod 取最大）。推荐值低于它 × (1+`margin`) 时，B = **UNKNOWN**（不是 FAIL），`why` 写明近期值，`unknown_needs` 写明需要人工确认这是一次性还是新常态。该窗口没有样本也是 UNKNOWN。
   - 选 UNKNOWN 而不是 FAIL 的理由写在 specs：几小时的数据分不出「新常态」和「一次性尖峰」，FAIL（DENY）等于替人做了判断；UNKNOWN 落到 REQUIRE_APPROVAL，正好是人类 SRE 会做的：「用量刚变，数据只有几小时，你来看」。
2. **新增证据条目**（查询 + 窗口 + 值），只在子项构成判定因素时写入（推荐值低于近期值 × (1+margin)），其余决策的证据列表、条件文本、LLM 提示逐字节不变，盒带不需重录。`report.py` 的 `evidence_labels` 给它中文标签「近期使用量 max（最近 N 小时内的最大值）」。
3. **打分器新增硬闸门 `allow_below_recent`**：ALLOW 且推荐 request 低于该 workload 的**真实近期用量**。近期用量由打分器自己从世界文件（`testenv/k8s_world.py` 的生成器）重新生成，用自己的窗口常量，不读 agent 的任何数字，也不依赖 `expect`。REQUIRE_APPROVAL 不计入（前面有人工闸门），只作为信息项 `approval_below_recent` 报告。基线 `eval/k8s_baseline.json` 增加该项（0）。
4. **样本内用例**：`testenv/k8s_scenarios/recent_step.yaml`（基础世界 + 一个 CPU 在最后 3 小时翻三倍的 workload）。测试先写，修复前 ALLOW（失败输出存 evidence），修复后 REQUIRE_APPROVAL 且 B 引用近期值。

## 不做

- 不改推荐公式（p99 × 1.3 / max × 1.2）：这个 change 只管「能不能放行」，不改「推荐多少」。
- 不重跑、不对照第三个世界调参：它对这个缺陷已污染。`recent_hours = 6` 是判断出来的，不是扫出来的。证据里若提到 promo-renderer 会标注「污染」，不作为修复的证据。
- 不新增预注册世界（EXPERIMENTS §10.5 第 3 条：修完需要第四个预注册世界，那是后续 change）。

## 已知限制

近期窗口只有 6 小时：6 小时到约 3.4 天之前发生的阶跃，既不在近期窗口里，也还没超过 p99 的 1% 门槛，这个子项抓不到。这是有意的取舍（窗口越长，日周期里的正常高峰越容易被当成「近期变化」），记在 specs 里。

## 人工 gate 与回滚

纯本地、只读，不触 AWS / 集群。回滚：revert 本 change 的 commit（配置键删除后子项自动关闭：没有 `recent_hours` 则不取数、不判）。

## Capabilities

### Modified Capabilities
- `k8s-mainline`（c0027）：条件 B 增加近期用量子项；评估增加 `allow_below_recent` 指标与闸门。
