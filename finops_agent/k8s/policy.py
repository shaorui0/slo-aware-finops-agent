"""Policy engine: conditions A-D and the six-step verdict order. Deterministic, no LLM, no I/O (except load/write).

Contract: openspec/changes/c0027-slo-aware-mainline/specs/k8s-mainline/spec.md
An input that is missing makes the condition UNKNOWN; UNKNOWN is never treated as a pass.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from finops_agent.k8s import packing
from finops_agent.k8s.recommend import is_noop, recommend

POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "k8s_policy.yaml"


def load_policy(path=None) -> dict:
    return yaml.safe_load(Path(path or POLICY_PATH).read_text())


def _r(x):
    """Round floats to 6 places, recursively, so the output is byte-stable."""
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, dict):
        return {k: _r(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_r(v) for v in x]
    return x


def _cond(status, why, f, keys, needs=None):
    ids = [f["ev"][k] for k in keys if k in f.get("ev", {})]
    return {"status": status, "why": why, "evidence": ids, "_needs": needs or []}


def _combine(fails, unknowns, passed_why, f, keys):
    if fails:
        return _cond("FAIL", "；".join(fails), f, keys)
    if unknowns:
        return _cond("UNKNOWN", "；".join(w for w, _ in unknowns), f, keys, [n for _, n in unknowns])
    return _cond("PASS", passed_why, f, keys)


# ---- conditions -------------------------------------------------------------
def cond_a(f, rec, policy):
    u, res = f["usage"], f["resource"]
    label = "CPU" if res == "cpu" else "内存"
    key = "p99" if res == "cpu" else "max"
    days, cov = u.get("valid_days") or 0, u.get("coverage") or 0.0
    unknowns = []
    if f["current"].get("request") is None:
        unknowns.append(("缺少当前 request", "需要当前 request（kube_pod_container_resource_requests）"))
    if u.get(key) is None:
        unknowns.append((f"没有{label}使用量", f"需要{label}使用量数据"))
    if days < policy["min_days"]:
        unknowns.append((f"有效天数 {days} 不足 {policy['min_days']}",
                         f"需要至少 {policy['min_days']} 天的 {label} 使用数据（当前 {days} 天）"))
    if cov < policy["min_coverage"]:
        unknowns.append((f"样本覆盖率 {cov:.2f} 低于 {policy['min_coverage']:.2f}",
                         f"需要样本覆盖率至少 {policy['min_coverage']:.2f}（当前 {cov:.2f}）"))
    return _combine([], unknowns, f"有效天数 {days} ≥ {policy['min_days']}，样本覆盖率 {cov:.2f} ≥ {policy['min_coverage']:.2f}",
                    f, ["valid_days", "coverage", key])


def cond_b(f, rec, cluster, policy):
    u, res, cur = f["usage"], f["resource"], f["current"]
    fails, unknowns = [], []
    margin = policy["margin"]
    pending = cluster.get("pending")
    if res == "cpu":
        keys = ["p99", "throttle", "pending"]
        if rec is None or u.get("p99") is None:
            unknowns.append(("缺少 CPU p99 或推荐值", "需要 CPU 使用量 p99"))
        elif rec["request"] < u["p99"] * (1 + margin) - 1e-9:
            fails.append(f"推荐 {rec['request']:g} core 低于 p99 {u['p99']:g} × (1+{margin:g})")
        thr = u.get("throttle_peak")
        if thr is None:
            unknowns.append(("缺少 CFS throttling 数据", "需要 container_cpu_cfs_throttled_periods_total 与 container_cpu_cfs_periods_total"))
        elif thr >= policy["max_throttle"]:
            fails.append(f"高峰期 throttling 比例 {thr:.3f} ≥ {policy['max_throttle']:g}")
        ok = f"推荐 ≥ p99 × (1+{margin:g})，高峰期 throttling {thr if thr is None else format(thr, '.3f')} < {policy['max_throttle']:g}"
    else:
        keys = ["max", "oom", "limit", "pending"]
        if rec is None or u.get("max") is None:
            unknowns.append(("缺少内存峰值或推荐值", "需要内存 working set 峰值"))
        elif rec["request"] < u["max"] * (1 + margin) - 1e-9:
            fails.append(f"推荐 {rec['request']:g} GiB 低于峰值 {u['max']:g} × (1+{margin:g})")
        oom = u.get("oom_events")
        if oom is None:
            unknowns.append(("缺少 OOMKilled 数据", "需要 kube_pod_container_status_last_terminated_reason"))
        elif oom > 0:
            fails.append(f"窗口内有 {oom:g} 次 OOMKilled")
        lim = cur.get("limit")
        if lim and u.get("max") is not None and u["max"] >= policy["oom_guard"] * lim:
            fails.append(f"峰值 {u['max']:g} GiB ≥ {policy['oom_guard']:g} × limit {lim:g} GiB，已贴近 OOM")
        ok = f"推荐 ≥ 峰值 × (1+{margin:g})，无 OOMKilled，" + (f"峰值 < {policy['oom_guard']:g} × limit" if lim else "未设 limit，OOM 判据不适用")
    if pending is None:
        unknowns.append(("缺少集群 Pending pod 数", "需要 kube_pod_status_phase{phase=\"Pending\"}"))
    elif pending > 0:
        fails.append(f"集群有 {pending:g} 个 Pending pod")
    return _combine(fails, unknowns, ok + "，集群无 Pending", f, keys)


def cond_c(f, policy):
    s = f.get("slo") or {}
    burn, budget = s.get("burn_rate_1h"), s.get("budget_remaining")
    job, deadline = s.get("job_max_duration"), s.get("deadline_seconds")
    keys = ["burn", "budget", "job", "deadline", "obj_availability", "obj_p99_seconds"]
    sd = f.get("slo_def") or {}
    defined = sd.get("defined")   # None = no SLO catalog given (c0029 behaviour); False = the catalog has no entry for this workload
    if defined is False:
        return _cond("UNKNOWN", "SLO 目录里没有该 workload 的定义，指标里的 slo_* 序列不能替代", f, keys,
                     ["需要在 SLO 目录里为该 workload 定义 SLO（availability / p99_seconds / 批任务 deadline_seconds）"])
    if defined:
        has_slo, has_job = bool(sd.get("objectives")), deadline is not None
    else:
        has_slo, has_job = burn is not None or budget is not None, job is not None
    if not has_slo and not has_job:
        return _cond("UNKNOWN", "未定义 SLO，不能用利用率替代", f, keys,
                     ["需要为该 workload 定义 SLO（slo_error_budget_remaining_ratio、slo_burn_rate_1h 或批任务 deadline）"])
    fails, unknowns, oks = [], [], []
    if has_slo:
        if burn is None or budget is None:
            unknowns.append(("SLO 指标不完整", "需要同时有 slo_burn_rate_1h 与 slo_error_budget_remaining_ratio"))
        else:
            if burn > 1:
                fails.append(f"1h 燃烧率 {burn:g} > 1，服务正在消耗错误预算")
            if budget < policy["min_budget"]:
                fails.append(f"剩余错误预算 {budget:g} < {policy['min_budget']:g}")
            oks.append(f"燃烧率 {burn:g} ≤ 1，剩余预算 {budget:g} ≥ {policy['min_budget']:g}")
    if has_job:
        if deadline is None:
            unknowns.append(("批任务缺少 deadline", "需要批任务的 deadline_seconds"))
        elif job is None:
            unknowns.append(("批任务缺少耗时指标", "需要 job_last_duration_seconds"))
        elif job > policy["deadline_guard"] * deadline:
            fails.append(f"批任务最长耗时 {job:g}s > {policy['deadline_guard']:g} × deadline {deadline:g}s")
        else:
            oks.append(f"批任务最长耗时 {job:g}s ≤ {policy['deadline_guard']:g} × deadline {deadline:g}s")
    return _combine(fails, unknowns, "；".join(oks), f, keys)


def cond_d(f, rec, cluster, base_pods, sav, policy):
    cur, res = f["current"], f["resource"]
    keys = ["request", "replicas", "alloc_cpu", "alloc_mem", "node_count", "pods"]
    mine = [p for p in base_pods if p.get("namespace") == f["namespace"] and p.get("workload") == f["workload"]]
    unknowns = []
    if cur.get("request") is None or cur.get("replicas") is None:
        unknowns.append(("缺少变更前快照（request 或副本数）", "需要当前 request 与副本数以生成快照和回滚命令"))
    if rec is None:
        unknowns.append(("没有推荐值，无法模拟缩容", "需要先有推荐值"))
    if not mine or not cluster.get("pods"):
        unknowns.append(("缺少 pod 级 requests，无法装箱", "需要 kube_pod_container_resource_requests 的 pod 级数据"))
    if unknowns:
        return _combine([], unknowns, "", f, keys)
    delta = max(0.0, cur["request"] - rec["request"])
    need = delta * cur["replicas"]
    # contract: capacity of the simulated post-change cluster (nodes_after); a caller that knows the autoscaler
    # ceiling can pass cluster["max_nodes"] to count the nodes a rollback could scale back up to.
    cap_nodes = max(sav["nodes_after"], cluster.get("max_nodes") or 0)
    free = packing.free_after(base_pods, cluster, f["namespace"], f["workload"], res, rec["request"], cap_nodes)
    unit = "core" if res == "cpu" else "GiB"
    if free + 1e-9 < need:
        return _cond("FAIL", f"缩容后集群空闲 {free:.2f} {unit} 小于回滚所需 {need:.2f} {unit}，回滚时没有容量", f, keys)
    return _cond("PASS", f"有变更前快照与回滚命令；缩容后空闲 {free:.2f} {unit} ≥ 回滚所需 {need:.2f} {unit}", f, keys)


# ---- verdict ----------------------------------------------------------------
def verdict_of(noop, conds, hpa_coupled):
    """The six-step order, first match wins. NOOP sits after B/D so risk guards fire on the current state too."""
    st = {k: v["status"] for k, v in conds.items()}
    if st["C"] == "FAIL":
        return "DENY"
    if st["A"] != "PASS":
        return "UNKNOWN"
    if st["B"] == "FAIL" or st["D"] == "FAIL":
        return "DENY"
    if noop:
        return "NOOP"
    if "UNKNOWN" in st.values() or hpa_coupled:
        return "REQUIRE_APPROVAL"
    return "ALLOW"


def _reasons(verdict, conds, rec, cur, hpa_coupled):
    if verdict == "NOOP":
        return [f"推荐值 {rec['request']:g} 不低于当前值 {cur['request']:g}，无需调整"]
    if verdict == "DENY":
        bad = ["C"] if conds["C"]["status"] == "FAIL" else [k for k in "BD" if conds[k]["status"] == "FAIL"]
        head = "服务正在退化，冻结优化" if bad == ["C"] else "降配后风险或回滚能力不满足"
        out = [head] + [f"条件 {k}：{conds[k]['why']}" for k in bad]
        if bad != ["C"] and is_noop(rec, cur.get("request")):
            out = ["当前配置偏低（under-provisioned），不应降配"] + out[1:] + [
                f"推荐值 {rec['request']:g} 高于当前值 {cur['request']:g}，仅供人工考虑上调，本 agent 不出降配计划"]
        return out
    if verdict == "UNKNOWN":
        return ["数据不足，不能判定"] + [f"条件 {k}：{conds[k]['why']}" for k in "A" if conds[k]["status"] == "UNKNOWN"]
    if verdict == "REQUIRE_APPROVAL":
        out = [f"条件 {k} 未知：{conds[k]['why']}" for k in "ABCD" if conds[k]["status"] == "UNKNOWN"]
        if hpa_coupled:
            out.append("HPA 以该资源为扩缩指标，改 request 会改变副本数，需要人工确认")
        return out
    return ["条件 A–D 全部通过，且没有 HPA 联动"]


def decide_one(f, cluster, policy, base_nodes, base_pods):
    cur, res = f["current"], f["resource"]
    rec = recommend(f, policy)
    noop = is_noop(rec, cur.get("request"))
    can_pack = rec is not None and not noop and cur.get("request") is not None and cur.get("replicas") is not None \
        and cluster.get("pods") and cluster.get("allocatable")
    if can_pack:
        sav = packing.savings(base_pods, cluster, f["namespace"], f["workload"], res, cur, rec["request"],
                              cluster["pricing"], policy["hours_per_month"])
    else:
        sav = {"request_value_usd_month": 0.0, "realizable_usd_month": 0.0, "nodes_before": base_nodes, "nodes_after": base_nodes}
    hpa = f.get("hpa")
    hpa_coupled = bool(hpa and hpa.get("metric") == res)
    conds = {"A": cond_a(f, rec, policy), "B": cond_b(f, rec, cluster, policy), "C": cond_c(f, policy),
             "D": cond_d(f, rec, cluster, base_pods, sav, policy) if can_pack or not noop else
                  _cond("PASS", "无需变更，没有回滚需求", f, [])}
    verdict = verdict_of(noop, conds, hpa_coupled)
    needs = []
    for k in "ABCD":
        for n in conds[k].pop("_needs"):
            if n not in needs:
                needs.append(n)
    h = policy["cpu_headroom"] if res == "cpu" else policy["mem_headroom"]
    return _r({
        "id": f.get("id") or f"K-{f['namespace']}-{f['workload']}-{res}",
        "workload": f["workload"], "namespace": f["namespace"], "team": f.get("team", ""), "resource": res,
        "current": dict(cur), "usage": dict(f["usage"]),
        "recommendation": rec or {"request": None, "method": None, "headroom": h},
        "savings": sav,
        "conditions": conds,
        "verdict": verdict, "reasons": _reasons(verdict, conds, rec, cur, hpa_coupled), "unknown_needs": needs,
        "hpa_coupled": hpa_coupled,
        "evidence": [dict(e) for e in f["evidence"]],
        "narrative": None,
        "container": f.get("container"),   # c0031 additions (spec k8s-pipeline), after the contract fields
        "slo": {**(f.get("slo") or {}), **(f.get("slo_def") or {})},
    })


def decide(facts_list: list[dict], cluster: dict, policy: dict) -> list[dict]:
    """facts_list + cluster + policy -> decision dicts in the contract shape, sorted by id."""
    pods = cluster.get("pods") or []
    base_nodes = packing.pack(pods, cluster["allocatable"]["cpu"], cluster["allocatable"]["memory_gib"],
                              cluster.get("min_nodes", 1)) if pods and cluster.get("allocatable") else 0
    out = [decide_one(f, cluster, policy, base_nodes, pods) for f in facts_list]
    return sorted(out, key=lambda d: d["id"])


def write_decisions(path, as_of, world, decisions):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"as_of": as_of, "world": world, "decisions": decisions}, indent=2, ensure_ascii=False) + "\n")
    return path
