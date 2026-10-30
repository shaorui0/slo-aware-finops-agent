## 1. 评估
- [x] 1.1 冻结完整性核对（`integrity.txt`）
- [x] 1.2 评估一次（record，`eval_record_output.txt`）
- [x] 1.3 回放复现（`replay_output.txt`）
- [x] 1.4 消融：c0043 前 / c0046 前 / 当前三个 agent 版本（`ablation.txt`）
- [x] 1.5 EXPERIMENTS §11；README 与 NARRATIVE 成绩单加第四个世界
## 2. 验收
- [x] 2.1 record 与回放一致；`python3 tools/check_rules.py --docs` 通过
