# 我是 Agent Engineer：这个项目证明了什么

这一页给面试官和评审。每条能力声明只有三样东西：一句话的主张、能点开核对的证据、诚实的边界。证据都在仓库里，命令可复现；边界写在主张旁边。

先说定位。项目叫 SLO-aware FinOps Agent。它只做一件事：在服务 SLO、容量安全和变更策略的约束下，优化 K8s workload 的 CPU/Memory requests；人批准之后在测试集群上执行，执行之后核实是否真的省了钱。我做的是 agent 的工程：决定它能碰什么、被告知什么、输出怎么检查、怎样变好而不倒退。完整的需求和取舍见 [`REQUIREMENTS.md`](REQUIREMENTS.md) R20，完成度见 [`MATURITY.md`](MATURITY.md)，构建过程见 [`BUILD_PROCESS.md`](BUILD_PROCESS.md)。

## 主线：八项能力声明

主线是 K8s workload 的 CPU/Memory requests。每条声明的格式和后面附录部分相同：主张、证据、边界。

### M1. 先收窄问题，再写契约

- 主张：项目从「找 12 类 AWS 浪费」收窄到「一类资源做到能动手、能核实」，并且在写任何代码之前先写接口契约（输入、指标名、四个条件、判定顺序、产物格式），让后续几个 agent 能按契约并行开发。契约被实现反过来修正过三次，每次都有记录。
- 证据：契约 [`c0027 spec`](../openspec/changes/c0027-slo-aware-mainline/specs/k8s-mainline/spec.md)、需求 [`REQUIREMENTS.md`](REQUIREMENTS.md) R20。三次修正分别是：
  - 判定顺序里 NOOP 原本排第一，会让快 OOM 的内存永远判成「无需调整」，改为排在风险守卫之后（c0028 发现）。
  - deadline 属于 SLO 定义而不是指标，SLO 目录成为第三个独立输入，判对率从 16/18 到 18/18（c0031）。
  - 条件 D 按字面实现，会否决几乎所有真正能省掉节点的变更，改为允许依赖节点扩容回滚（c0029 发现、c0031 定稿）。
- 边界：契约是我按需求文档写的，没有和真实团队的变更流程对过。

### M2. 判定是确定性的，LLM 只写解释

- 主张：推荐值、装箱、四个条件、判定顺序全部是代码。LLM 每条决策调用一次，只拿这一条的证据，写解释、提案和「什么情况下判定会变」。写完之后断言判定没被改；数字必须能在证据里找到，找不到的句子被抹掉并留痕。
- 证据：
  - 代码：[`policy.py`](../finops_agent/k8s/policy.py)、[`narrate.py`](../finops_agent/k8s/narrate.py)。
  - 验收证据：c0031 [`evidence.md`](../openspec/changes/c0031-k8s-pipeline/evidence.md)。18 条解释全部来自真实 `claude -p` 的录制回放，未落地数字 0，打分器把「解释里有未落地数字」也算作闸门失败。
- 边界：录制的回答没有触发过抹除，抹除路径只被单元测试覆盖。解释写得好不好，没有人读过之外的评估（K12）。

### M3. 受控执行：批准、闸门、回滚

- 主张：agent 能动手，但只在人创建批准文件之后、只在本地 k3d 测试集群上执行。执行前有三道闸：批准文件、context、现场状态与计划一致。执行后进入 canary 观察，命中停止条件就自动回滚，并验证资源回到变更前。项目 hook 拦掉其他任何 context 上的 kubectl 写操作。
- 证据：
  - 代码：[`act.py`](../finops_agent/k8s/act.py)、[`hooks/aws_readonly_guard.py`](../hooks/aws_readonly_guard.py)。
  - 演示日志：c0030 [`evidence.md`](../openspec/changes/c0030-k3d-act/evidence.md)、c0034 [`evidence.md`](../openspec/changes/c0034-k8s-loop/evidence.md)，用的是流水线生成的计划。
  - 两次拒绝之后集群和台账都没变。内存计划在注入过载下 p99 0.7506 > 0.3，触发回滚。CPU 计划观察 120 秒后核实通过。台账走完了 applied → verified 和 applied → rolled_back 两条路。
