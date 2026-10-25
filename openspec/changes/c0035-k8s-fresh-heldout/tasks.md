## 1. 预注册（freeze 之前）
- [ ] 1.1 `testenv/k8s_world.py`：只加新 profile 种类（weekly、spiky、creep、sidecar、gaps、slo_metrics 开关），旧输出不变
- [ ] 1.2 `testenv/heldout/k8s_fresh_world.yaml` + `k8s_fresh_slos.yaml`：10 个 workload，`expect` 与 `why`
- [ ] 1.3 `testenv/test_k8s_fresh.py`：自洽、目录一致、服务可查询、旧世界序列不变
- [ ] 1.4 提交 freeze，记下 sha；此前不运行 agent 与评分器

## 2. 评估（freeze 之后，一次）
- [ ] 2.1 `eval/k8s_heldout.sh`
- [ ] 2.2 跑一次（`claude` 可用则 `FINOPS_LLM_MODE=record`，新增 cassette，不改旧的），评分，原始输出落盘
- [ ] 2.3 提交 eval，记下 sha

## 3. 结果与验收
- [ ] 3.1 `eval/EXPERIMENTS.md` 追加「主线 K8s：预注册新世界」
- [ ] 3.2 验收：`git log` 中 freeze 早于 eval；`python3 -W ignore -m unittest discover -t . -s testenv -p 'test_*.py'` 与 `-s eval` 通过；`make k8s-eval` 基础世界不变；`python3 tools/check_rules.py --docs` 通过
- [ ] 3.3 evidence.md、HTML 报告、博客笔记（末行 `PROGRESS_ROW:`）
