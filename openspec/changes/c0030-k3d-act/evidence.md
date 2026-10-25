# c0030 验收证据（原始输出）

## 1. hook 单测
```
$ python3 -W ignore -m unittest discover -s hooks -p 'test_*.py'
test_allowed (test_aws_readonly_guard.GuardTest.test_allowed) ... ok
test_blocked (test_aws_readonly_guard.GuardTest.test_blocked) ... ok
test_hook_protocol (test_aws_readonly_guard.GuardTest.test_hook_protocol) ... ok
test_hook_protocol_k8s (test_aws_readonly_guard.GuardTest.test_hook_protocol_k8s) ... ok
test_k8s_allowed (test_aws_readonly_guard.GuardTest.test_k8s_allowed) ... ok
test_k8s_blocked (test_aws_readonly_guard.GuardTest.test_k8s_blocked) ... ok

----------------------------------------------------------------------
Ran 6 tests in 0.135s

OK
```

## 2. act.py 单测（mock subprocess）
```
$ python3 -W ignore -m unittest finops_agent.k8s.test_act -v
test_approval_name_must_match_plan_id (finops_agent.k8s.test_act.ActTest.test_approval_name_must_match_plan_id) ... [act 16:19:29] outcome=refused reason=approval_file name must be P-K-shop-api-steady-cpu-canary.approved
ok
test_drift_refused (finops_agent.k8s.test_act.ActTest.test_drift_refused) ... [act 16:19:29] outcome=refused reason=state drift: live resources differ from plan.before
ok
test_every_kubectl_call_carries_context (finops_agent.k8s.test_act.ActTest.test_every_kubectl_call_carries_context) ... [act 16:19:29] gates passed for P-K-shop-api-steady-cpu-canary; applying
ok
test_foreign_context_in_command_refused (finops_agent.k8s.test_act.ActTest.test_foreign_context_in_command_refused) ... [act 16:19:29] outcome=refused reason=plan command carries a different --context
ok
test_kubectl_refuses_other_context (finops_agent.k8s.test_act.ActTest.test_kubectl_refuses_other_context) ... ok
test_metrics_unavailable_fails_safe (finops_agent.k8s.test_act.ActTest.test_metrics_unavailable_fails_safe) ... [act 16:19:29] gates passed for P-K-shop-api-steady-cpu-canary; applying
ok
test_missing_approval_refused (finops_agent.k8s.test_act.ActTest.test_missing_approval_refused) ... [act 16:19:29] outcome=refused reason=approval file missing: runs/approvals/P-K-shop-api-steady-cpu-canary.approved
ok
test_non_kubectl_or_bad_verb_refused (finops_agent.k8s.test_act.ActTest.test_non_kubectl_or_bad_verb_refused) ... [act 16:19:29] outcome=refused reason=plan command does not start with kubectl: 'rm -rf /'
ok
test_oom_triggers_rollback (finops_agent.k8s.test_act.ActTest.test_oom_triggers_rollback) ... [act 16:19:29] gates passed for P-K-shop-api-steady-cpu-canary; applying
ok
test_p99_triggers_rollback (finops_agent.k8s.test_act.ActTest.test_p99_triggers_rollback) ... [act 16:19:29] gates passed for P-K-shop-api-steady-cpu-canary; applying
ok
test_quantities_compare_semantically (finops_agent.k8s.test_act.ActTest.test_quantities_compare_semantically) ... ok
test_rollback_that_does_not_restore_is_failed (finops_agent.k8s.test_act.ActTest.test_rollback_that_does_not_restore_is_failed) ... [act 16:19:29] gates passed for P-K-shop-api-steady-cpu-canary; applying
ok
test_rollout_failure_rolls_back (finops_agent.k8s.test_act.ActTest.test_rollout_failure_rolls_back) ... [act 16:19:29] gates passed for P-K-shop-api-steady-cpu-canary; applying
ok
test_status_and_cli (finops_agent.k8s.test_act.ActTest.test_status_and_cli) ... [act 16:19:29] gates passed for P-K-shop-api-steady-cpu-canary; applying
unknown plan id nope; have: ['P-K-shop-api-steady-cpu-canary']
ok
test_throttle_triggers_rollback_and_verifies (finops_agent.k8s.test_act.ActTest.test_throttle_triggers_rollback_and_verifies) ... [act 16:19:29] gates passed for P-K-shop-api-steady-cpu-canary; applying
ok
test_verified (finops_agent.k8s.test_act.ActTest.test_verified) ... [act 16:19:29] gates passed for P-K-shop-api-steady-cpu-canary; applying
ok
test_wrong_context_refused (finops_agent.k8s.test_act.ActTest.test_wrong_context_refused) ... [act 16:19:29] outcome=refused reason=context 'k3d-cre6630' != k3d-finops-test
ok

----------------------------------------------------------------------
Ran 17 tests in 0.034s

OK
```

