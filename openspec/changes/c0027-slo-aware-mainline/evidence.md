# c0027 验收证据

## check_rules --docs
```
check_rules: OK
```

## make test（本 change 不改代码）
```
Ran 3 tests in 0.067s
OK
Ran 29 tests in 5.078s
OK
Ran 160 tests in 20.858s
OK
Ran 35 tests in 17.317s
OK
Ran 35 tests in 5.738s
OK
```

## make eval（附录数字不变）
```
{"recall": 1.0, "savings_recall": 1.0, "precision": 1.0, "savings_accuracy": 0.9167, "step_agreement": 1.0, "depth": {"shallow_recall": 1.0, "deep_recall": 1.0}, "leads": 7}
gates: pass
```
