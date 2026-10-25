## Why

c0027 的契约把执行路径定为「人批准后，只在本地 k3d 集群执行」。目前仓库里没有任何一行代码能做这件事：`remediate.py` 只打印脚本，不执行；hook 只管 AWS，不看 kubectl。要讲清 Decide → Act → Verify，必须有一条真正会写集群、又被层层闸住的路径，并且能在笔记本上复现。

## What Changes

- `hooks/aws_readonly_guard.py` 同时解析 kubectl 与 k3d 命令（K7）：读动词放行；写动词必须同时带 `--context k3d-finops-test` 和 `# INTENT:` 行；k3d 只允许操作 `finops-test`。
- 新增 `finops_agent/k8s/act.py`：审批门 → context 门 → 漂移检查 → 带 INTENT 的 canary patch → rollout → 观察期 → 停止条件 → 回滚 → 验证，每次写 `runs/<id>/k8s_actions.jsonl`。
- 新增本地 k3d 演示：`make k8s-demo` / `make k8s-down`，用公开镜像，演示拒绝、自动回滚、成功验证三条路径。

## 人工 gate 与回滚

这个 change 带写路径，所以明确写出：

- 人工 gate：`runs/approvals/<plan_id>.approved` 由人创建；文件不存在，act.py 拒绝执行。act.py 自己从不创建该文件。
- 范围 gate：plan 的 `context` 不是 `k3d-finops-test` 就拒绝；每条 kubectl 调用都从 plan 取 `--context`，没有任何默认 context。hook 另外在 Claude Code 里拦截其他 context 的 kubectl 写操作。
- 漂移 gate：执行前读回实时资源，与 `before` 不一致就拒绝。
- 回滚：观察期内命中任一停止条件，自动执行 `rollback_cmd`，等 rollout 完成，并验证资源回到 `before`；验证不通过则记为失败并报告，不再继续改动。
- 不涉及 AWS 写操作；不触碰任何其他 k8s 集群（含维护者工作集群）。

## Capabilities

### New Capabilities
- `k8s-act`：受控执行路径与 kubectl guard。

### Modified Capabilities
（无；plan JSON 形状沿用 c0027 `k8s-mainline` 契约，不改）

## Impact

新增代码在 `hooks/`、`finops_agent/k8s/act.py`、`ops/k3d/`、`scripts/k8s_demo.sh`；Makefile 只加两个 target。demo 需要 docker 与 k3d，不进 `make test`。
