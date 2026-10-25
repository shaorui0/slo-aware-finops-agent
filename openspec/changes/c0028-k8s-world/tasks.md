## 1. 世界

- [x] 1.1 `testenv/k8s_world.yaml`：9 个 workload、expect（cpu 与 memory）、traps、集群尺寸（6 节点，FFD 事实成立）
- [x] 1.2 `testenv/k8s_world.py`：确定性生成器、`load(as_of=)`、场景叠加、元数据说明
- [x] 1.3 `testenv/k8s_scenarios/drift_day3.yaml`

## 2. 服务

- [x] 2.1 `metrics_server --world k8s` / `--as-of`；缺样本点（None）在选择器与范围函数里被跳过；k8s 元数据只在该模式下出现
- [x] 2.2 增加 `container_cpu_usage_cores`（契约变更请求）

## 3. 测试

- [x] 3.1 `testenv/test_k8s_world.py`：自洽、指标与标签、形状、推荐器推导、装箱事实、new-service 5 天、漂移、默认世界元数据不被污染

## 4. 验收

- [x] 4.1 `python3 -W ignore -m unittest discover -t . -s testenv -p 'test_*.py'` 全绿（含原有测试）
- [x] 4.2 `python3 tools/check_rules.py --docs` 通过
- [x] 4.3 `python3 -m testenv.metrics_server --world k8s --port N` 实际起服务，抽查 /api/v1/series 与 query
