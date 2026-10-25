# c0039 验收证据（主 agent 在 main 上复核）

```
make test        6 / 29 / 357 / 44 / 105 全部 OK
make k8s-eval    {"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0}  gates: pass
make k8s-days    day1 new=10；day2 unchanged=8 expired=2；day3 new=1 unchanged=7 expired=1
make k8s-verify  main: predicted 280.32 realized 140.16 closure diff 4.26e-14；control: 100%
make eval        gates: pass（附录数字不变）
make alerts-test SUCCESS（exit 0）
make check       check_rules: OK（含 --docs：链接可解析、标题含中文）
```

K11：README 与 NARRATIVE 以主线为主体、附录明确标注；主页页面待本地构建与维护者批准发布。
