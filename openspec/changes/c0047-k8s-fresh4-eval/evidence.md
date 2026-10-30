# c0047 验收证据

结论：评估一次，28/28，unsafe_allow 0，allow_below_recent 0，全部闸门通过。消融显示满分来自 c0043（窗口发现），c0046（近期用量子项）没有被这个世界检验到。详见 `eval/EXPERIMENTS.md` §11。

## 冻结完整性（评估前）

```
$ git diff --stat abda3c3 HEAD -- testenv/heldout/k8s_fresh4_world.yaml testenv/heldout/k8s_fresh4_slos.yaml testenv/test_k8s_fresh4.py
（空）
$ shasum -a 256 testenv/heldout/k8s_fresh4_world.yaml testenv/heldout/k8s_fresh4_slos.yaml
05ef97dae2c202f510c20fcfc12659e4ceb950f371d2740f78797997b46b8a09  testenv/heldout/k8s_fresh4_world.yaml
f2466b2cc1d7e3449b5f1349c98abf2f016577333d4a0477850bbdf736eb58d5  testenv/heldout/k8s_fresh4_slos.yaml
$ 冻结与修复的提交时间
abda3c3 2026-10-30 23:12:32 +0900 c0045-k8s-fresh4-freeze [3/4 freeze]: 冻结第四个预注册世界、SLO 目录、daily_burst 生成器与测试
d659d0b 2026-10-30 23:23:16 +0900 c0046-k8s-recent-regime [1/4 proposal]: 条件 B 近期用量子项 + 打分器 allow_below_recent 硬闸门（修 EXPERIMENTS §10 的窗口代表性缺陷）
a4b39da 2026-10-30 23:23:17 +0900 c0046-k8s-recent-regime [2/4 spec+tasks]: 近期子项判 UNKNOWN 的理由、证据口径、allow_below_recent 定义与验收命令
$ c0046 分支是否包含冻结提交
does not contain
```

## 评估一次（record）

```
k8s run k8s-fresh4 (as_of 2026-10-29T23:55:00Z): 28 decisions {'ALLOW': 12, 'DENY': 6, 'NOOP': 4, 'REQUIRE_APPROVAL': 2, 'UNKNOWN': 4}, plans 14, ledger {'new': 14, 'unchanged': 0, 'expired': 0, 'halted': 0, 'approved': 0, 'suppressed': 0, 'held': 0}, narrative {'llm': 28}
runs/k8s-fresh4/k8s_decisions.json
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0, "allow_below_recent": 0}
n={'expected': 28, 'decisions': 28, 'unknown_expected': 4, 'ranged': 14, 'realizable': 14} narrative={'llm': 28}
gates: pass
exit=0
```

## 回放复现

```
n={'expected': 28, 'decisions': 28, 'unknown_expected': 4, 'ranged': 14, 'realizable': 14} narrative={'llm': 28}
gates: pass
exit=0
```

## 消融

```
# 消融（评估之后做，只读）：同一个第四世界、同一个指标服务、同一个打分器（HEAD），换 agent 代码版本；叙述用 template，不影响裁决。
# ab1f0d9 = c0043 合并前；e169857 = c0046 合并前（含 c0043）；HEAD = 当前
== agent@ab1f0d9
{"verdict_accuracy": 0.9286, "unsafe_allow": 0, "unknown_recall": 0.5, "request_in_range": 1.0, "realizable_accuracy": 1.0, "allow_below_recent": 0}
n={'expected': 28, 'decisions': 26, 'unknown_expected': 4, 'ranged': 14, 'realizable': 14} narrative={'template': 26}
wrong: cart-archiver/cpu got None expect UNKNOWN, cart-archiver/memory got None expect UNKNOWN
== agent@e169857
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0, "allow_below_recent": 0}
n={'expected': 28, 'decisions': 28, 'unknown_expected': 4, 'ranged': 14, 'realizable': 14} narrative={'template': 28}
== agent@HEAD
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0, "allow_below_recent": 0}
n={'expected': 28, 'decisions': 28, 'unknown_expected': 4, 'ranged': 14, 'realizable': 14} narrative={'template': 28}
```

## 文档检查

```
$ python3 tools/check_rules.py --docs
check_rules: OK
```