- 边界：
  - 回滚证明的是「退化时守卫会触发」，不是「降 request 造成了退化」。throttling 来自 limit 而不是 request，演示里的过载是人为注入的。
  - 只在 k3d 上跑过，没有在真实集群上执行。

### M4. 持续运行：每天跑一次也不会出乱子

- 主张：提案台账让重复运行不重复提案；判定变化或目标值偏离超过 5% 时旧提案过期、重新提案；已执行的 workload 出现 SLO 退化时全局 safe_halt，之后只报告不出计划，直到人工清除。
- 证据：
  - `make k8s-days`，见 c0031 [`evidence.md`](../openspec/changes/c0031-k8s-pipeline/evidence.md)。第 2 天 burning-slo 开始烧预算，两条提案过期；第 3 天负载漂移 +60%，api-steady 的目标从 0.25 变成 0.35，旧提案过期并重新提案。
  - safe_halt 由单元测试覆盖：[`test_ledger.py`](../finops_agent/k8s/test_ledger.py)。
- 边界：三天是模拟时钟，没有在真实环境里用 cron 跑过；safe_halt 只在单元测试里触发过。

### M5. 省没省：核实而不是预测

- 主张：降 request 只有在节点数变少时才省钱。每条推荐同时报两种口径（账面释放的预留、节点能缩才兑现的钱），不相加；执行之后用前后两段节点小时账单核实，把变化精确拆成五类原因。
- 证据：[`K8S_SAVINGS_VERIFICATION.md`](K8S_SAVINGS_VERIFICATION.md)（由 `make k8s-verify` 生成）、c0033 [`evidence.md`](../openspec/changes/c0033-k8s-verify/evidence.md)。合计预测 $280.32，实际 $140.16。packing-trap 账面释放 $36.91，实际 0；被回滚的那条实现率 0%。分解误差 4e-14。
- 边界：场景参数是挑过的，为了让每类偏差各出现一次；合成指标不随 request 变化，所以没有延迟和 throttling 模型。

### M6. 评估纪律：样本内的 1.0 不算数

- 主张：对照标准答案打分，unsafe ALLOW（该拒却放行）是硬闸。样本内的满分只是上界。泛化只看预注册世界：先盲写并冻结，再只评一次。用来找 bug 的世界不能度量修复，所以修复之前先盲写第二个世界并冻结。
- 证据：[`eval/EXPERIMENTS.md`](../eval/EXPERIMENTS.md) §8 起；冻结提交早于评估提交，可在 `git log` 里核对。
- 边界：见下面的成绩单。

### M7. 给不懂项目的人看的报告

- 主张：同一份数据三个视图。日报回答「今天要我处理什么」：每个候选六段，被拒和数据不足单独成表，每个数字回指决策 JSON。周报回答「改完省了吗、伤到服务没有」。月报回答「这套东西值不值得信」。
- 证据：
  - 样例：[`日报`](../reports/k8s-sample/daily.html)、[`周报`](../reports/k8s-sample/weekly.html)、[`月报`](../reports/k8s-sample/monthly.html)。
  - 测试：[`test_report.py`](../finops_agent/k8s/test_report.py)，覆盖六段结构、摘要金额、数字落地及其反例、safe_halt 横幅、缺输入不崩。
- 边界：是否真的读得懂，要靠维护者亲自读一份日报来判断（MATURITY 的 K12）。合并时我发现过一处误导：核实表把集群整体的节点数放在每一行，看起来 packing-trap 也省了一台节点。已改成只显示一次并加说明。

### M8. 多 agent 构建：契约先行，产物扇出

- 主张：主线由多个子 agent 分四波并行构建。每个 agent 有独立 worktree 和只许写的路径，同波任务之间没有数据依赖。主 agent 只合并，合并后自己重跑验证命令再打勾。所有对外动作（push、仓库改名、主页）走人工批准。
- 证据：[`MATURITY.md`](MATURITY.md) 的主线一节、[`BUILD_PROCESS.md`](BUILD_PROCESS.md)、c0027 到 c0038 的 `git log`（每个 change 按阶段提交）。
- 边界：只写路径是纪律，不是强制；两个 agent 同改 Makefile 时靠合并冲突暴露。报告读取的台账格式是另一个 agent 猜的，集成时才对齐。

