## Why

c0027 把主线收窄到 K8s CPU/Memory requests，并钉死了决策 JSON、条件 A–D 和判定顺序。现在需要把这块确定性内核真正写出来：从 Prometheus 取数、算推荐、算节省、过策略，输出 `k8s_decisions.json`。后面的判断层、执行、报告、评估（c0030 起）都读这个文件，所以它必须可测、可复现，LLM 不参与 verdict。

## What Changes

- 新增 `finops_agent/k8s/collect.py`：只用契约里的指标名，按 (workload, resource) 建 facts，每个数字附一条 evidence `{id, query, window, value, source}`，调用记入现有 `Trace`。
- 新增 `finops_agent/k8s/recommend.py`：cpu 取 p99 乘 (1+cpu_headroom) 向上取整到 cpu_step；memory 取窗口内最大 working set 乘 (1+mem_headroom) 向上取整到 mem_step；不低于当前值为 NOOP。
- 新增 `finops_agent/k8s/packing.py`：first-fit decreasing 装箱，算出 `realizable_usd_month` 与 `request_value_usd_month`，确定性，同分按名字打破。
- 新增 `finops_agent/k8s/policy.py`：条件 A–D、六步判定顺序、HPA 联动、`unknown_needs`；顶层 `decide(facts_list, cluster, policy)` 与 `write_decisions(path, as_of, world, decisions)`。
- 新增 `config/k8s_policy.yaml`：所有阈值集中在这一个文件。
- 测试：每条判定规则一个最小 fixture，含优先级、packing-trap、NOOP、确定性。

## 不做什么

- 不写判断层（narrative 恒为 null）、不写执行计划、不写台账、不写报告和评估。
- 不依赖 `testenv/k8s_world.*`，collect 用桩传输层测。

## Impact

只新增文件，不改现有代码。对集群与 AWS 全部只读，不涉及人工 gate。DoD 对应 K2。
