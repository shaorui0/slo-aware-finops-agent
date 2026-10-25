## 1. 执行回写台账
- [ ] 1.1 `act.py`：`apply_plan(state_path=...)`、`--state`、`ledger` 字段；漂移闸门只比较涉及的资源
- [ ] 1.2 `test_act.py`：verified / rolled_back / refused / failed 四种结局的台账状态；无存活提案时不影响结局；资源子集比较

## 2. 流水线出报告
- [ ] 2.1 `pipeline.py` + `__main__.py`：`--prev-run`、`--score`，写三份 HTML
- [ ] 2.2 `test_pipeline.py`：三份 HTML 存在、不含本机路径、带 actions 时周报显示

## 3. 报告对齐与打磨
- [ ] 3.1 `report.py`：台账、考卷、计划、narrative 真实形状
- [ ] 3.2 证据标签与相关性过滤；「做完应该是什么样」首句说钱
- [ ] 3.3 `test_report.py` 与夹具改为真实运行产物

## 4. demo 与样例
- [ ] 4.1 `ops/k3d/shop.yaml` 与 `scripts/k8s_demo.sh`：生成的计划、清单对齐世界、台账流转、周报
- [ ] 4.2 `make k8s-demo` 真实跑一遍（仅 finops-test），再 `make k8s-down`
- [ ] 4.3 `scripts/k8s_report_samples.sh` 用真实 run 重新生成 `reports/k8s-sample/*.html`

## 5. 验收
- [ ] 5.1 `make test`、`make k8s-eval`（verdict_accuracy 1.0、unsafe_allow 0）、`make k8s-days`、`make eval`、`python3 tools/check_rules.py --docs` 通过（`reports/index.html` 缺行除外）
- [ ] 5.2 evidence.md、`reports/0034-k8s-loop.html`、`notes/blog/0034-k8s-loop.md`（含 PROGRESS_ROW）
