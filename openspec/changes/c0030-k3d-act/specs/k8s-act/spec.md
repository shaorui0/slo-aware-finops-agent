## ADDED Requirements

### Requirement: kubectl 与 k3d guard

同一个 PreToolUse hook（fail-closed）在 AWS 规则之外解析 kubectl 与 k3d 命令。

- 读动词放行：get, describe, top, logs, rollout status, version, config view|get-contexts|current-context, api-resources, auth can-i, wait。
- 写动词（apply, create, patch, scale, set, edit, delete, rollout restart|undo, label, annotate, replace, cordon, drain, taint）仅当同时满足：存在显式 `--context k3d-finops-test`（或 `--context=`形式）；命令文本里在 kubectl 之前有 `# INTENT:` 行。否则 exit 2，stderr 说明原因。
- 其他动词、无法解析的 kubectl 命令一律拦截。
- k3d：`cluster create|delete|list|start|stop finops-test` 与 `kubeconfig ...` 放行；点名其他集群拦截。

#### Scenario: 其他 context 被拦
- **WHEN** 命令是 `kubectl --context k3d-cre6630 delete pod x`
- **THEN** hook 退出码 2

#### Scenario: 缺 INTENT 被拦
- **WHEN** 写命令带正确 context 但没有 `# INTENT:` 行
- **THEN** hook 退出码 2

### Requirement: act.py 执行顺序

`python3 -m finops_agent.k8s.act apply --plans <file> --plan-id <id>` 依次执行，任一步失败即停：
1. plan.context 必须是 `k3d-finops-test`，否则 refused。
2. approval 文件存在，否则 refused。
3. 读回实时 requests/limits/replicas，与 `before` 不同则 refused（drift）。
4. 执行 apply_cmd，命令串前置 `# INTENT:` 行。
5. `kubectl rollout status`。
6. 观察 `observe_seconds`，轮询 throttle_ratio、p99_seconds（demo app /metrics），restarts、oom_killed（kubectl）。
7. 命中停止条件：执行 rollback_cmd，rollout status，验证资源等于 `before`。
8. 结果写入 `runs/<id>/k8s_actions.jsonl`：`outcome` 取 applied|verified|rolled_back|refused|failed，含 reason、时间戳、观察值。

所有 kubectl 调用经同一个函数，该函数总是传 plan 的 `--context`。`status` 子命令读 jsonl 汇总。

### Requirement: 本地演示

`make k8s-demo` 创建 `finops-test` 集群并依次演示：无审批拒绝、错 context 拒绝、审批后 canary + 负载触发自动回滚并验证、复位后正常负载成功验证。日志写 `runs/k8s-demo/demo.log`。`make k8s-down` 只删除 `finops-test`。
