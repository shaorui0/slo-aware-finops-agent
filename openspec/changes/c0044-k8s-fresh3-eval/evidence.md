# c0044 验收证据

结论：评估一次，结果原样记录。verdict_accuracy 0.9583（23/24），unsafe_allow 0，unknown_recall 1.0（4/4），request_in_range 1.0（16/16），realizable_accuracy 1.0（1/1）。闸门失败（低于基线 1.0），退出码 1。唯一失分 promo-renderer/cpu：期望 REQUIRE_APPROVAL，实际 ALLOW，推荐值只有当前用量的一半；按打分器定义不算 unsafe，按后果算。详见 `eval/EXPERIMENTS.md` §10。

## 提交顺序更正

proposal 写的是「修复之前盲写并冻结」。核对时间戳后更正：c0043 的实现提交 `b5de59f`（22:47）比冻结 `6450a8f`（22:50）早 3 分钟，两边在两个 worktree 并行。能主张的是互不可见，以及合并顺序先世界后修复，不是冻结早于修复。

## 冻结完整性（评估前）

```
$ git diff --stat 6450a8f HEAD -- testenv/heldout/k8s_fresh3_world.yaml testenv/heldout/k8s_fresh3_slos.yaml testenv/test_k8s_fresh3.py
（空）
$ shasum -a 256 testenv/heldout/k8s_fresh3_world.yaml testenv/heldout/k8s_fresh3_slos.yaml
eb822fc50933b556e53bc3031d887de3930944f3dcde59d417c98e8ef6bc2a0b  testenv/heldout/k8s_fresh3_world.yaml
108c0e40e75391f0e059bc31f2f85ded1aec8f76bb2e3da2ced182cd4f034ef2  testenv/heldout/k8s_fresh3_slos.yaml
$ git merge-base --is-ancestor 6450a8f b5de59f && echo freeze-before-fix
not-ancestor (parallel branches)
6450a8f 2026-10-30 22:50:03 +0900 c0042-k8s-fresh3-freeze [3/4 freeze]: third pre-registered world, catalog, additive generator kinds, tests
b5de59f 2026-10-30 22:47:23 +0900 c0043-k8s-silent-discovery [3/4 impl]: 窗口发现 + 条件 A 近期数据子项（stale_days），过期 workload 判 UNKNOWN；stale 场景与单测
$ merge order on main
87b907f merge c0043-k8s-silent-discovery（窗口发现，最近无数据判 UNKNOWN）
0c40001 merge c0042-k8s-fresh3-freeze（第三预注册世界，修复前冻结 6450a8f）
```

## 评估一次（record，真实 claude -p）

```
k8s run k8s-fresh3 (as_of 2026-10-29T23:55:00Z): 24 decisions {'ALLOW': 14, 'DENY': 2, 'NOOP': 2, 'REQUIRE_APPROVAL': 2, 'UNKNOWN': 4}, plans 16, ledger {'new': 16, 'unchanged': 0, 'expired': 0, 'halted': 0, 'approved': 0, 'suppressed': 0, 'held': 0}, narrative {'llm': 24}
runs/k8s-fresh3/k8s_decisions.json
GATE FAILED: verdict_accuracy 0.9583 is worse than baseline 1.0
{"verdict_accuracy": 0.9583, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0}
n={'expected': 24, 'decisions': 24, 'unknown_expected': 4, 'ranged': 16, 'realizable': 1} narrative={'llm': 24}
wrong: promo-renderer/cpu got ALLOW expect REQUIRE_APPROVAL
exit=1
```

## 回放复现

```
{"verdict_accuracy": 0.9583, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0}
n={'expected': 24, 'decisions': 24, 'unknown_expected': 4, 'ranged': 16, 'realizable': 1} narrative={'llm': 24}
wrong: promo-renderer/cpu got ALLOW expect REQUIRE_APPROVAL
exit=1
```

## 文档检查

```
$ python3 tools/check_rules.py --docs
check_rules: OK
```
