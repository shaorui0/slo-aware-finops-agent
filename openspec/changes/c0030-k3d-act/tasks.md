## 1. Guard
- [ ] 1.1 `hooks/aws_readonly_guard.py` 增加 kubectl/k3d 解析；`hooks/test_aws_readonly_guard.py` 增加 allow/block 表，原 aws 用例不变

## 2. act.py
- [ ] 2.1 `finops_agent/k8s/act.py`：apply/status，顺序门、单一 kubectl 入口、jsonl 记录
- [ ] 2.2 `finops_agent/k8s/test_act.py`：mock subprocess 覆盖每条拒绝路径与回滚

## 3. k3d 演示
- [ ] 3.1 `ops/k3d/`：集群配置、demo 应用、负载生成；`scripts/k8s_demo.sh`；Makefile 加 `k8s-demo`、`k8s-down`

## 4. 验收
- [ ] 4.1 `python3 -W ignore -m unittest discover -s hooks -p 'test_*.py'` 与 `python3 -W ignore -m unittest finops_agent.k8s.test_act` 通过
- [ ] 4.2 `make k8s-demo` 端到端跑通，日志原样存入 evidence.md；`make k8s-down` 后集群已删除
- [ ] 4.3 `python3 tools/check_rules.py --docs` 通过