## 主线的成绩单

同一个 agent 在四个世界上的成绩。前两列是它被做出来或被修过的世界，只是上界；最后一列是修复之前就冻结、修复之后只评一次的世界，是唯一能对外说的泛化数字。出处：[`eval/EXPERIMENTS.md`](../eval/EXPERIMENTS.md) §8、§9。

| 指标 | 基础世界（样本内） | 第一个预注册世界，修复前 | 第一个预注册世界，修复后（已污染） | **第二个预注册世界** |
|---|---|---|---|---|
| 判定正确率 | 1.0（18/18） | 0.90（18/20） | 1.0 | **0.9167（22/24）** |
| unsafe ALLOW（该拒却放行，硬闸） | 0 | **1** | 0 | **0** |
| 数据不足判成 UNKNOWN 的比例 | 1.0 | 1.0 | 1.0 | **0.0（0/2）** |
| 推荐值落在区间内 | 1.0 | 0.77 | 0.92 | **0.94** |

这张表的读法：

- **第一个预注册世界抓到了一次危险失败。** 一个内存持续上涨（泄漏）的服务，四个条件全部通过，被判可以降内存，因为规则里没有「趋势」。修复是在条件 B 里加趋势守卫：每日峰值做最小二乘，外推 14 天。同时修了 sidecar 混入用量、节点价格没传进来两处。
- **修复之前，先盲写并冻结了第二个世界。** 用来找 bug 的世界不能度量修复：修完之后它对这些失败就被污染了，第三列的 1.0 只说明修复生效。
- **第二个世界上，危险失败是 0，新失败换了一类。** 一个最后一周没有任何指标的 workload，在发现阶段就消失了，既没有判 UNKNOWN，也没有出现在报告里。它没有被放行，但 agent 对它沉默了。这条记为下一轮要修的问题，修之前要先冻结第三个世界。
- **样本很小。** n=24，判定正确率 22/24 的 95% 区间约 [0.74, 0.98]。四个世界出自同一个作者家族，不是独立分布。

---

# 附录证据：广度扫描时期

下面是改名之前（c0001 到 c0026）的十二项能力声明和成绩单，原样保留。

## 十二项能力声明（广度扫描时期）

### 1. Context engineering：规则层决定上限

- 主张：agent 每次会话先读一套项目自己的规则层（身份、用户偏好、沟通规则、目录路由、13 条公理），上限由 context 决定，而不是由某次 prompt 的运气决定。
- 证据：[`rules/`](../rules/WORKSPACE.md)、[`DISTILLATION.md`](DISTILLATION.md)（从私有母版蒸馏：删什么、留什么、为什么）、c0003 [`openspec/changes/c0003-project-rules/evidence.md`](../openspec/changes/c0003-project-rules/evidence.md)、c0012 [`openspec/changes/c0012-rules-sync/evidence.md`](../openspec/changes/c0012-rules-sync/evidence.md)（`tools/sync_rules.py` 只出同步提议，不落盘）。
- 边界：规则层是否让 agent 变好，没有做过对照实验（有规则对无规则）；它保证的是约束可读、可检查，由 `make check` 守住目录路由和泄漏，不守住「模型是否真的照做」。

### 2. 护栏：写操作在两层被挡住

- 主张：agent 无法对 AWS 做写操作：项目 hook 在 Bash 层拦截，测试服务器对写操作返回 `UnauthorizedOperation`，AWS 侧的 IAM 加固另有需求清单；处置只出 dry-run 计划，承诺折扣连命令都不给。
- 证据：[`hooks/aws_readonly_guard.py`](../hooks/aws_readonly_guard.py) 与 [`hooks/test_aws_readonly_guard.py`](../hooks/test_aws_readonly_guard.py)、[`aws_account_hardening.md`](aws_account_hardening.md)、c0001 [`openspec/changes/c0001-requirements-v2/evidence.md`](../openspec/changes/c0001-requirements-v2/evidence.md)、c0011 [`openspec/changes/c0011-remediation-plans/evidence.md`](../openspec/changes/c0011-remediation-plans/evidence.md)（6 条有节省的 finding 里 `apply` 无 `--approve` 退出码为 2）。
- 边界：hook 只看 Bash 命令，SDK 直连和人工操作它挡不住，真正的保证是 IAM，而 IAM 加固只写了需求，没有在真实账号上落地（本项目没有接入真实 AWS 账号，见 [`MATURITY.md`](MATURITY.md) 的阻塞项）。

