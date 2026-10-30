## 1. 世界

- [ ] 1.1 `testenv/k8s_world.py`：新增 cpu `daily_burst`，基础世界序列 sha256 不变
- [ ] 1.2 `testenv/heldout/k8s_fresh4_world.yaml`：14 个 workload，换节点类型与单价，expect 含 judgment_conflict 与 realizable
- [ ] 1.3 `testenv/heldout/k8s_fresh4_slos.yaml`

## 2. 测试

- [ ] 2.1 `testenv/test_k8s_fresh4.py`：加载、自洽、目录一致、难点类别、FFD 节点数与 realizable 复算、新 profile、指标服务可提供、基础世界不变

## 3. 冻结

- [ ] 3.1 冻结提交，记录 sha；冻结后不再改世界与目录

## 4. 验收

- [ ] 4.1 `python3 -W ignore -m unittest discover -t . -s testenv -p 'test_*.py'` 全绿
- [ ] 4.2 `python3 tools/check_rules.py --docs` 通过
- [ ] 4.3 `python3 -m testenv.metrics_server --world testenv/heldout/k8s_fresh4_world.yaml --port N` 实际起服务并抽查
- [ ] 4.4 evidence、HTML 报告、博客笔记写明盲写协议与冻结 sha，并写明没有运行任何评估
