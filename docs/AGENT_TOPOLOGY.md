# Agent 拓扑：确定性骨架，LLM 只开一个窄口

这份文档用七张图讲主线（K8s CPU/Memory requests）里 agent 工程那一面：先是完整拓扑，再拆成六个机制，分别是上下文、工具边界、状态机、验证、失败恢复和评估。每张图下面列出对应的代码位置。图只画仓库里已有的东西。

设计只有一句话：采集、推荐、裁决、执行、核实全部是代码；LLM 只在一个位置出现，把一条已经定好的裁决翻译成人能读的解释和提案。下面六个机制都围绕这个窄口展开。

图例：蓝色是确定性代码，橙色是 LLM，绿色是人，灰色是文件（状态与产物），红色是集群写操作。

## 0. 完整拓扑

```mermaid
flowchart TB
  subgraph IN["输入"]
    M["Metrics<br/>Prometheus：用量、throttling、OOM、requests"]
    S["SLO 目录<br/>testenv/k8s_slos.yaml"]
    P["节点价格 + 策略<br/>config/k8s_policy.yaml"]
  end

  subgraph RUN["一次运行：k8s run"]
    C["collect<br/>只读查询 + 证据 E1..En"]
    R["recommend<br/>p99×1.3 / max×1.2"]
    K["packing<br/>FFD 装箱：节点能否真的减少"]
    PO["policy<br/>条件 A B C D → 裁决"]
    N["narrate<br/>每条决策一次隔离调用"]
    G["grounding 校验<br/>数字必须有出处"]
    AS{"断言：裁决没变"}
    L["ledger.update<br/>去重 / 过期 / 熔断"]
    PL["plans<br/>patch、canary、停止条件、回滚"]
    RP["report<br/>日报 / 周报 / 月报"]
  end

  subgraph HUMAN["人"]
    H1["读日报"]
    H2["创建 &lt;plan_id&gt;.approved"]
  end

  subgraph EXEC["执行：act（k3d-finops-test）"]
    A1["门：context / 批准文件 / 漂移"]
    A2["patch canary + rollout"]
    A3["观察：throttling p99 OOM 重启"]
    A4["自动回滚 + 验证已还原"]
    A5["读回线上状态 = patch"]
  end

  V["verify<br/>账单差额拆五因，精确闭合"]
  ST[("runs/k8s_state.json<br/>提案台账")]
  OUT[("k8s_decisions.json<br/>k8s_plans.json<br/>k8s_trace.jsonl")]
  CAS[("eval/cassettes<br/>录制回放")]
  EV["eval/score_k8s.py<br/>CI 门禁"]

  M --> C
  S --> C
  P --> C
  C --> R --> K --> PO
  PO --> N
  CAS -.-> N
  N --> G --> AS --> L
  ST <--> L
  L --> PL --> OUT
  AS --> OUT
  OUT --> RP --> H1 --> H2
  H2 --> A1 --> A2 --> A3
  A3 -- "停止条件触发" --> A4
  A3 -- "观察期内正常" --> A5
  A4 --> ST
  A5 --> ST
  ST --> V --> RP
  OUT --> EV

  classDef code fill:#dbeafe,stroke:#1e40af,color:#0f172a
  classDef llm fill:#fed7aa,stroke:#c2410c,color:#0f172a
  classDef human fill:#bbf7d0,stroke:#15803d,color:#0f172a
  classDef file fill:#e5e7eb,stroke:#4b5563,color:#0f172a
  classDef write fill:#fecaca,stroke:#b91c1c,color:#0f172a
  class C,R,K,PO,G,AS,L,PL,RP,V,EV,A1,A3,A5 code
  class N llm
  class H1,H2 human
  class ST,OUT,CAS file
  class A2,A4 write
```

| 环节 | 代码 |
|---|---|
| 一次运行的编排 | `finops_agent/k8s/pipeline.py:65` `run` |
| 采集与证据 | `finops_agent/k8s/collect.py:328` `collect_facts` |
| 推荐值 | `finops_agent/k8s/recommend.py:24` |
| 装箱 | `finops_agent/k8s/packing.py:11` `pack` |
| 裁决 | `finops_agent/k8s/policy.py:181` `verdict_of` |
| 叙述（唯一的 LLM） | `finops_agent/k8s/narrate.py` |
| 裁决不变断言 | `finops_agent/k8s/pipeline.py:77` |
| 台账 | `finops_agent/k8s/ledger.py:70` `update` |
| 执行 | `finops_agent/k8s/act.py:310` `apply_plan` |
| 核实 | `finops_agent/k8s/verify.py:210` `run_verify` |
| 评估 | `eval/score_k8s.py` |