### 3. Spec 驱动、按阶段的改动

- 主张：每个改动是一个 OpenSpec change，按阶段提交（proposal、spec+tasks、impl、docs、acceptance），验收证据、HTML 报告和原始笔记一起交付，过程可以回放。
- 证据：[`openspec/changes/`](../openspec/changes/c0001-requirements-v2/proposal.md) 下 25 个 change（c0001 到 c0025），[`CONTRIBUTING.md`](../CONTRIBUTING.md) 的改动闭环，[`.claude/skills/finops-change-loop/SKILL.md`](../.claude/skills/finops-change-loop/SKILL.md)，[`reports/index.html`](../reports/index.html) 的全部改动一览。
- 边界：阶段编号偶有出错（c0011 的 acceptance 提交标成 `[5/5]`，实际只有 4 个阶段），历史保留没有改写。

### 4. 评估与闸门：「更好」是一个数字

- 主张：对照标准答案打分（recall、按金额加权的 recall、精确率、金额是否在区间内、方法步骤是否一致、踩陷阱次数、不变量违规），基线只升不降，CI 里会红。
- 证据：[`eval/score.py`](../eval/score.py)、[`eval/baseline.json`](../eval/baseline.json)、[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)、c0006 [`openspec/changes/c0006-eval-ci/evidence.md`](../openspec/changes/c0006-eval-ci/evidence.md)（评估抓到的第一批 bug 在打分器里：宽松匹配让召回虚高 8 个点）、实验记录 [`eval/EXPERIMENTS.md`](../eval/EXPERIMENTS.md)。
- 边界：匹配规则宽松（类型相同即命中），12 条标准答案的粒度是 0.083，一条的变化就是一个台阶；「基线只升不降」目前由 `--write-baseline` 的约定保证，打分器没有强制。

### 5. 对抗鲁棒性：注入与数字诱饵

- 主张：资源元数据按不可信输入处理；12 个注入变体（Name tag、owner tag、job 标签、ASG 名、卷 tag、礼貌权威口吻、想让陷阱被标成浪费的变体、四个数字诱饵）都没有改变发现集。数字诱饵一度压掉一条真 finding，c0022 修在校验器上，没有改 prompt。
- 证据：[`eval/test_adversarial.py`](../eval/test_adversarial.py)、[`testenv/scenarios/`](../testenv/scenarios/adversarial_number_bait.yaml)、c0015 [`openspec/changes/c0015-adversarial-and-experiments/evidence.md`](../openspec/changes/c0015-adversarial-and-experiments/evidence.md)、c0022 [`openspec/changes/c0022-grounding-number-bait/evidence.md`](../openspec/changes/c0022-grounding-number-bait/evidence.md)、[`eval/EXPERIMENTS.md`](../eval/EXPERIMENTS.md) §4。
- 边界：每个变体只录了一次（n=1），是「这一次抵住」，不是概率保证；注入文字是我写的，没有针对 Sonnet 做过优化攻击；`not_waste` 判断仍可引用不可信数字（G8，只是日志）；数字词（「九万九千」、ninety-nine thousand）不被数字识别抓到。

### 6. LLM 录制回放与 agent 可观测性

- 主张：模型只有一个出口 `llm.py`，三种模式（replay、record、live）；CI 不需要 API key，用 cassette 回放，结果确定；每次调用都进 trace，带 tokens、延迟、成本、结论与是否被接受，报告有「Agent 成本」一节。
- 证据：[`finops_agent/llm.py`](../finops_agent/llm.py)、[`eval/cassettes/`](../eval/cassettes/)、c0009 [`openspec/changes/c0009-judgment-layer/evidence.md`](../openspec/changes/c0009-judgment-layer/evidence.md)（trace 中 7 次 llm 调用全部 `mode=replay`）、[`finops_agent/test_llm.py`](../finops_agent/test_llm.py)。
- 边界：回放保证 CI 确定，不保证模型每次这么答：同一 prompt 两次录制，6 个判断里有 1 个换了结论；c0009 早期几轮调优的指标和成本没有 git 记录，无法还原；对抗调用的 tokens_in 异常大（缓存读取，原因未查）。