## 3. make k8s-demo 全新集群端到端（runs/k8s-demo/demo.log 原文）

首轮运行暴露一个问题：旧 loadgen pod 在滚动更新后仍存活约 30s，继续打 40 rps，导致成功路径被误判回滚。修复：loadgen 改 Recreate，两个 workload 设 terminationGracePeriodSeconds: 1。以下是修复后、先 make k8s-down 再从零创建集群的一次完整运行。
```

=== [1] create cluster finops-test (idempotent)
INFO[0007] You can now use it like this:                
kubectl config use-context k3d-finops-test
kubectl cluster-info
INFO[0003] Successfully imported 1 image(s) into 1 cluster(s) 
NAME                       STATUS   ROLES                  AGE   VERSION
k3d-finops-test-server-0   Ready    control-plane,master   6s    v1.33.6+k3s1

=== [2] deploy namespace shop: api-steady (over-provisioned) + loadgen
# INTENT: create the demo workload in the disposable test cluster
namespace/shop created
configmap/demo-scripts created
service/api-steady created
deployment.apps/api-steady created
deployment.apps/loadgen created
Waiting for deployment "api-steady" rollout to finish: 0 of 1 updated replicas are available...
deployment "api-steady" successfully rolled out
deployment "loadgen" successfully rolled out
resources before: {"limits":{"cpu":"1","memory":"256Mi"},"requests":{"cpu":"500m","memory":"128Mi"}}
baseline metrics: http_request_duration_p99_seconds 0.0161 cpu_throttle_ratio 0.0000 http_requests_total 39 container_cpu_cfs_periods_total 54 container_cpu_cfs_throttled_periods_total 0 

=== [3] write k8s_plans.json (hand-written fixture in the c0027 contract shape)
plan: P-K-shop-api-steady-cpu-canary  observe_seconds=45

=== [4] refusal 1: no approval file
[act 16:17:04] outcome=refused reason=approval file missing: runs/approvals/P-K-shop-api-steady-cpu-canary.approved
exit=3 (3 = refused)

=== [5] refusal 2: wrong context (copy of the plan edited)
[act 16:17:04] outcome=refused reason=context 'k3d-wrong-context' != k3d-finops-test
exit=3 (3 = refused)
resources unchanged: {"limits":{"cpu":"1","memory":"256Mi"},"requests":{"cpu":"500m","memory":"128Mi"}}

=== [6] human approves: create the approval file
P-K-shop-api-steady-cpu-canary.approved

=== [7] inject heavy load (40 rps), apply canary: expect stop condition and automatic rollback
# INTENT: set loadgen to 40 rps to drive the canary
deployment.apps/loadgen env updated
Waiting for deployment "loadgen" rollout to finish: 0 out of 1 new replicas have been updated...
Waiting for deployment "loadgen" rollout to finish: 0 out of 1 new replicas have been updated...
Waiting for deployment "loadgen" rollout to finish: 0 out of 1 new replicas have been updated...
Waiting for deployment "loadgen" rollout to finish: 0 of 1 updated replicas are available...
deployment "loadgen" successfully rolled out
metrics under heavy load before patch: http_request_duration_p99_seconds 0.0234 cpu_throttle_ratio 0.0000 http_requests_total 464 container_cpu_cfs_periods_total 184 container_cpu_cfs_throttled_periods_total 0 
[act 16:17:18] gates passed for P-K-shop-api-steady-cpu-canary; applying
[act 16:17:18] outcome=applied reason=patch applied, observing
[act 16:17:20] observe {'throttle_ratio': 0.0, 'p99_seconds': 0.012, 'restarts': 0, 'oom_killed': 0}
[act 16:17:25] observe {'throttle_ratio': 0.9259, 'p99_seconds': 0.2923, 'restarts': 0, 'oom_killed': 0}
[act 16:17:25] STOP condition hit: {'signal': 'throttle_ratio', 'op': '>', 'value': 0.3, 'observed': 0.9259}; rolling back
[act 16:17:27] outcome=rolled_back reason=stop condition throttle_ratio > 0.3 (observed 0.9259); resources verified equal to before
exit=4 (4 = rolled_back)
resources after rollback: {"limits":{"cpu":"1","memory":"256Mi"},"requests":{"cpu":"500m","memory":"128Mi"}}

=== [8] reset to normal load (5 rps), apply again: expect verified
# INTENT: set loadgen to 5 rps to drive the canary
deployment.apps/loadgen env updated
Waiting for deployment "loadgen" rollout to finish: 0 out of 1 new replicas have been updated...
Waiting for deployment "loadgen" rollout to finish: 0 out of 1 new replicas have been updated...
Waiting for deployment "loadgen" rollout to finish: 0 of 1 updated replicas are available...
deployment "loadgen" successfully rolled out
[act 16:17:41] gates passed for P-K-shop-api-steady-cpu-canary; applying
[act 16:17:41] outcome=applied reason=patch applied, observing
[act 16:17:43] observe {'p99_seconds': 0.0, 'throttle_ratio': 0.0, 'restarts': 0, 'oom_killed': 0}
[act 16:17:48] observe {'p99_seconds': 0.0121, 'throttle_ratio': 0.0, 'restarts': 0, 'oom_killed': 0}
[act 16:17:53] observe {'p99_seconds': 0.0122, 'throttle_ratio': 0.0, 'restarts': 0, 'oom_killed': 0}
[act 16:17:58] observe {'p99_seconds': 0.0123, 'throttle_ratio': 0.0, 'restarts': 0, 'oom_killed': 0}
[act 16:18:03] observe {'p99_seconds': 0.0124, 'throttle_ratio': 0.0, 'restarts': 0, 'oom_killed': 0}
[act 16:18:08] observe {'p99_seconds': 0.0124, 'throttle_ratio': 0.0, 'restarts': 0, 'oom_killed': 0}
[act 16:18:13] observe {'p99_seconds': 0.0124, 'throttle_ratio': 0.0, 'restarts': 0, 'oom_killed': 0}
[act 16:18:18] observe {'p99_seconds': 0.0124, 'throttle_ratio': 0.0, 'restarts': 0, 'oom_killed': 0}
[act 16:18:23] observe {'p99_seconds': 0.0122, 'throttle_ratio': 0.0, 'restarts': 0, 'oom_killed': 0}
[act 16:18:29] observe {'p99_seconds': 0.1006, 'throttle_ratio': 0.0142, 'restarts': 0, 'oom_killed': 0}
[act 16:18:29] outcome=verified reason=observed 45s without a stop condition; live resources match the patch
exit=0 (0 = verified)
resources after canary: {"limits":{"cpu":"200m","memory":"256Mi"},"requests":{"cpu":"100m","memory":"128Mi"}}
final metrics: http_request_duration_p99_seconds 0.1006 cpu_throttle_ratio 0.0142 http_requests_total 231 container_cpu_cfs_periods_total 476 container_cpu_cfs_throttled_periods_total 2 

=== [9] summary
P-K-shop-api-steady-cpu-canary	verified	observed 45s without a stop condition; live resources match the patch
--- k8s_actions.jsonl (outcome, reason)
2026-10-25T16:17:04+00:00  refused      approval file missing: runs/approvals/P-K-shop-api-steady-cpu-canary.approved
2026-10-25T16:17:04+00:00  refused      context 'k3d-wrong-context' != k3d-finops-test
2026-10-25T16:17:18+00:00  applied      patch applied, observing
2026-10-25T16:17:27+00:00  rolled_back  stop condition throttle_ratio > 0.3 (observed 0.9259); resources verified equal to before
2026-10-25T16:17:41+00:00  applied      patch applied, observing
2026-10-25T16:18:29+00:00  verified     observed 45s without a stop condition; live resources match the patch
DEMO OK: refusals, rollback and verified path all behaved
```

## 4. 收尾
```
$ make k8s-down
Successfully deleted cluster finops-test!
$ k3d cluster list finops-test
FATA No nodes found for given cluster
```

## 5. 规则检查
```
$ python3 tools/check_rules.py --docs
check_rules: OK
```