## 1. 上下文：每条决策一次隔离调用

```mermaid
flowchart LR
  D["一条决策<br/>verdict 已由代码定好"]
  subgraph PROMPT["prompt（只含这一条）"]
    R1["RULES<br/>不裁决、不算术、只抄数字"]
    V1["verdict（final, set by code）"]
    U1["&lt;untrusted_metadata&gt;<br/>namespace / workload / team<br/>声明为数据，不是指令"]
    F1["facts_view<br/>current usage recommendation savings<br/>conditions reasons evidence"]
    SC["SCHEMA<br/>why / would_change / proposal / evidence"]
  end
  X1["其他 workload"]
  X2["整次运行的数据"]
  X3["历史 / 台账"]
  LLM["LLM<br/>无状态、无工具"]

  D --> V1
  D --> U1
  D --> F1
  R1 --> LLM
  V1 --> LLM
  U1 --> LLM
  F1 --> LLM
  SC --> LLM
  X1 -. "看不到" .-x LLM
  X2 -. "看不到" .-x LLM
  X3 -. "看不到" .-x LLM

  classDef llm fill:#fed7aa,stroke:#c2410c,color:#0f172a
  classDef no fill:#f3f4f6,stroke:#9ca3af,color:#6b7280,stroke-dasharray:4 3
  class LLM llm
  class X1,X2,X3 no
```

- 只给这一条决策：`narrate.py:59` `facts_view`。放在一起讲会把 A 的数字串到 B 上。
- 规则与禁止算术：`narrate.py:31` `RULES`。
- 第三方字段围起来：`narrate.py:37`、`narrate.py:72`。
- 长期记忆在台账文件里，由代码读入，不在对话里：`runs/k8s_state.json`。

## 2. 工具边界：三层最小权限，加一层 hook

```mermaid
flowchart TB
  subgraph L1["LLM 层"]
    LL["narrate<br/>没有任何工具，只输出一个 JSON"]
  end
  subgraph L2["读层"]
    CO["collect<br/>只读 Prometheus"]
  end
  subgraph L3["写层 act.py"]
    SA["sanitize_cmd<br/>动词白名单 patch / scale / set / rollout"]
    CT["context 固定 k3d-finops-test"]
    AP["批准文件名绑定 plan_id"]
  end
  subgraph L4["开发侧 hook（约束写代码的 coding agent）"]
    HK["hooks/aws_readonly_guard.py<br/>AWS 只读<br/>kubectl 写须 --context k3d-finops-test + # INTENT:<br/>k3d 只能动 finops-test"]
  end
  K8S[("k3d-finops-test")]
  PROD[("其他集群 / AWS")]

  LL -. "无路径" .-x K8S
  CO --> |"GET"| PROM[("Prometheus")]
  SA --> CT --> AP --> K8S
  HK -. "拦截" .-x PROD

  classDef llm fill:#fed7aa,stroke:#c2410c,color:#0f172a
  classDef code fill:#dbeafe,stroke:#1e40af,color:#0f172a
  classDef guard fill:#fef3c7,stroke:#b45309,color:#0f172a
  class LL llm
  class CO,SA,CT,AP code
  class HK guard
```

| 层 | 能做 | 不能做 | 代码 |
|---|---|---|---|
| LLM | 输出一个 JSON | 查询、执行、写裁决 | `narrate.py` |
| 读 | 只读 Prometheus | 写任何东西 | `collect.py:61` `Prom` |
| 写 | 四个动词，固定 context | 计划里非法的命令到不了集群 | `act.py:38-39`，`act.py:77` `sanitize_cmd` |
| hook | 放行合规的写 | AWS 写、非测试集群写、缺 INTENT 的写 | `hooks/aws_readonly_guard.py:146` `check_kubectl`，`:168` `check_k3d` |

代码那一层约束这个 agent，hook 那一层约束开发它的 coding agent。两层防的对象不同，任何一层失守，另一层还在。

## 3. 状态机：提案台账