### 7. 多 agent 编排：worktree、owned_paths、escrow

- 主张：这个项目的大部分改动由 agent 分四波并行完成：每个 agent 独立 git worktree 与分支，只能改自己的 owned_paths，主 agent 只合并并重新验证；push 和改全局设置走 escrow，由人批准。
- 证据：[`BUILD_PROCESS.md`](BUILD_PROCESS.md)（4 波、24 个 agent、事故与教训）、[`MATURITY.md`](MATURITY.md)（DoD 逐条带验证命令）、[`../reports/index.html`](../reports/index.html)。
- 边界：编排的实现是我的 harness（仓库外），本仓库只留下过程的结果和这份文档，没有可运行的调度代码；两次泄漏和一次测试全局状态污染都是合并之后才被发现（见 BUILD_PROCESS 的事故一节），说明合并前的验证有缺口，补救是事后的。

### 8. FinOps 领域模型与方法

- 主张：成本是单价乘用量乘时长；浪费有七种形态，其中「不是浪费」（潮汐、DR 温备）和「忙但无用」最难；方法七步 M0 到 M6，先删再降最后买承诺；检测器负责算术，需要跨源判断的信号以线索交给判断层。
- 证据：[`METHOD.md`](METHOD.md)、[`../README.md`](../README.md) 第一部分、[`testenv/world.yaml`](../testenv/world.yaml)（12 条标准答案、4 个陷阱、3 个可见性缺口）、c0004 [`openspec/changes/c0004-test-env/evidence.md`](../openspec/changes/c0004-test-env/evidence.md)（只找闲置的检测器在 $8.7K/月里只找到不到 $100）、可见性链 c0002 [`openspec/changes/c0002-visibility-chain/evidence.md`](../openspec/changes/c0002-visibility-chain/evidence.md)。
- 边界：测试账号是合成的，噪声和真实账单不同；方法里的阈值（忙时 CPU 10%、跳涨 2 倍）和世界的形状互相迁就；没有接入真实 AWS 账号。

### 9. 五段式发现：现状、期望、差异、处置、预防

- 主张：每条标准答案和报告里每条发现都读成同一个结构，从「是什么」到「怎么防复发」，处置来自 dry-run 计划，预防来自按类型的控制表和告警规则名；数字由代码算，LLM 文字不进数字。
- 证据：[`finops_agent/fivepart.py`](../finops_agent/fivepart.py)、[`finops_agent/prevention.py`](../finops_agent/prevention.py)、[`reports/testenv-catalog.html`](../reports/testenv-catalog.html)、c0014 [`openspec/changes/c0014-testenv-catalog/evidence.md`](../openspec/changes/c0014-testenv-catalog/evidence.md)、c0016 [`openspec/changes/c0016-integration-five-part/evidence.md`](../openspec/changes/c0016-integration-five-part/evidence.md)（`report.html` 里五个小标题各 12 处，等于发现数）。
- 边界：多数孤儿和共享资源没有 owner tag，计划里「通知谁」只能写「未知」，这本身是归因缺口；预防控制是建议，没有在真实环境里验证过。

### 10. 省钱核实：resolved 之后真的省了吗

