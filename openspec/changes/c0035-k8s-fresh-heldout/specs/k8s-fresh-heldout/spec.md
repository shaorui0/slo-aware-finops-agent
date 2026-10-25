## ADDED Requirements

### Requirement: 世界先冻结，后评估

世界文件、SLO 目录、`expect` 与理由、评估命令必须在第一次对该世界运行 agent 或评分器之前提交。冻结之后 `expect` 不改，发现的错误写成 errata。

#### Scenario: 提交顺序是证据
- **WHEN** 读 `git log --oneline` 中本 change 的提交
- **THEN** 标有 `freeze` 的提交早于标有 `eval` 的提交，且 `git diff <freeze> HEAD -- testenv/heldout/` 为空

### Requirement: 世界是新的，且有基础世界没有的情形

世界遵守 c0027 的 schema 与 c0031 的 SLO 目录。workload 名全部不同于基础世界。至少 3 种基础世界没有的情形，本世界包含：每周（非每日）一次的峰值、内存随时间持续上升、SLO 在目录里定义但燃烧率指标缺失、sidecar 容器扭曲每 pod 用量、limit 低于需求（已在被 throttle）、不同时刻的批任务、窗口中段的数据缺口、p99.9 极尖但 p99 平的 api。每条 `expect` 带一句 `why`。

#### Scenario: 世界自洽且可被服务
- **WHEN** 加载世界与目录，并用 `testenv.metrics_server --world` 启动
- **THEN** 世界里有 `slo` 块的 workload 恰好是目录里的那些、数值相等；序列可查询；缺口与缺失的燃烧率指标按设计出现

#### Scenario: 生成器扩展不改旧输出
- **WHEN** 加入新 profile 种类
- **THEN** 基础世界的每条序列与扩展前逐点相同，`testenv` 的既有测试全部通过

### Requirement: 评估只做一次，原样报告

`eval/k8s_heldout.sh` 起服务、跑 agent、用 `eval.score_k8s --world` 评分。结果、n 与 verdict_accuracy 的 Wilson 区间、逐条漏检的分层归因写入 `eval/EXPERIMENTS.md`；`unsafe_allow` 非 0 时放在最前面。不据结果改任何 agent 代码或阈值。

#### Scenario: 与基础世界并排
- **WHEN** 读 EXPERIMENTS 新增小节
- **THEN** 有基础世界与新世界五个指标（verdict_accuracy、unsafe_allow、unknown_recall、request_in_range、realizable_accuracy）的并排表
