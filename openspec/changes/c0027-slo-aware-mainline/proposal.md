## Why

维护者 10-25 的需求文档把首个可交付版本定成：K8s workload 的 CPU/Memory requests 优化，以 SLO 和容量为硬约束，以可验证的净节省为目标。现有仓库的方向是反的。它铺开了 12 种 AWS 浪费（EBS、EIP、NAT、SP、tag、跨 AZ…），没有 K8s，没有 requests，没有策略引擎，也没有执行与验证的闭环。维护者的口径是「要一个做深，不要面面俱到」：CPU 和 memory 这一项能跑通、能持续跑通、讲得清，就够了。

项目因此改名为 **SLO-aware FinOps Agent**。核心设计原则是：Metrics 描述资源实际发生了什么，SLO 描述业务允许发生什么，Requests 描述调度器应为工作负载预留多少资源，这三者不能互相替代。

## What Changes

- 改名，并把主线收窄到 K8s CPU/Memory requests。README、NARRATIVE、MATURITY 的标题与定位随之改写。
- 现有 12 类 AWS 检测器、`world.yaml`、`make eval` 保持可运行，CI 不变。文档里把它们改称「附录：广度扫描（冻结）」。
- `docs/REQUIREMENTS.md` 新增 R20（先深后广）和主线交付物 K1–K8。
- `docs/MATURITY.md` 新增 `k8s-depth` 一节，列出带 verify 的 DoD。
- 新增接口契约 `specs/k8s-mainline/spec.md`：世界文件 schema、指标名、决策 JSON、执行计划、提案台账、判定规则。后续 change（c0028 起）按这份契约并行实现。

## Capabilities

### New Capabilities
- `k8s-mainline`：主线的接口契约。

### Modified Capabilities
（无）

## Impact

本 change 只改文档和 openspec，不改代码，`make test` 与 `make eval` 的数字不变。执行权限定为：人批准后，只在本地 k3d 集群（context `k3d-finops-test`）执行；不涉及 AWS 写操作。GitHub 仓库改名对外可见，走 escrow，本 change 不做。
