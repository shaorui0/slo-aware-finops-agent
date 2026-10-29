# c0041 验收证据

## 2.1 文档检查
```
$ python3 tools/check_rules.py --docs
check_rules: OK
```

## 2.2 规则层与泄漏扫描
```
$ python3 tools/check_rules.py
check_rules: OK
```

## 2.3 新节中的数字与出处
| README 中的说法 | 出处 |
|---|---|
| CPU p99 × 1.3，内存峰值 × 1.2 | `config/k8s_policy.yaml:9`（cpu_headroom 0.3）、`:11`（mem_headroom 0.2） |
| 预测 $280.32，实际 $140.16 | `docs/K8S_SAVINGS_VERIFICATION.md:8` |
| 偏差拆成五类原因 | `docs/K8S_SAVINGS_VERIFICATION.md:9`、表 V3（:57） |
| 判对 22/24，unsafe ALLOW 0 | `eval/EXPERIMENTS.md:495`（§9.2） |
| 第一个预注册世界抓到一条内存泄漏放行 | `eval/EXPERIMENTS.md` §8.0 |
| 三道闸、canary、自动回滚 | `docs/NARRATIVE.md` M3 |
| 台账去重、过期、safe_halt | `docs/NARRATIVE.md` M4 |

结论：通过。纯文档改动，未改代码、未重跑评估。