```mermaid
stateDiagram-v2
  [*] --> proposed: 裁决为 ALLOW / REQUIRE_APPROVAL
  proposed --> proposed: 同裁决、目标值差 ≤5%（只更新 last_seen）
  proposed --> approved: 运行时发现 plan_id.approved 文件
  proposed --> expired: 裁决或目标值变了 / 不再可执行
  approved --> expired: 裁决或目标值变了
  approved --> applied: act 打完 patch
  applied --> verified: 观察期内无停止条件，线上 = patch
  applied --> rolled_back: 停止条件触发，回滚并确认已还原
  applied --> halted: 之后的运行发现条件 C FAIL
  halted --> verified
  halted --> rolled_back
  expired --> [*]
  verified --> [*]
  rolled_back --> [*]: 同裁决同目标不再重提（suppressed）

  note right of halted
    同时置全局 safe_halt：
    不出计划、不出新提案，
    人工 k8s clear-halt 才解除
  end note
```

| 规则 | 代码 |
|---|---|
| 一个 (namespace, workload, resource) 同时只有一条 open 提案 | `ledger.py:43` `key_of` |
| 合法转移表 | `ledger.py:20` `TRANSITIONS` |
| 去重、过期、熔断 | `ledger.py:70` `update`，熔断在 `ledger.py:83-88` |
| applied / verified / rolled_back 只由执行器写 | `ledger.py:142` `mark`，`act.py:291` `ledger_mark` |
| 回滚过的同一目标不再重复提案（目标值移动后才重提） | `ledger.py:114-116`（diff 里记为 suppressed） |
| 人工解除熔断 | `ledger.py:154` `clear_halt` |
| 时间戳用运行的 as_of，可以模拟多天回放 | `make k8s-days` |

SLO 调整走的是「过期」这条边：SLO 目录一变，下一次运行条件 C 的结果就变，裁决随之改变，旧提案 expired，必要时出新提案。

## 4. 验证：输出、动作、结果三层

```mermaid
flowchart TB
  subgraph O["① 输出：LLM 写的话"]
    O1["严格 JSON + 字段形状"] --> O2["引用的证据 id 必须存在"]
    O2 --> O3["每个数字在本条决策里找得到<br/>找不到的从句涂掉"]
    O3 --> O4["声称其他裁决的从句涂掉"]
    O4 --> O5{"涂完还有无出处的数字？"}
    O5 -- "有" --> O6["整条拒掉（fail closed）"]
    O5 -- "无" --> O7["采用"]
    O7 --> O8{"断言：全部裁决与叙述前一致"}
  end
  subgraph A["② 动作：patch 真生效且没伤服务"]
    A1["执行前：线上 = plan.before（漂移检查）"] --> A2["rollout status"]
    A2 --> A3["观察窗口：throttling p99 OOM 重启"]
    A3 --> A4["读回线上状态 = patch"]
  end
  subgraph R["③ 结果：钱真的省了"]
    R1["BEFORE / AFTER 两个等长窗口<br/>节点数由 FFD 装箱逐小时算出"] --> R2["差额拆五因<br/>a 变更 b 无关增长 c HPA d 回滚 e 装箱无收益"]
    R2 --> R3{"五因之和 = 账单差额？"}
    R3 -- "否" --> R4["核实不算数"]
  end
  O8 --> A1
  A4 --> R1
```

| 层 | 代码 |
|---|---|
| 输出：解析与形状 | `narrate.py:135` `parse_narrative` |
| 输出：数字落地 | `narrate.py:93` `grounding_brief`，`finops_agent/judge/validate.py:117` `ungrounded_numbers` |
| 输出：错误裁决涂掉 | `narrate.py:108` `_strip_wrong_verdicts` |
| 输出：fail closed | `narrate.py:162` |
| 裁决不变 | `pipeline.py:77` |
| 动作 | `act.py:310-391` |
| 结果 | `verify.py:210` `run_verify`，模块注释写了五因链和闭合断言 |

grounding 只能保证「不编数」，保证不了「推理对」。推理对不对由两件事兜底：裁决本来就不归 LLM；eval 对裁决打分（第 6 节）。LLM 出错，最坏的结果是解释写得差，不会让一个危险变更被放行。

## 5. 失败恢复：失败的方向永远是更保守