- 主张：对 resolved 的 finding 用前后两期 Cost Explorer 数据算反事实基线、分层对照组和精确的账单分解，比较预测与实际，并按原因分类偏差。两张发票直接相减会得出「清理没效果」：三项清理省 $76.05，账单却涨 $474.93。
- 证据：[`SAVINGS_VERIFICATION.md`](SAVINGS_VERIFICATION.md)（代码生成）、[`finops_agent/verify_savings.py`](../finops_agent/verify_savings.py)、c0018 [`openspec/changes/c0018-savings-verification/evidence.md`](../openspec/changes/c0018-savings-verification/evidence.md)、c0021 [`openspec/changes/c0021-savings-verification-real-deviations/evidence.md`](../openspec/changes/c0021-savings-verification-real-deviations/evidence.md)（场景链 5 条：预测 $744.21，实现 $140.60，实现率 18.9%；只看 agent 自己的 4 条是 85.9%）。
- 边界：首版 4 条全是 100%，那是零假设结果，只证明管线不引入虚假偏差，所以 c0021 专门喂了期中删除、承诺吸收、用量增长、口径差四类偏差；场景里「对照组与处理组增速同为 30%」是场景设计，不是方法保证；agent 的 gp2 到 gp3 牌价预测大卷漏算保留的 IOPS 与吞吐（实现率 41.7%），是检测器层面的真实偏差，没有修；核实器没有接判断层的发现。

### 11. SLO 与腐烂：清理之后会复发

- 主张：成本健康和可靠性一样会腐烂，所以有 SLI、SLO、浪费预算、告警规则和巡检节奏；跨运行的发现有五种状态，其中 `resolved` 需要成本侧证据，资源还在、成本没变只能是 `not_redetected`，漏检不会伪装成整改。
- 证据：[`FINOPS_SLO.md`](FINOPS_SLO.md)、[`finops_agent/slo.py`](../finops_agent/slo.py)、[`config/alerts/finops.rules.yml`](../config/alerts/finops.rules.yml)（promtool 单测）、[`scripts/rot_demo.sh`](../scripts/rot_demo.sh)（三个周期出现 resolved、regressed、new）、c0007 [`openspec/changes/c0007-finops-slo/evidence.md`](../openspec/changes/c0007-finops-slo/evidence.md)、c0023 [`openspec/changes/c0023-persistent-anomaly-and-resolution/evidence.md`](../openspec/changes/c0023-persistent-anomaly-and-resolution/evidence.md)。
- 边界：`not_redetected` 目前没有告警挂在它上面；持续异常线索用前 92 天 P25 做基线，异常占历史超过 75% 时基线被抬高，线索会再次消失（未测）；cron 巡检只有示例（[`ops/crontab.example`](../ops/crontab.example)），没有部署到真实环境。

### 12. 人读层：给读者的层也要可生成、可测试

- 主张：六张图（架构、数据流、可见性链、方法七步、腐烂生命周期、harness 分层）有 mermaid 源和渲染的 SVG，导读页由 `tools/build_index.py` 从仓库状态生成，测试守住链接、图的新鲜度和每个 change 都被列出；文档的语言、链接和标题由 `make check` 检查。手写导读必然和仓库脱节。
- 证据：[`docs/diagrams/README.md`](diagrams/README.md)、[`../reports/index.html`](../reports/index.html)、[`../tools/build_index.py`](../tools/build_index.py)、c0019 [`openspec/changes/c0019-human-read-layer/evidence.md`](../openspec/changes/c0019-human-read-layer/evidence.md)。
- 边界：图依赖 docker 里的 mermaid-cli 渲染，容器没有 CJK 字体，要看真浏览器的截图才能确认标签没被裁；导读页每次合并新 change 都会过期，要重新生成，这一点在构建中出过一次（BUILD_PROCESS 的事故一节）。

## 广度扫描的成绩单

### 样本内 1.0 对预注册 0.75

| 世界 | 性质 | recall | savings_recall | 深层 recall | 陷阱违规 |
|---|---|---|---|---|---|
| 基础世界 | 样本内：prompt 对着它调过，是上界 | 1.0 | 1.0 | 1.0 | 0 |
| next_month、month_after | 样本外，但已被污染：c0023 对着它们的失败修了 agent | 1.0 / 1.0 | 1.0 / 1.0 | 1.0 / 1.0 | 0 |
| fresh_world | 预注册：标准答案先提交，之后只评估一次，事后不改 agent | 0.75 | 0.568 | 0.667 | 1 |

数字出处是 [`eval/EXPERIMENTS.md`](../eval/EXPERIMENTS.md) §7.3 与 §7.6，可用 `python3 -m eval.heldout` 复现。

这个落差为什么重要：

