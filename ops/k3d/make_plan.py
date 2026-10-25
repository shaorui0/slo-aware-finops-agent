"""Write the hand-made demo fixture runs/k8s-demo/k8s_plans.json (contract shape from c0027).

Later waves generate this file from the decision engine; here it is a fixture so the
execution path can be shown on its own. Usage: python3 ops/k3d/make_plan.py <run_dir> [observe_seconds]
"""
import json
import sys
from pathlib import Path

CTX = "k3d-finops-test"
run_dir = Path(sys.argv[1])
observe = int(sys.argv[2]) if len(sys.argv) > 2 else 45
decision_id = "K-shop-api-steady-cpu"
plan_id = f"P-{decision_id}-canary"


def patch(cpu_req, cpu_lim):
    return {"spec": {"template": {"spec": {"containers": [
        {"name": "api", "resources": {"requests": {"cpu": cpu_req}, "limits": {"cpu": cpu_lim}}}]}}}}


def cmd(p):
    return (f"kubectl --context {CTX} -n shop patch deploy api-steady --type strategic "
            f"-p '{json.dumps(p, separators=(',', ':'))}'")


plan = {
    "plan_id": plan_id, "decision_id": decision_id, "context": CTX,
    "before": {"requests": {"cpu": "500m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "256Mi"}, "replicas": 1},
    "patch": patch("100m", "200m"),
    "apply_cmd": cmd(patch("100m", "200m")),
    "rollback_cmd": cmd(patch("500m", "1")),
    "canary": {"scope": "one workload"}, "observe_seconds": observe,
    "stop_conditions": [
        {"signal": "throttle_ratio", "op": ">", "value": 0.3},
        {"signal": "p99_seconds", "op": ">", "value": 0.5},
        {"signal": "oom_killed", "op": ">", "value": 0},
        {"signal": "restarts", "op": ">", "value": 0},
    ],
    "approval_file": f"runs/approvals/{plan_id}.approved",
}
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "k8s_plans.json").write_text(json.dumps({"plans": [plan]}, indent=2) + "\n")
wrong = dict(plan, context="k3d-wrong-context")
(run_dir / "k8s_plans_wrongctx.json").write_text(json.dumps({"plans": [wrong]}, indent=2) + "\n")
print(plan_id)
