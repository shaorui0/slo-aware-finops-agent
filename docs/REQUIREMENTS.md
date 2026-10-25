# 需求分析：FinOps Agent

来源：语音 `2026-10-23_2311` / `2331` / `2335`（主），`1715` / `1758` / `1809`（FinOps 模型补充），`2026-10-20_1206`（面试叙事）。原文在维护者的私有 context 仓库里，不进本仓库。

## 1. 一句话

做一个开源的 AWS FinOps Agent，用它作为「我是 Agent Engineer」的唯一旗舰证据：产品本身证明 FinOps 模型，构建过程（spec / change / eval / harness / context）证明 Agent 工程能力。FinOps 是载体，真正要练和要展示的是 Harness Engineering（R15）。

## 2. 需求拆解

| # | 需求 | 原话锚点 | 归属 |
|---|---|---|---|
| R1 | 定位是 **Agent Engineer / Agent Infra Engineer**，不是"做 Agent 应用开发"；面试"就面你一个东西"，必须能讲清楚真正的优点 | 2311、2331 末尾 | 叙事 |
| R2 | FinOps 分层能力：可见性/归因（识别 tag、owner，**还要校验打得对不对**）→ 明显浪费（闲置）→ 潜在浪费 → 可优化 → 自动打平到目标 spec | 1809、2335 | 本项目 |
| R3 | 浪费判据要分**潮汐短期 vs 长期**：短期冲高是正常弹性，长期不降才算浪费；成本告警本身是一套 SLA 体系，水位对 SLO 负责 | 1809、1758 | 本项目 |
| R4 | 治理顺序：先删 → 再降配 → 最后买承诺（SP/RI）；单位成本/对账（每次查询多少钱） | 1715、1758 | 本项目 |
| R5 | **第一轮只做分析**，结果交给我，再往下做 | 2335 | 本项目，M1 |
| R6 | 体现 LLM 能力边界与风险：幻觉、prompt 注入，三层——是什么 / 怎么触发 / 系统上如何避免；Context 定上限，Harness 定下限 | 2331、1758 | 本项目 |
| R7 | 评估与迭代：用什么指标、优化什么、为什么优化、结果是否可验证可复用；"不是去判断，而是去验证" | 2311、2331 | 本项目 |
| R8 | 过程即交付：每个 change 走 OpenSpec + 一个 commit，有验收 loop（被动 loop + 主动 loop），不能"只有开发没有测试" | 2335 | 本项目 |
| R9 | 每个 change 产出：本地 HTML 报告（**不是** Claude artifact）+ 博客想法（只整理核心内容，**不做写作优化**） | 2335 | 本项目 |
| R10 | cron 定期把项目的公理/规则蒸馏出来，形成本项目自己的规则层 `rules/`（axioms / skills / communication / soul / user / workspace），以 context-infrastructure 为母版 | 2335 | 本项目 |
| R11 | 发到 GitHub，标准是"完完整整真正好的开源项目" | 2335 | 本项目 |
| R12 | 该做成 skill 的都做成 skill，包括"每个 change 一个 commit"这个动作本身 | 2335 末尾 | 本项目 |
| R13 | prompt prehook：每次发 prompt 时，按我的认知/公理去校验或优化 prompt | 2335 开头 | **context-infrastructure，不在本仓库** |
| R14 | 每次验收结果都要告诉我（在 main agent 里汇报）；给我看的 markdown 一律同时出 HTML | 2341 | 本项目，进 change-loop |
| R16 | 接个人 AWS 账号，**只读**。只读由项目级 hook 实现并注册在本项目 `.claude/settings.json`，这本身是项目的生产安全设计；AWS 侧再加一层限制（独立账号、只读角色等）**先只列需求，不改 AWS** | 2344 | 本项目 |
| R17 | 可见性用 **本地搭的 Grafana**。指标分两步：先由 agent 分析「需要哪些 metric」，再去系统里查「有没有」，差集就是可见性缺口 | 2345 | 本项目 |
| R18 | 个人主页（shaorui0.github.io）顶部加 **FinOps Agent** 入口页：链到博客、GitHub 地址、我的思考过程、按阶段画出的流程图、每个 commit 的交付物；按阶段展示，不一次性端出 | 2345 | 主页仓库 + 本项目 |
| R19 | 可见性**以终为始**、一环套一环：先分析要什么可见性，第二轮去系统里找对应 metric；找 metric 本身也是一个有计划、有结果的任务；最后判断每个指标能否支撑 SLA/SLI | 2348 | 本项目，D1b |
| R15 | **项目目的是学 Harness Engineering 最佳实践、体现对 AI Agent 的掌控力**，不是为做而做，也不只为解决成本问题。取舍冲突时，过程的可展示性优先于功能广度 ；并且要覆盖**工业级使用 agent 的方方面面**，本项目就是那个完整样例（覆盖面见 §5） | 2341、2347 | 全局原则 |
| R20 | **先深后广，并改名 SLO-aware FinOps Agent。** 主线只做 K8s workload 的 CPU/Memory requests，以 SLO 和容量为硬约束、以可验证的净节省为目标；Metrics、SLO、Requests 三者不能互相替代。执行权限是人批准后，只在本地 k3d 测试集群执行。已有的 12 类 AWS 浪费冻结为附录 | 2026-10-25 需求文档，10-26 改名 | 本项目，主线 |