```mermaid
flowchart LR
  subgraph J["判断侧"]
    J1["数据不足（条件 A 不过）"] --> J1o["UNKNOWN，写明补什么数据"]
    J2["LLM 调用失败 / 回复不可用"] --> J2o["确定性模板叙述<br/>source: template，trace 记原因"]
    J3["回放模式缺 cassette"] --> J3o["报错，不偷偷调线上模型"]
    J4["SLO 退化（已改过的 workload）"] --> J4o["halted + 全局 safe_halt"]
  end
  subgraph E["执行侧"]
    E1["context 不对 / 无批准文件 / 线上漂移"] --> E1o["refused，不执行"]
    E2["apply 失败"] --> E2o["failed"]
    E3["停止条件触发"] --> E3a["自动回滚"] --> E3b{"线上 = 改动前？"}
    E3b -- "是" --> E3o["rolled_back"]
    E3b -- "否" --> E3f["failed：需要人工介入，不再往下推"]
    E4["patch 已应用但状态不确定"] --> E4o["台账停在 applied，写明需要人处理"]
  end

  classDef safe fill:#dcfce7,stroke:#15803d,color:#0f172a
  classDef stop fill:#fee2e2,stroke:#b91c1c,color:#0f172a
  class J1o,J2o,E1o,E3o safe
  class J3o,J4o,E2o,E3f,E4o stop
```

| 失败 | 代码 |
|---|---|
| UNKNOWN 永远不当作通过 | `policy.py:181` `verdict_of`（A 不过直接 UNKNOWN） |
| 模板叙述 | `narrate.py:172` `template_narrative`，调用失败与被拒在 `narrate.py:232` `narrate_all` |
| 缺 cassette 报错 | `finops_agent/llm.py:34` `CassetteMissing` |
| 执行门与回滚确认 | `act.py:326-388` |
| 状态不确定停在 applied | `act.py:320-321` |

## 6. 评估：learn 这一步是人在环里

```mermaid
flowchart LR
  W["手写测试世界<br/>每个 case 有期望裁决与推荐区间"] --> RUN["k8s run（回放）"]
  RUN --> SC["score_k8s.py<br/>verdict_accuracy<br/>unsafe_allow<br/>unknown_recall<br/>request_in_range"]
  SC --> GA{"门禁<br/>unsafe_allow = 0<br/>不低于基线<br/>叙述里无无出处数字"}
  GA -- "通过" --> CI["CI 绿：make k8s-eval"]
  GA -- "不通过" --> FAIL["CI 红"]

  F0["新世界：先冻结再跑，只跑一次"] --> RUN
  RUN --> MISS["漏判 / 危险放行"]
  MISS --> HUMAN["人：读失败，定修法"]
  HUMAN --> FREEZE["先冻结下一个世界"]
  FREEZE --> FIX["再修代码或阈值<br/>旧世界记为已污染"]
  FIX --> W

  classDef human fill:#bbf7d0,stroke:#15803d,color:#0f172a
  class HUMAN human
```

| 世界 | 状态 | 结果 |
|---|---|---|
| 基础世界 | 样本内 | 18/18，unsafe 0 |
| 第一个冻结世界（修复前） | 只跑一次 | 0.90，unsafe 1（内存缓慢上涨，当时没有趋势检查） |
| 同一世界（修复后） | 已污染 | 1.0 |
| 第二个冻结世界（在修复之前冻结） | 只跑一次 | 0.9167，unsafe 0，unknown_recall 0 |

| 环节 | 代码 |
|---|---|
| 打分与门禁 | `eval/score_k8s.py:54` `score`，`:100` `gates` |
| 基线 | `eval/k8s_baseline.json` |
| 冻结世界 | `testenv/heldout/k8s_fresh_world.yaml`，`testenv/heldout/k8s_fresh2_world.yaml` |
| 一次性评估脚本 | `eval/k8s_heldout.sh` |
| 实验记录 | `eval/EXPERIMENTS.md` §8、§9 |

没有自动学习，这是有意的。自动调阈值等于拿测试集训练，分数会变成自欺；阈值是风险决策，应当由人拍板，并留下 commit 和理由。

## 已知缺口

- 第二个冻结世界里有一个 workload 最后一周没有数据。workload 发现用的是瞬时查询，它被静默丢掉了，而不是判成 UNKNOWN。这违反了第 5 节的原则。按规矩，修之前要先冻结第三个世界。
- 演示里的回滚证明停止条件会触发并且回滚可以确认，但 throttling 由 limit 决定，不由 request 决定，所以它不证明「降 request 导致了退化」。
