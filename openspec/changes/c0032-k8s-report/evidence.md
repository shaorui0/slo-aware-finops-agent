# c0032 验收证据（原始输出）

## 1. 报告单测
```
$ python3 -W ignore -m unittest finops_agent.k8s.test_report -v
test_bad_view (finops_agent.k8s.test_report.CliTest.test_bad_view) ... ok
test_cli_writes_file_without_local_paths (finops_agent.k8s.test_report.CliTest.test_cli_writes_file_without_local_paths) ... ok
test_cards_are_grounded (finops_agent.k8s.test_report.DailyTest.test_cards_are_grounded) ... ok
test_denied_and_unknown_in_table_with_needs (finops_agent.k8s.test_report.DailyTest.test_denied_and_unknown_in_table_with_needs) ... ok
test_grounding_check_catches_a_made_up_number (finops_agent.k8s.test_report.DailyTest.test_grounding_check_catches_a_made_up_number) ... ok
test_ledger_as_dict_keyed (finops_agent.k8s.test_report.DailyTest.test_ledger_as_dict_keyed) ... ok
test_legend_and_diff (finops_agent.k8s.test_report.DailyTest.test_legend_and_diff) ... ok
test_missing_optional_inputs_do_not_crash (finops_agent.k8s.test_report.DailyTest.test_missing_optional_inputs_do_not_crash) ... ok
test_narrative_labelled_only_when_present (finops_agent.k8s.test_report.DailyTest.test_narrative_labelled_only_when_present) ... ok
test_plan_fields_and_missing_plan (finops_agent.k8s.test_report.DailyTest.test_plan_fields_and_missing_plan) ... ok
test_safe_halt_banner (finops_agent.k8s.test_report.DailyTest.test_safe_halt_banner) ... ok
test_six_sections_once_in_order (finops_agent.k8s.test_report.DailyTest.test_six_sections_once_in_order) ... ok
test_summary_money_is_sum_of_allow_realizable (finops_agent.k8s.test_report.DailyTest.test_summary_money_is_sum_of_allow_realizable) ... ok
test_utilization_is_p99_over_new_request (finops_agent.k8s.test_report.DailyTest.test_utilization_is_p99_over_new_request) ... ok
test_zero_realizable_explained (finops_agent.k8s.test_report.DailyTest.test_zero_realizable_explained) ... ok
test_ledger_flow_counts (finops_agent.k8s.test_report.WeeklyMonthlyTest.test_ledger_flow_counts) ... ok
test_monthly_quality (finops_agent.k8s.test_report.WeeklyMonthlyTest.test_monthly_quality) ... ok
test_monthly_text (finops_agent.k8s.test_report.WeeklyMonthlyTest.test_monthly_text) ... ok
test_monthly_without_inputs (finops_agent.k8s.test_report.WeeklyMonthlyTest.test_monthly_without_inputs) ... ok
test_weekly_content (finops_agent.k8s.test_report.WeeklyMonthlyTest.test_weekly_content) ... ok
test_weekly_without_verify (finops_agent.k8s.test_report.WeeklyMonthlyTest.test_weekly_without_verify) ... ok

----------------------------------------------------------------------
Ran 21 tests in 0.076s

OK
```

## 2. 全量单测
```
$ python3 -W ignore -m unittest discover -t . -s finops_agent -p 'test_*.py'
Ran 249 tests in 22.089s
OK
```

## 3. 规则检查
```
$ python3 tools/check_rules.py --docs
check_rules: OK
```

## 4. 样例生成与无本机路径
```
$ bash scripts/k8s_report_samples.sh
wrote reports/k8s-sample/daily.html reports/k8s-sample/weekly.html reports/k8s-sample/monthly.html
$ grep -c "本机用户目录前缀" reports/k8s-sample/*.html
reports/k8s-sample/monthly.html:0
reports/k8s-sample/weekly.html:0
reports/k8s-sample/daily.html:0
```

## 5. 样例页面关键数字（来自 day2 夹具）
```
可以做的 5 条合计，节点能缩才兑现： $140.16/月 另：账面释放的预留 $120.36/月 （口径不同，不等于账单减少，不要与上面相加） 待你批准 5 · 被拒 4 · 数据不足 2 · 无需调整 2 和上次比 新增 0 不变 16 消失 0 判定变化 2 K-shop-burning-slo-cpu： ALLOW 可以做 → DENY 不能动 服务正在退化，冻结优化 K-shop-burning-slo-memory： ALLOW 可以做 → DENY 不能动 服务正在退化，冻结优化 台账里已过期的提案 
cards: 10
```

## 6. 世界夹具与考卷的发现

fixtures/score.json 里 data-batch-nightly 的 cpu 与 memory 两条标准答案是 DENY，policy 实际给 REQUIRE_APPROVAL（verdict_accuracy 0.8889，unsafe_allow 0）。这是 c0029 的决策行为，本 change 不改，报告如实呈现。
