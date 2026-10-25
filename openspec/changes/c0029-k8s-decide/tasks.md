## 1. 配置
- [x] 1.1 `config/k8s_policy.yaml`：min_days、min_coverage、cpu/mem headroom 与 step、margin、max_throttle、oom_guard、min_budget、deadline_guard、same_target_tolerance，另有 window_days、valid_day_min_hours、pricing

## 2. 纯函数
- [x] 2.1 `recommend.py` + `test_recommend.py`：两条公式、向上取整、NOOP
- [x] 2.2 `packing.py` + `test_packing.py`：FFD、两种节省口径、确定性、装箱陷阱

## 3. 策略引擎
- [x] 3.1 `policy.py` + `test_policy.py`：条件 A–D、六步判定顺序、HPA 联动、unknown_needs、`decide`、`write_decisions`

## 4. 取数
- [x] 4.1 `collect.py` + `test_collect.py`：PromQL 取数、evidence、Trace；用桩传输层和一个本地假 HTTP 服务测

## 5. 验收
- [x] 5.1 `python3 -W ignore -m unittest discover -t . -s finops_agent -p 'test_*.py'` 全过（含原有测试）
- [x] 5.2 `python3 -m unittest discover -t . -s finops_agent/k8s -p 'test_*.py'` 通过（MATURITY K2 的验证命令）
- [x] 5.3 `python3 tools/check_rules.py --docs` 通过
- [x] 5.4 evidence.md、`reports/0029-k8s-decide.html`、`notes/blog/0029-k8s-decide.md`
