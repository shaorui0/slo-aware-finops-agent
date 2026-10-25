# c0035 验收证据

所有输出为原样，本机路径前缀已去掉。freeze = `7eb267b`，eval = `73b626c`。

## K10-1：freeze 早于 eval
```
$ git log --oneline -4
73b626c c0035-k8s-fresh-heldout [4/5 eval]: 新世界评估一次（record，20 条叙述盒带）：verdict_accuracy 0.90，unsafe_allow 1
7eb267b c0035-k8s-fresh-heldout [3/5 freeze]: 预注册世界冻结（评估前）
2cb1175 c0035-k8s-fresh-heldout [2/5 spec+tasks]: 冻结、世界、单次评估三条要求与任务清单
6deb1de c0035-k8s-fresh-heldout [1/5 proposal]: 预注册新 K8s 世界与盲测协议（先写协议，后做事）
$ git diff 7eb267b HEAD -- testenv/heldout/ | wc -l
0
```
冻结提交只含世界、SLO 目录、生成器的新 profile 种类与单测；评估提交只新增脚本、20 个盒带与原始输出。

## 评估：`bash eval/k8s_heldout.sh`（回放；与 record 的决策文件逐字段相同）
```
k8s run k8s-fresh (as_of 2026-10-24T23:55:00Z): 20 decisions {'ALLOW': 11, 'DENY': 2, 'NOOP': 3, 'REQUIRE_APPROVAL': 2, 'UNKNOWN': 2}, plans 13, ledger {'new': 13, 'unchanged': 0, 'expired': 0, 'halted': 0, 'approved': 0, 'suppressed': 0, 'held': 0}, narrative {'llm': 20}
runs/k8s-fresh/k8s_decisions.json
GATE FAILED: unsafe_allow 1 != 0: ['session-store/memory']; request_in_range 0.7692 < 0.8: [{'id': 'edge-gateway/cpu', 'request': 0.55, 'range': [0.25, 0.35]}, {'id': 'session-store/memory', 'request': 2.9375, 'range': [3.3, 3.9]}, {'id': 'route-quote/cpu', 'request': 0.3, 'range': [1.5, 1.9]}]; verdict_accuracy 0.9 is worse than baseline 1.0; unsafe_allow 1 is worse than baseline 0; request_in_range 0.7692 is worse than baseline 1.0; realizable_accuracy 0.6667 is worse than baseline 1.0
{"verdict_accuracy": 0.9, "unsafe_allow": 1, "unknown_recall": 1.0, "request_in_range": 0.7692, "realizable_accuracy": 0.6667}
n={'expected': 20, 'decisions': 20, 'unknown_expected': 2, 'ranged': 13, 'realizable': 3} narrative={'llm': 20}
wrong: edge-gateway/memory got NOOP expect ALLOW, session-store/memory got ALLOW expect DENY
exit 1
```
退出码 1 是评分器的闸门失败，这是发现，不是本 change 要修的错误。record 那一次的原始输出：`eval_record_output.txt`；决策与评分：`k8s_fresh_decisions.json`、`k8s_fresh_eval.json`。

## K10-2：EXPERIMENTS 小节
`eval/EXPERIMENTS.md` §8「主线 K8s：预注册新世界」：freeze/eval sha、基础世界与新世界五指标并排表、n=20 与 Wilson 区间 [0.70, 0.97]、unsafe_allow 放最前、逐条漏检的分层归因与「如果要修」（未应用）。

## 单测与基础世界
```
$ python3 -W ignore -m unittest discover -t . -s testenv -p 'test_*.py'
Ran 76 tests ... OK
$ python3 -W ignore -m unittest discover -t . -s eval -p 'test_*.py'
Ran 44 tests ... OK
$ make k8s-eval            # 基础世界，与 c0031 的证据逐字相同
{"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0}
n={'expected': 18, 'decisions': 18, 'unknown_expected': 2, 'ranged': 8, 'realizable': 2} narrative={'llm': 18}
gates: pass
$ python3 tools/check_rules.py --docs
check_rules: OK
```
生成器扩展不改旧输出：`testenv/test_k8s_fresh.py::test_base_world_series_unchanged` 钉住基础世界 326 条序列的 sha256（扩展前后相同）。

## 世界可被服务
`python3 -m testenv.metrics_server --world testenv/heldout/k8s_fresh_world.yaml` 起服务后：
```
max_over_time(container_cpu_usage_cores{workload="rate-engine"}[14d])   -> 3.308, 3.356 (weekly peak)
container_memory_working_set_bytes{workload="edge-gateway"}             -> gateway ~0.6 GiB, mesh-proxy ~1.5 GiB (6 series, 3 pods x 2 containers)
slo_burn_rate_1h{workload="fraud-score"}                                -> [] (absent by design)
job_last_duration_seconds{workload="eod-reconcile"}                     -> 3647.6
```

## 没做的
- 不修任何失败：未改 agent 代码、阈值、世界、expect、已有盒带。
- LLM 叙述走真实 `claude -p` 录制（`claude` 在 PATH 上）；每条只录一次。