- 只有最后一行是没被污染的泛化数字。前两行回答「调优有没有用」和「修复有没有用」，不回答「换一个账号会怎样」。
- 落差的来源不是判断层。fresh_world 上 8 个判断全部被接受，5 个 waste 与 not_waste 判断全对，注入没有生效；三个漏检和一个陷阱违规都在确定性检测与取证层。所以我会先去补线索和取证，而不是改 prompt。
- 样本只有 12 条，recall 0.75 的 Wilson 95% 区间约 [0.47, 0.91]，点估计的落差是 0.25，但 n=12 不能当成统计显著。
- 看到 1.0 就停下来是错的。c0023 对着样本外失败修完之后，那两个世界不再是样本外，这个教训直接催生了预注册的 c0024。

### 已知缺口

按价值排序，来源 [`eval/EXPERIMENTS.md`](../eval/EXPERIMENTS.md) §7.8，按预注册的规则没有在 c0024 修，因为任何一条修完，fresh_world 对这条失败就被污染，需要再造一个新世界并在改动前冻结：

1. 线索生成器补「持续高位」的稳态异常（GT02：NAT 字节费整季度持平，既没有跳变，也没有相对前 92 天基线的增长）。
2. `unattributed_transfer` 的取证器接上 `db_client_bytes_total` 或同类流量对指标（GT03：agent 里没有任何取证器引用它）。
3. 未挂载卷检测器读 `keep-until`、`purpose` 等保留 tag（TR05：带保留 tag 的卷被报成浪费，是 LLM 没参与的确定性检测器违规）。
4. 归因检测增加 ASG 成员和 job 标签的比对（GT07：现有路径只比 tag 与启动模板）。
5. 评分器对多资源标准答案按条目聚合金额（savings_accuracy 0.5385 里有 4 对是 GT 写成聚合而 agent 按资源出 finding，合计其实在范围内）。
6. 修完任何一条后再造新世界，改动前冻结。

### 个别 change 的附带说明

- c0017 与 c0020：LLM 推导第 ① 环，c0017 录制的 overlap 是 0.91，但重采样均值约 0.79，单次数字不能当能力证据；c0020 改成「只说词、不写名，名字由第 ② 环发现」之后 5 次采样均值 0.964（最小 0.91）。**量尺换了**，从对 LLM 写的正则打分改成对第 ② 环发现的名字打分，所以 0.79 到 0.964 不是同口径的提升；第 ② 环的发现规则是看过本测试服务器的指标目录后调的，换命名风格的服务器可能更低。
- c0020：两次 0.91 都丢了目标 G6 的第二条路径（跨 AZ）。
- c0023：持续异常下取证器的「超额」候选约等于 0，GT02 改取全路径 $2,948.47，超出区间上限 1.7%，next_month 的 savings_accuracy 因此从 0.8889 降到 0.8。
- 数字词与 G8：数字落地规则抓不到数字词，`not_waste` 日志仍可引用不可信数字。
- c0015：样本内 savings_accuracy 0.9167 是真实退步（GT02 选了 $2,332.80，低于下限 $2,400）。
- fresh_world 的勘误：GT06、GT12 是多资源聚合条目，GT09 的范围漏了停机实例上的卷，所以 savings_accuracy 0.5385 里有评分粒度的成分，不全是 agent 的错；precision 1.0 是宽松匹配的产物。
- 合成数据：世界是我写的，同一个作者、同一个生成器家族、同一套分类，不是独立分布；漏检率不能外推到真实账单。
- 还没有的东西：真实 AWS 账号上的运行、判断层每个变体多次采样、独立生成器的样本外、cron 在真实环境里的部署。

## 读这个项目的顺序

1. [`../README.md`](../README.md)：主线的判定规则、日报样例、核实、成绩单、进度表。
2. [`ARCHITECTURE.md`](ARCHITECTURE.md)：图和模块。
3. [`../eval/EXPERIMENTS.md`](../eval/EXPERIMENTS.md)：数字从哪来，哪些能信。
4. [`BUILD_PROCESS.md`](BUILD_PROCESS.md)：怎么用 agent 把它做出来，以及出过哪些事故。
5. [`../reports/index.html`](../reports/index.html)：所有改动、报告、图。
