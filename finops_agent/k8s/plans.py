"""Plans: ALLOW and REQUIRE_APPROVAL decisions -> execution plans in the contract shape (k8s-mainline spec).

Pure function of the decision and the policy; nothing is executed here (act.py does that, behind an approval file).
DENY, UNKNOWN and NOOP produce no plan. Details of field values: openspec/changes/c0031-k8s-pipeline/specs/k8s-pipeline/spec.md.
"""
from __future__ import annotations

import json

CONTEXT = "k3d-finops-test"
OBSERVE_SECONDS = 120
ACTIONABLE = ("ALLOW", "REQUIRE_APPROVAL")


def quantity(resource: str, value: float) -> str:
    """kubectl-style quantity: cpu '2' or '250m'; memory '2Gi' or '1152Mi'."""
    if resource == "cpu":
        return str(int(round(value))) if abs(value - round(value)) < 1e-9 else f"{int(round(value * 1000))}m"
    return f"{int(round(value))}Gi" if abs(value - round(value)) < 1e-9 else f"{int(round(value * 1024))}Mi"


def plan_id(d: dict) -> str:
    return f"P-{d['id']}-{d['recommendation']['request']:g}"


def _patch(container: str, resource: str, q: str) -> dict:
    return {"spec": {"template": {"spec": {"containers": [{"name": container, "resources": {"requests": {resource: q}}}]}}}}


def _cmd(ns: str, workload: str, patch: dict) -> str:
    return (f"kubectl --context {CONTEXT} -n {ns} patch deploy {workload} --type strategic "
            f"-p '{json.dumps(patch, separators=(',', ':'), sort_keys=True)}'")


def stop_conditions(d: dict, policy: dict) -> list[dict]:
    out = []
    if d["resource"] == "cpu":
        out.append({"signal": "throttle_ratio", "op": ">", "value": policy["max_throttle"]})
    p99 = (d.get("slo") or {}).get("objectives", {}).get("p99_seconds")
    if p99 is not None:
        out.append({"signal": "p99_seconds", "op": ">", "value": p99})
    if d["resource"] == "memory":
        out.append({"signal": "oom_killed", "op": ">", "value": 0})
    out.append({"signal": "restarts", "op": ">", "value": 0})
    return out


def build_plan(d: dict, policy: dict) -> dict | None:
    """The plan for one decision, or None for DENY / UNKNOWN / NOOP. Raises ValueError when a plan is due but cannot be built."""
    if d["verdict"] not in ACTIONABLE:
        return None
    cur, rec, res = d["current"], d["recommendation"], d["resource"]
    if not d.get("container"):
        raise ValueError("no container name in the decision (kube_deployment_spec_replicas has no container label)")
    if rec.get("request") is None or cur.get("request") is None or cur.get("replicas") is None:
        raise ValueError("no pre-change snapshot or no recommendation")
    before_q, after_q = quantity(res, cur["request"]), quantity(res, rec["request"])
    ns, wl, c = d["namespace"], d["workload"], d["container"]
    pid = plan_id(d)
    return {
        "plan_id": pid, "decision_id": d["id"], "context": CONTEXT,
        "before": {"requests": {res: before_q}, "limits": {res: quantity(res, cur["limit"])} if cur.get("limit") else {},
                   "replicas": cur["replicas"]},
        "patch": _patch(c, res, after_q),
        "apply_cmd": _cmd(ns, wl, _patch(c, res, after_q)),
        "rollback_cmd": _cmd(ns, wl, _patch(c, res, before_q)),
        "canary": {"scope": "one workload"}, "observe_seconds": OBSERVE_SECONDS,
        "stop_conditions": stop_conditions(d, policy),
        "approval_file": f"runs/approvals/{pid}.approved",
    }


def build_plans(decisions: list[dict], policy: dict, only: set | None = None) -> tuple[list[dict], list[dict]]:
    """-> (plans, skipped). `only`: decision ids that may get a plan (the ledger's active proposals); None = all actionable."""
    plans, skipped = [], []
    for d in decisions:
        if d["verdict"] not in ACTIONABLE or (only is not None and d["id"] not in only):
            continue
        try:
            plans.append(build_plan(d, policy))
        except ValueError as e:
            skipped.append({"decision_id": d["id"], "why": str(e)})
    return plans, skipped
