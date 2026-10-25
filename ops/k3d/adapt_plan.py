"""Check that the demo deployment equals what the generated plan assumed, and adapt `before` only if it does not.

The plan comes from `k8s run` against the synthetic world (shop/api-steady: 3 replicas, container `app`, cpu 1/2, memory 2Gi/3Gi).
ops/k3d/shop.yaml is written to match, so normally nothing is adapted. If the two ever differ this prints a loud banner and
rewrites, in the plan file, `before` and `rollback_cmd` for the touched resources to the LIVE values (the original is kept as
<plans>.generated.json). It never touches the cluster; the live deployment JSON is passed in as a file.

Usage: python3 ops/k3d/adapt_plan.py <k8s_plans.json> <live_deployment.json> <plan_id> [<plan_id> ...]
"""
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from finops_agent.k8s import act   # noqa: E402


def live_resources(plan, dep):
    cname = act.container_name(plan)
    c = next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == cname)
    res = c.get("resources") or {}
    return {"requests": res.get("requests") or {}, "limits": res.get("limits") or {}, "replicas": dep["spec"].get("replicas", 1)}


def main(argv):
    path, dep_path, ids = Path(argv[0]), Path(argv[1]), argv[2:]
    doc = json.loads(path.read_text())
    dep = json.loads(dep_path.read_text())
    diffs = []
    for plan in doc["plans"]:
        if plan["plan_id"] not in ids:
            continue
        live, only = live_resources(plan, dep), act.touched(plan)
        if act.same_resources(live, plan["before"], only):
            print(f"{plan['plan_id']}: demo deployment == synthetic world for {sorted(only)} and replicas, no adaptation needed")
            continue
        diffs.append((plan, live, only))
    if not diffs:
        return 0
    (path.with_name(path.stem + ".generated.json")).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print("=" * 78)
    print("WARNING: THE SYNTHETIC WORLD AND THE DEMO CLUSTER DIFFER. ADAPTING THE PLAN'S `before`.")
    for plan, live, only in diffs:
        before = plan["before"]
        for kind in ("requests", "limits"):
            for k in sorted(only):
                if str(before.get(kind, {}).get(k)) != str(live[kind].get(k)):
                    print(f"  {plan['plan_id']}: before.{kind}.{k}: world {before.get(kind, {}).get(k)} -> demo {live[kind].get(k)}")
        if before.get("replicas") != live["replicas"]:
            print(f"  {plan['plan_id']}: before.replicas: world {before.get('replicas')} -> demo {live['replicas']}")
        new_before = {"requests": {k: v for k, v in live["requests"].items() if k in only},
                      "limits": {k: v for k, v in live["limits"].items() if k in only}, "replicas": live["replicas"]}
        plan["before"] = new_before
        patch = copy.deepcopy(plan["patch"])
        res = patch["spec"]["template"]["spec"]["containers"][0]["resources"]
        res["requests"] = {k: new_before["requests"][k] for k in res.get("requests", {}) if k in new_before["requests"]}
        res.pop("limits", None)
        plan["rollback_cmd"] = re.sub(r"-p '.*'$", lambda m: "-p '" + json.dumps(patch, separators=(",", ":"), sort_keys=True) + "'", plan["rollback_cmd"])
        print(f"  {plan['plan_id']}: rollback_cmd now restores the demo's own values")
    print("  The patch (the part under test) is unchanged. The numbers in the report describe the synthetic world, not this cluster.")
    print("=" * 78)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