## 3. 交付物

### 主线交付物（R20）

| ID | 交付物 | 验收 |
|---|---|---|
| K1–K12 | 合成 K8s 世界、推荐器、策略引擎、判断层、日报/周报/月报、k3d 闭环、kubectl 守卫、持续运行台账、节省核实、预注册新世界、叙事 | 逐条见 [`MATURITY.md`](MATURITY.md) 的「主线 k8s-depth」一节；接口契约见 c0027 的 spec |

下表 D0–D9 是广度扫描的交付物，已冻结为附录。

| ID | 交付物 | 验收标准（可验证） | 对应需求 |
|---|---|---|---|
| D0 | 仓库骨架 + 本文档 + change-loop skill（本次 init commit） | `openspec list` 可用；`.claude/skills/finops-change-loop` 存在；init 有 HTML 报告 + 博客笔记 | R8 R9 R12 |
| D1 | **只读分析 Agent**：输入个人 AWS 账号的成本/资源数据（经只读 hook）+ 合成数据（给 eval 和 demo） → 输出分层报告（归因 / 闲置 / 潜在 / 可优化），每条 finding 带证据（资源 ID、指标、时间窗、计算式） | 在 demo 数据集上跑通；每条 finding 都能回溯到原始数据行；零写操作 | R2 R3 R4 R5 |
| D2 | **Eval harness**：植入已知浪费的合成数据集（golden set）+ 自动打分 | 指标至少：finding precision/recall、证据引用率、**savings ≤ 实际花费**不变量、跨运行稳定性；CI 里能跑 | R7 |
| D3 | **安全 / 风险用例集**：tag 值、资源名、描述字段里埋 prompt 注入；无证据数字（幻觉）检测 | 注入用例全部不改变 agent 行为；报告里无来源的数字 = 0 | R6 |
| D4 | 修复计划（dry-run）：把 finding 转成可审阅的变更计划，人工 gate 后才执行 | 默认只输出 plan；执行路径需要显式确认；有回滚说明 | R2 R4 |
| D1b | **可见性链（以终为始）**，四环，每环单独产出、单独验收，上一环的结果是下一环的输入：① 从成本目标反推需要哪些可见性（成本 SLO/SLI 是什么）② 制定找 metric 的计划（去哪找、怎么查）③ 执行计划，产出 metric 清单（含查询证据）④ 逐个 metric 判断能否支撑某个 SLI，差集即缺口 | 每环一份产物 + 验收；缺口表每行有「为哪个 SLI 需要」和「查询证据」 | R17 R19 |
| D5 | 主动 loop：cron 定期跑分析 + eval 回归 + 公理蒸馏，写进私有 context 仓库 | 每次运行留一份可 diff 的产物；回归下降会报出来 | R8 R10 |
| D6 | 开源打磨：README、架构图、quickstart（demo 数据 5 分钟跑起来）、LICENSE、贡献指南 | 新人 clone 后不需要 AWS 账号即可跑 demo | R11 |
| D7 | 面试叙事：一页「这个项目证明了什么」，按 R1 的 Agent Engineer 口径 | 每个论点链接到具体 change / eval 结果 | R1 |
| D8 | 主页 FinOps Agent 页：阶段时间线（每个 change → 交付物 → 报告/博客链接）+ GitHub 链接 | 页面从本仓库 `reports/` 和 git log 生成，新 change 提交后可重建；导航栏有入口 | R18 |
| D9 | AWS 侧只读加固需求清单（只分析，不动 AWS） | `docs/aws_account_hardening.md`，每条写明防的是什么、hook 层为什么挡不住 | R16 |
| X1 | prompt prehook（UserPromptSubmit） | — | R13，另起，挂在 context-infrastructure |

