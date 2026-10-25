## 1. 夹具
- [x] 1.1 `finops_agent/k8s/fixtures/make_fixtures.py`：对 `--world k8s` 的 metrics_server 跑 collect+decide，生成 day1（as_of 10-25）与 day2（10-26）的 `k8s_decisions.json`、`k8s_plans.json`
- [x] 1.2 手写 `k8s_state.json`、`k8s_actions.jsonl`、`k8s_verify.json`、`k8s_unit_cost.json`、`score.json`

## 2. 渲染
- [x] 2.1 `finops_agent/k8s/report.py`：日报（图例、摘要、对比、六段卡片、被拒表、NOOP 折叠）
- [x] 2.2 周报与月报
- [x] 2.3 CLI 与 `render(view, inputs)`

## 3. 测试与样例
- [x] 3.1 `finops_agent/k8s/test_report.py`：六段各一次、摘要金额等于 ALLOW 的 realizable 之和、DENY/UNKNOWN 全入被拒表且带 unknown_needs、卡片数字落地检查、safe_halt 横幅、缺可选输入不崩、无 verify 时周报文案
- [x] 3.2 `scripts/k8s_report_samples.sh` 生成并提交 `reports/k8s-sample/{daily,weekly,monthly}.html`

## 4. 验收
- [x] 4.1 `python3 -W ignore -m unittest discover -t . -s finops_agent -p 'test_*.py'` 通过
- [x] 4.2 `python3 tools/check_rules.py --docs` 通过
- [x] 4.3 `bash scripts/k8s_report_samples.sh` 后三个 HTML 已生成，且不含本机绝对路径
- [x] 4.4 evidence.md、`reports/0032-k8s-report.html`、`notes/blog/0032-k8s-report.md`
