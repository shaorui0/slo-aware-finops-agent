## 1. 实现
- [x] 1.1 `config/k8s_policy.yaml`：趋势阈值；`collect.py`：每日最大值序列、最小二乘、证据 `trend_slope` / `trend_proj`
- [x] 1.2 `policy.py`：条件 B 趋势子项与中文理由；单测（上升 FAIL、平稳 PASS、下降无 FAIL、cpu 开关）
- [x] 1.3 `collect.py`：用量、throttling、requests、limits 按容器过滤；sidecar 夹具单测
- [x] 1.4 `pipeline.py`：`--nodes-from` 读 `pricing`；单测（有、无）

## 2. 评估
- [x] 2.1 基础世界 `make k8s-eval` 闸门通过，verdict 与推荐值不变；重录盒带（只新增，旧的 git mv 到 `eval/cassettes/archive/` 并列 MANIFEST）
- [x] 2.2 第一个预注册世界 `bash eval/k8s_heldout.sh` 重跑；`eval/EXPERIMENTS.md` 追加 §8.5（已污染）
- [x] 2.3 重新生成 `reports/k8s-sample/`（如需要）

## 3. 验收
- [x] 3.1 `make test`、`make k8s-eval`、`make k8s-days`、`make k8s-verify`、`make eval`（附录不变）、`python3 tools/check_rules.py --docs` 通过
- [x] 3.2 evidence.md、HTML 报告、博客笔记（末行 `PROGRESS_ROW:`）