里程碑：**M0** = D0（本次）→ **M1** = D1 + D2 最小版（R5 说的"先做分析，结果给我"在这里停一次）→ M2 = D3 → M3 = D4 → M4 = D5 → M5 = D6 + D7 发布。

## 4. 设计约束

- **公司数据零进入**。雇主的账单、资源 ID、账号号、内部代码都不能进仓库。只用合成数据，或用户自己的个人 AWS 账号（只读角色）。
- **只读优先**。M1–M2 全程无写 API；写路径（D4）单独 gate。两层防线：项目 hook（`hooks/aws_readonly_guard.py`，拦 agent 发出的 aws CLI 写操作，fail-closed）＋ AWS 侧 IAM（D9，真正的保证；hook 挡不住 SDK 直连和人手误操作）。
- **数字必须有出处**。这条来自以前做成本可见性服务的教训：报告里推荐的 savings 比花费本身还大（资源重复计 + 单价错）。在这里它直接变成 D2 的硬不变量，也是面试里最有说服力的"为什么要做 eval"。
- **LLM 只做判断层**。拉数、聚合、计算交给确定性代码（脚本/SQL），LLM 负责归类、解释、排序和计划；这样每个数字都能复算。
- 每个 change：`openspec` change → 实现 → 验收（测试/eval）→ HTML 报告 + 博客笔记 → 一个 commit。流程写在 `.claude/skills/finops-change-loop/SKILL.md`。

## 5. Harness Engineering 覆盖面（R15 的验收清单，也是 D7 的骨架）

工业级使用 agent 的各个面，每一面都在本项目里有可指认的实现。状态写明交付它的 change，证据在 `openspec/changes/<id>/evidence.md`。

