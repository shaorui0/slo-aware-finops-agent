## 1. 契约
- [x] 1.1 `specs/k8s-mainline/spec.md`：世界 schema、指标名、推荐器、两种节省口径、策略引擎 A–D 与判定顺序、决策文件、执行计划、台账、报告、评估

## 2. 文档收窄
- [x] 2.1 README：改名 SLO-aware FinOps Agent；定位、三者分离原则、与 AWS 原生能力的差异；现有 12 类改称「附录：广度扫描（冻结）」
- [x] 2.2 REQUIREMENTS：R20 先深后广；主线交付物 K1–K8；§5 覆盖矩阵标注主线与附录
- [x] 2.3 MATURITY：新增 k8s-depth 一节，DoD 带 verify
- [x] 2.4 NARRATIVE：标题与开头改为新定位，原 12 项能力保留为附录证据

## 3. 验收
- [x] 3.1 `python3 tools/check_rules.py --docs` 通过
- [x] 3.2 `make test` 与 `make eval` 数字不变（本 change 不改代码）
