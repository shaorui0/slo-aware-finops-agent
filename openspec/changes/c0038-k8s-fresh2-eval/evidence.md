# c0038 验收证据

## 冻结完整性（评估前）

```
$ git diff --stat 10eefdb HEAD -- testenv/heldout/k8s_fresh2_world.yaml testenv/heldout/k8s_fresh2_slos.yaml testenv/test_k8s_fresh2.py
（空）
$ shasum -a 256 testenv/heldout/k8s_fresh2_world.yaml
cb493ab62c2ec57b47a8bf8a88f2a10a853627c5f1c8855787cb9f5f0c0d700c
$ git merge-base --is-ancestor 10eefdb HEAD && echo freeze-is-ancestor
freeze-is-ancestor
10eefdb 2026-10-26 01:59:08 +0900 c0036 freeze
ff2931a 2026-10-26 02:26:25 +0900 c0037 第一个实现提交
```

## 评估一次（record，真实 claude -p）

见同目录 `eval_record_output.txt`：

```
k8s run k8s-fresh2: 22 decisions {'ALLOW': 13, 'DENY': 4, 'REQUIRE_APPROVAL': 5}, plans 18, narrative {'llm': 22}
GATE FAILED: unknown_recall 0.0 < 1.0; ...
{"verdict_accuracy": 0.9167, "unsafe_allow": 0, "unknown_recall": 0.0, "request_in_range": 0.9444, "realizable_accuracy": null}
n={'expected': 24, 'decisions': 22, 'unknown_expected': 2, 'ranged': 18, 'realizable': 0} narrative={'llm': 22}
wrong: edge-collector/cpu got None expect UNKNOWN, edge-collector/memory got None expect UNKNOWN
exit=1
```

## 事后核对（只读，不改任何东西）

- edge-collector：`count(kube_deployment_spec_replicas{workload="edge-collector"})` 在 as_of 为空；`count(max_over_time(...[14d]))` 为 1。workload 在窗口内存在，as_of 时刻无数据，所以被瞬时查询发现漏掉。
- session-cache：`max(max_over_time(container_memory_working_set_bytes{workload="session-cache"}[14d]))` = 2.66 GiB；`[28d]` = 4.64 GiB。agent 用 14 天窗口，expect 用全周期。

## 回放复现

评估之后用回放模式重跑一次，结果与 record 一致（见下方命令输出）。
    {"verdict_accuracy": 0.9167, "unsafe_allow": 0, "unknown_recall": 0.0, "request_in_range": 0.9444, "realizable_accuracy": null}
    wrong: edge-collector/cpu got None expect UNKNOWN, edge-collector/memory got None expect UNKNOWN