| 面 | 本项目里的实现 | 状态 |
|---|---|---|
| Context engineering（上限） | 项目级规则层 `rules/`（SOUL/USER/COMMUNICATION/WORKSPACE/axioms/skills），每次会话由 `CLAUDE.md` 加载；FinOps 领域模型写进 [`METHOD.md`](METHOD.md) 与 README 第一部分；规则同步工具 `tools/sync_rules.py` | ✅ c0003 规则层；c0008 语言规则；c0012 同步工具；c0013 方法与领域模型文档 |
| 权限与边界（下限） | 项目 hook `aws_readonly_guard.py` + 测试服务器拒写 + 处置只出 dry-run 计划；AWS 侧 H1–H7 加固清单见 [`aws_account_hardening.md`](aws_account_hardening.md) | hook ✅ c0001；服务器拒写 ✅ c0004；dry-run 方案 ✅ c0011。AWS 侧加固只列需求，落地受阻于真实 AWS 账号（见下方例外） |
| Spec 驱动 | 每个 change 一个 OpenSpec change，proposal/spec/tasks/evidence 留痕 | ✅ 从 c0001 起，c0001 到 c0025 |
| 任务分解 / 计划 | 可见性链四环（D1b），每环先计划后执行，产物落 `visibility.json`；第 ① 环可由 LLM 从成本目标推导，第 ② 环按语义发现指标名 | ✅ c0002、c0005；c0017 LLM 推导第 ① 环；c0020 第 ② 环发现与 need 级第 ④ 环 |
| 工具设计 | 确定性检测器做拉数和计算；跨源信号输出为 Lead，由判断层的取证器算出候选金额再交给模型选择 | ✅ c0005 检测器；c0009 判断层与取证器；c0023 持续异常线索 |
| 评估 | golden set（`testenv/world.yaml`，12 finding / 4 trap / 3 gap）+ `eval/score.py` + baseline 回归闸；样本内、样本外、预注册新世界分开报 | ✅ c0004–c0006；c0009 recall 1.0（样本内）；c0015 样本外与实验记录；c0024 预注册世界 recall 0.75 |
| 幻觉 / 注入防御 | 12 个注入变体 + 输出数字落地规则（G1–G9）；数字诱饵由校验器脱敏而不是否决 | ✅ c0015 用例集与落地规则；c0022 number_bait |
| 人工 gate / 回滚 | 写路径只出 dry-run 计划，含撤回成本与回滚说明，无 `--approve` 不执行，承诺折扣只给建议 | ✅ c0011 dry-run 方案；c0016 接入每次运行 |
| 验收 loop | 被动：hook / 单测 / CI（check → test → alerts-test → eval）；主动：每日巡检 `make inspect` + 跨运行 rot 检测 + SLO 告警 + `make rot-demo` | ✅ CI c0006；巡检与 SLO c0007；三周期 rot 演示 c0010；resolved 与 not_redetected 区分 c0023。cron 在真实环境的部署受阻于真实 AWS 账号 |
| 可观测性（agent 自身） | `trace.jsonl` 记每次外部调用，LLM 调用带 tokens、延迟、成本、模式、结论；报告「Agent 成本」一节；LLM 录制回放 | ✅ c0005 API trace；c0009 token 成本与 cassette |
| 多 agent 编排 | 4 波并行、每个 agent 独立 worktree 与 owned_paths、主 agent 只合并并重验、变更走 escrow、NEXT 准入规则；过程记录在 [`BUILD_PROCESS.md`](BUILD_PROCESS.md)，完成标准在 [`MATURITY.md`](MATURITY.md) | ✅ c0009 到 c0025 的构建本身；c0025 文档化 |
| 记忆 / 知识沉淀 | 公理 FA01–FA13 在 `rules/axioms/`；每 change 一份博客笔记；母版同步提议工具 | ✅ c0003 公理；笔记每个 change；c0012 同步工具（提议不落盘） |
| 可展示性 | 每 change 一份 HTML 报告；README 两部分 + 思考过程；六张图 + 导读页；NARRATIVE、MATURITY；主页阶段页（D8） | 报告 ✅ c0001 起；README ✅ c0006、c0025；图与导读页 ✅ c0019；叙事与成熟度 ✅ c0025。主页入口受阻于维护者批准 push（见下方例外） |
| 运营可持续（FinOps SLO） | 8 个成本 SLI、浪费预算、11 条告警（promtool 单测）、巡检节奏、五种发现状态 | ✅ c0007；c0023 加 not_redetected |
| 五段式与预防 | 每条标准答案与每条发现读成现状、期望、差异、处置、预防；测试数据目录页 | ✅ c0014 目录页；c0016 报告卡片、预防控制表与 owner |
| 省钱核实 | 用前后两期 Cost Explorer 数据核实 resolved 的真实节省，偏差按原因分类 | ✅ c0018 方法；c0021 真实偏差场景 |

### 依赖维护者的例外

下面两行是矩阵里仅有的、受阻于维护者而不是工程的项目；除此之外，矩阵里不再有尚未交付的行。

- 真实 AWS 账号：AWS 侧 H1–H7 加固的落地、个人账号上的只读运行、cron 巡检的真实部署，都需要维护者建好只读 profile。
- 主页入口：本地构建可做，push 到主页仓库需要维护者批准。

## 6. 已确认（2026-10-23 23:44–23:45）

| Q | 决定 |
|---|---|
| Q1 数据源 | 个人 AWS 账号，只读（R16）；合成数据仍用于 eval 和 demo |
| Q2 可见性 | Grafana，本地搭（R17） |
| Q3 LICENSE | MIT |
| Q4 名字 | GitHub 仓库 `finops-agent`（FinOps Agent） |

## 7. 已确认（23:48 之后）

| Q | 决定 |
|---|---|
| Q5 主页 | `https://shaorui0.github.io/#/cn`。源码在维护者私有 context 仓库的简历站目录（content 下的 md 是事实源，由构建脚本生成），部署仓库是主页的 GitHub Pages 仓库。D8 在这里加 FinOps Agent 入口 |
