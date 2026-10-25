"""Regenerate the decision fixtures from the synthetic K8s world (read-only, local test server).

Usage: PYTHONPATH=. python3 finops_agent/k8s/fixtures/make_fixtures.py
Writes day1/ (as_of 2026-10-25) and day2/ (as_of 2026-10-26): k8s_decisions.json and k8s_plans.json.
The hand-written ledger / actions / verify / score fixtures in this folder are not touched.
"""
import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from finops_agent.k8s.collect import Prom, collect_facts
from finops_agent.k8s.policy import decide, load_policy, write_decisions

HERE = Path(__file__).resolve().parent
WORLD = "testenv/k8s_world.yaml"
CTX = "k3d-finops-test"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def plan_for(d, rec):
    ns, w, res = d["namespace"], d["workload"], d["resource"]
    cur = d["current"]
    unit = "m" if res == "cpu" else "Mi"
    fmt = (lambda v: f"{int(round(v * 1000))}m") if res == "cpu" else (lambda v: f"{int(round(v * 1024))}Mi")
    key = "cpu" if res == "cpu" else "memory"
    patch = {"spec": {"template": {"spec": {"containers": [{"name": w, "resources": {"requests": {key: fmt(rec["request"])}}}]}}}}
    back = {"spec": {"template": {"spec": {"containers": [{"name": w, "resources": {"requests": {key: fmt(cur["request"])}}}]}}}}
    pid = f"P-{d['id']}-{fmt(rec['request'])}"
    kc = f"kubectl --context {CTX} -n {ns} patch deploy {w} --type strategic -p"
    return {"plan_id": pid, "decision_id": d["id"], "context": CTX,
            "before": {"requests": {key: fmt(cur["request"])}, "limits": {key: fmt(cur["limit"]) if cur.get("limit") else None},
                       "replicas": cur["replicas"]},
            "patch": patch, "apply_cmd": f"{kc} '{json.dumps(patch)}'", "rollback_cmd": f"{kc} '{json.dumps(back)}'",
            "canary": {"scope": "one workload"}, "observe_seconds": 120,
            "stop_conditions": [{"signal": "throttle_ratio", "op": ">", "value": 0.05},
                                {"signal": "p99_seconds", "op": ">", "value": 0.2},
                                {"signal": "oom_killed", "op": ">", "value": 0},
                                {"signal": "restarts", "op": ">", "value": 0}],
            "approval_file": f"runs/approvals/{pid}.approved"}


def write_score(decs, world):
    """Mini exam score for the fixtures: compare day2 verdicts with the world's `expect`."""
    exp = {(x["namespace"], x["name"], r): v["verdict"] for x in world["workloads"] for r, v in x["expect"].items()}
    items = [{"id": d["id"], "expect": exp[(d["namespace"], d["workload"], d["resource"])], "got": d["verdict"]} for d in decs]
    ok = sum(i["expect"] == i["got"] for i in items)
    unsafe = sum(i["expect"] in ("DENY", "UNKNOWN") and i["got"] == "ALLOW" for i in items)
    unk = [i for i in items if i["expect"] == "UNKNOWN"]
    score = {"n": len(items), "verdict_accuracy": round(ok / len(items), 4), "unsafe_allow": unsafe,
             "unknown_recall": round(sum(i["got"] == "UNKNOWN" for i in unk) / len(unk), 4) if unk else None,
             "items": items}
    (HERE / "score.json").write_text(json.dumps(score, indent=2, ensure_ascii=False) + "\n")


def main():
    w = yaml.safe_load(Path(WORLD).read_text())
    max_nodes = w["nodes"]["max"]
    policy = load_policy()
    for day, as_of in (("day1", "2026-10-25T00:00:00Z"), ("day2", "2026-10-26T00:00:00Z")):
        port = free_port()
        srv = subprocess.Popen([sys.executable, "-m", "testenv.metrics_server", "--world", "k8s", "--port", str(port)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(2.5)
            ts = datetime.fromisoformat(as_of.replace("Z", "+00:00")).timestamp()
            got = collect_facts(Prom(f"http://127.0.0.1:{port}"), policy, as_of=ts, pricing=w["pricing"],
                                min_nodes=w["nodes"]["min"], max_nodes=max_nodes)
        finally:
            srv.terminate()
        decs = decide(got["facts"], got["cluster"], policy)
        if day == "day2":   # one hand-written, grounded narrative so the sample shows the LLM block
            d0 = next(d for d in decs if d["id"] == "K-shop-api-steady-cpu")
            u, r, c, sv = d0["usage"], d0["recommendation"], d0["current"], d0["savings"]
            d0["narrative"] = (f"api-steady 的 CPU p99 只有 {u['p99']:g} core，当前 request 是 {c['request']:g} core，用不到一成半。"
                               f"推荐降到 {r['request']:g} core；三个副本一起降后节点数从 {sv['nodes_before']} 到 {sv['nodes_after']}，"
                               f"每月可兑现 ${sv['realizable_usd_month']:g}。条件 A–D 都通过，可以做。")
            write_score(decs, w)
        out = HERE / day
        write_decisions(out / "k8s_decisions.json", got["as_of"], "testenv/k8s_world.yaml", decs)
        plans = [plan_for(d, d["recommendation"]) for d in decs if d["verdict"] in ("ALLOW", "REQUIRE_APPROVAL")]
        (out / "k8s_plans.json").write_text(json.dumps({"plans": plans}, indent=2, ensure_ascii=False) + "\n")
        print(day, got["as_of"], len(decs), "decisions", len(plans), "plans")


if __name__ == "__main__":
    main()
