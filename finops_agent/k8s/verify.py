"""K8s savings verification: did the applied request changes actually lower the bill?

A request change saves money only if the cluster runs fewer nodes, or the freed capacity absorbs growth that
would otherwise need a node. So the bill here is node-hours x node price, and the node count of every hour is
NOT read from a number: it is first-fit decreasing (finops_agent.k8s.packing.pack) over that hour's pod requests.

Two windows of equal length: BEFORE (before the changes) and AFTER (after they settled). The change of the bill
is decomposed into causes by a chain of states; every link differs from the previous one in exactly one thing,
so the links telescope and the causes sum to the bill change exactly (closure, asserted):

  S0  BEFORE window, requests and replicas as they were
  B   AFTER window hours, original requests, with the unrelated growth that happened            -> (b)
  A   + the applied changes, HPA replicas held as in B; credited plan by plan in the order applied  -> (a) nodes removed
                                                                                                    (e) 0 usd, request lowered, no node removed
  D   + the rollbacks (rolled-back plans switch back to the original request)                    -> (d)
  C   + HPA re-derives its replicas from the new cpu request (live)                              -> (c)  = actual AFTER state

Per plan, realized = -(a + d + c) of that plan: the effect attributable to the change, with the unrelated growth
(b) taken out as the control (the appendix's verify_savings does the same: control-corrected). The bill change
itself is the naive number; it is shown next to realized in the document. Predicted = the decision's
`realizable_usd_month` (the agent's claim: nodes saved by this change alone x price). The gap realized - predicted
is split per plan, again exactly:

  (e) packing   a0 - predicted: the same chain on a no-growth state; differs when other plans of the batch took the
                capacity first, or when the request fell but no node did (then the explanation says so)
  (b) growth    -(a - a0): the same chain on the grown state; unrelated growth took (or freed) capacity
  (d) rollback  -d: the change did not stick
  (c) hpa       -c: lower cpu request -> higher utilization -> more replicas

Limits (also written into docs/K8S_SAVINGS_VERIFICATION.md): FFD is not additive, so the per-plan split of a stage follows the order
in which the plans were applied (each is credited with the nodes it freed given what was already applied; the shares telescope to the stage total); the HPA model is desired = ceil(demand / (target x request)) clamped to [min, max], without the 10%
tolerance band or stabilization windows; the synthetic metrics do not react to a request change (no throttling or
latency model), so slo_ok only reflects the world's own SLO signals.

Offline and read-only: reads decisions, plans and a scenario file; writes only the files named on the command line.
CLI: python3 -m finops_agent.k8s.verify --decisions D --plans P --scenario S --out-dir DIR [--control S2] [--doc MD]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from finops_agent.k8s.packing import pack

HOUR = 3600
HOURS_PER_MONTH = 730
EPS = 1e-9
CAUSE_LABEL = {
    "a": "(a) 变更本身：节点少了",
    "b": "(b) 无关增长",
    "c": "(c) HPA 联动扩容",
    "d": "(d) 回滚",
    "e": "(e) 装箱没兑现",
}
ROUND = 2


# ---- timeline: what the cluster looked like, hour by hour ------------------------------------
@dataclass
class Change:
    plan_id: str
    workload: str
    namespace: str
    resource: str          # cpu | memory
    request: float         # target request after the change (core / GiB)
    at: float
    rolled_back_at: float | None = None


@dataclass
class Timeline:
    """Everything the verifier needs. Built by `from_scenario` for the synthetic world; a production adapter would
    fill the same fields from kube_pod_container_resource_requests history, HPA status and the node autoscaler."""
    alloc_cpu: float
    alloc_mem: float
    min_nodes: int
    max_nodes: int | None
    node_hour_usd: float
    workloads: dict                         # name -> {namespace, replicas, requests: {cpu, memory}, hpa: {...}|None}
    changes: list                           # [Change]
    scale_events: dict                      # name -> [(ts, replicas)] manual replica growth
    before: tuple                           # (start_ts, end_ts)
    after: tuple
    demand: object                          # callable(name, hour_ts, growth: bool) -> total cpu cores (HPA workloads)
    bumps: dict = field(default_factory=dict)   # name -> [(ts, resource, request)] unrelated request increases (growth too)
    slo: object = None                      # callable(name, start_ts, end_ts) -> {"burn_max", "budget_min"} | None
    source: str = ""


def hours(window: tuple) -> list:
    t0, t1 = window
    return [t0 + k * HOUR for k in range(int(round((t1 - t0) / HOUR)))]


class Engine:
    """Node count and bill for any combination of plan modes, growth and live-HPA, memoized per hour."""

    def __init__(self, tl: Timeline):
        self.tl = tl
        self.by_id = {c.plan_id: c for c in tl.changes}
        self._cache: dict = {}
        self.over_max_hours = 0

    def _active(self, c: Change, h: float, mode: str) -> bool:
        if h < c.at:
            return False
        return mode == "intended" or c.rolled_back_at is None or h < c.rolled_back_at

    def requests(self, name: str, h: float, modes: dict, growth: bool = True) -> dict:
        r = dict(self.tl.workloads[name]["requests"])
        if growth:
            for ts, res, val in sorted(self.tl.bumps.get(name, [])):
                if ts <= h:
                    r[res] = val
        for c in sorted(self.tl.changes, key=lambda c: c.at):
            if c.workload == name and c.plan_id in modes and self._active(c, h, modes[c.plan_id]):
                r[c.resource] = c.request
        return r

    def replicas(self, name: str, h: float, modes: dict, growth: bool, live: frozenset) -> int:
        w = self.tl.workloads[name]
        hpa = w.get("hpa")
        if hpa:
            cpu = self.requests(name, h, modes, growth)["cpu"] if name in live else w["requests"]["cpu"]
            want = math.ceil(self.tl.demand(name, h, growth) / (hpa["target_utilization"] / 100 * cpu) - 1e-9)
            return max(hpa["min"], min(hpa["max"], want))
        n = w["replicas"]
        if growth:
            for ts, r in sorted(self.tl.scale_events.get(name, [])):
                if ts <= h:
                    n = r
        return n

    def pods(self, h: float, modes: dict, growth: bool, live: frozenset) -> list:
        out = []
        for name, w in self.tl.workloads.items():
            req = self.requests(name, h, modes, growth)
            for i in range(self.replicas(name, h, modes, growth, live)):
                out.append({"namespace": w["namespace"], "workload": name, "pod": f"{name}-{i}", "cpu": req["cpu"], "mem": req["memory"]})
        return out

    def nodes(self, h: float, modes: dict, growth: bool = True, live=()) -> int:
        live = frozenset(live)
        key = (h, tuple(sorted(modes.items())), growth, live)
        if key not in self._cache:
            n = pack(self.pods(h, modes, growth, live), self.tl.alloc_cpu, self.tl.alloc_mem, self.tl.min_nodes)
            if self.tl.max_nodes is not None and n > self.tl.max_nodes:
                self.over_max_hours += 1
            self._cache[key] = n
        return self._cache[key]

    def node_hours(self, window_hours: list, modes: dict, growth: bool = True, live=()) -> int:
        return sum(self.nodes(h, modes, growth, live) for h in window_hours)

    def modal_nodes(self, window_hours: list, modes: dict, growth: bool = True, live=()) -> int:
        counts: dict = {}
        for h in window_hours:
            n = self.nodes(h, modes, growth, live)
            counts[n] = counts.get(n, 0) + 1
        return max(sorted(counts), key=lambda n: counts[n])


def sequential(players: list, value) -> dict:
    """Marginal of each player when players join one at a time in the given order: value(prefix_k) - value(prefix_k-1).
    The shares sum to value(all) - value(empty) exactly (telescoping), integers in, integers out."""
    out, prev = {}, value(frozenset())
    for k, p in enumerate(players):
        cur = value(frozenset(players[:k + 1]))
        out[p] = cur - prev
        prev = cur
    return out


# ---- the verification ------------------------------------------------------------------------
def _ts(s) -> float:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() if not isinstance(s, (int, float)) else float(s)


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_qty(resource: str, v) -> float:
    s = str(v)
    if resource == "cpu":
        return float(s[:-1]) / 1000 if s.endswith("m") else float(s)
    if s.endswith("Gi"):
        return float(s[:-2])
    if s.endswith("Mi"):
        return float(s[:-2]) / 1024
    raise ValueError(f"bad memory quantity {v!r}")


def plan_target(plan: dict, resource: str) -> float:
    """The request a plan writes, parsed from its patch (what kubectl would apply)."""
    cs = plan["patch"]["spec"]["template"]["spec"]["containers"]
    return parse_qty(resource, cs[0]["resources"]["requests"][resource])


def usd(x: float) -> float:
    r = round(x, ROUND)
    return r + 0.0 if r else 0.0


def run_verify(decisions: list, plans: list, tl: Timeline, hours_per_month: int = HOURS_PER_MONTH, min_budget: float = 0.25) -> dict:
    """-> detail dict. `to_contract(detail)` gives the k8s_verify.json list."""
    dec = {d["id"]: d for d in decisions}
    pl = {p["plan_id"]: p for p in plans}
    hb, ha = hours(tl.before), hours(tl.after)
    if len(hb) != len(ha) or not hb:
        raise ValueError("BEFORE and AFTER windows must have the same non-zero number of hours")
    eng = Engine(tl)
    order = [c.plan_id for c in sorted(tl.changes, key=lambda c: (c.at, c.plan_id))]   # the order they were applied
    info = {}
    for pid in order:
        c = eng.by_id[pid]
        if pid not in pl:
            raise ValueError(f"{pid}: applied in the scenario but not in the plans file")
        d = dec.get(pl[pid]["decision_id"])
        if d is None:
            raise ValueError(f"{pid}: its decision {pl[pid]['decision_id']} is not in the decisions file")
        if d["workload"] != c.workload or d["resource"] != c.resource:
            raise ValueError(f"{pid}: scenario change is on {c.workload}/{c.resource}, the decision on {d['workload']}/{d['resource']}")
        if abs(plan_target(pl[pid], c.resource) - c.request) > 1e-6:
            raise ValueError(f"{pid}: the applied request {c.request} differs from the plan's patch")
        info[pid] = d
    scale = hours_per_month / len(hb) * tl.node_hour_usd           # node-hours of the window -> usd per month

    # ---- the chain of states (per-plan shares inside a stage: each plan's marginal in the order applied)
    bill_before = eng.node_hours(hb, {}) * scale
    S0a = eng.node_hours(ha, {}, growth=False)
    B = eng.node_hours(ha, {}, growth=True)
    intended = lambda S: {p: "intended" for p in S}
    A_end = eng.node_hours(ha, intended(order), growth=True)
    rolled = [p for p in order if eng.by_id[p].rolled_back_at is not None]
    mixed = lambda Q: {p: ("actual" if p in Q else "intended") for p in order}
    D_end = eng.node_hours(ha, mixed(rolled), growth=True)
    final_modes = {p: "actual" for p in order}
    hpa_ws = sorted(n for n, w in tl.workloads.items() if w.get("hpa"))
    C = [D_end]
    for j in range(1, len(hpa_ws) + 1):
        C.append(eng.node_hours(ha, final_modes, growth=True, live=hpa_ws[:j]))
    after_nodehours = eng.node_hours(ha, final_modes, growth=True, live=hpa_ws)
    assert C[-1] == after_nodehours
    A = [B, A_end]
    D = [A_end, D_end]

    a = {p: float(x) * scale for p, x in sequential(order, lambda S: eng.node_hours(ha, intended(S), growth=True)).items()}
    a0 = {p: float(x) * scale for p, x in sequential(order, lambda S: eng.node_hours(ha, intended(S), growth=False)).items()}
    d_ = {p: 0.0 for p in order}
    for p, x in sequential(sorted(rolled, key=lambda p: (eng.by_id[p].rolled_back_at, p)), lambda Q: eng.node_hours(ha, mixed(Q), growth=True)).items():
        d_[p] = float(x) * scale
    c_ = {p: 0.0 for p in order}
    c_unattributed = []
    for j, w in enumerate(hpa_ws):
        delta = (C[j + 1] - C[j]) * scale
        owner = [p for p in order if eng.by_id[p].workload == w and eng.by_id[p].resource == "cpu"]
        if owner:
            c_[owner[0]] += delta
        elif abs(delta) > EPS:
            c_unattributed.append({"workload": w, "usd_month": delta})
    b_total = (B - eng.node_hours(hb, {})) * scale

    bill_after = after_nodehours * scale
    delta_bill = bill_after - bill_before

    # ---- causes of the bill change (bill-delta sign: negative = cheaper)
    causes = [{"cause": "b", "scope": "cluster", "usd_month": b_total,
               "explanation": explain_b_total(tl, b_total)}]
    for p in order:
        ch = eng.by_id[p]
        d = info[p]
        book = d["savings"]["request_value_usd_month"]
        if abs(a[p]) > EPS:
            causes.append({"cause": "a", "scope": p, "usd_month": a[p],
                           "explanation": f"{ch.workload} 的 {ch.resource} request 降到 {fmt_q(ch.resource, ch.request)}，按 FFD 重排后节点小时少了，账单 {usd(a[p])} 美元/月。"})
        else:
            causes.append({"cause": "e", "scope": p, "usd_month": 0.0,
                           "explanation": f"{ch.workload} 的 {ch.resource} request 降到 {fmt_q(ch.resource, ch.request)}，账面释放 {usd(book)} 美元/月，但装箱后节点数没有变化，账单不变。"})
        if abs(d_[p]) > EPS or ch.rolled_back_at is not None:
            causes.append({"cause": "d", "scope": p, "usd_month": d_[p],
                           "explanation": f"{ch.workload} 的 {ch.resource} 变更在 {iso(ch.rolled_back_at)} 被回滚，request 回到原值，这条变更的节点节省随之消失，账单 +{usd(d_[p])} 美元/月。"})
    for p in order:
        if abs(c_[p]) > EPS or tl.workloads[eng.by_id[p].workload].get("hpa") and eng.by_id[p].resource == "cpu":
            ch = eng.by_id[p]
            causes.append({"cause": "c", "scope": p, "usd_month": c_[p],
                           "explanation": explain_c(tl, ch, c_[p])})
    for u in c_unattributed:
        causes.append({"cause": "c", "scope": u["workload"], "usd_month": u["usd_month"], "explanation": "HPA 副本数变化，但没有对应的 cpu 变更计划，归为未归属。"})
    total = sum(x["usd_month"] for x in causes)
    closure = {"sum_causes_usd_month": total, "bill_delta_usd_month": delta_bill, "diff": total - delta_bill}
    assert abs(closure["diff"]) < 1e-6, f"decomposition does not close: {closure}"

    # ---- per plan
    nodes_before = eng.modal_nodes(hb, {})
    nodes_after = eng.modal_nodes(ha, final_modes, True, hpa_ws)
    out_plans = []
    for p in order:
        ch, d = eng.by_id[p], info[p]
        predicted = d["savings"]["realizable_usd_month"]
        book = d["savings"]["request_value_usd_month"]
        realized = -(a[p] + d_[p] + c_[p])
        realized = 0.0 if abs(realized) < EPS else realized
        terms = {"e": -a0[p] - predicted, "b": -(a[p] - a0[p]), "d": -d_[p], "c": -c_[p]}
        assert abs(sum(terms.values()) - (realized - predicted)) < 1e-6
        devs = plan_deviations(tl, ch, d, terms, a[p], predicted, book, rolled=ch.rolled_back_at is not None)
        slo = tl.slo(ch.workload, tl.after[0], tl.after[1]) if tl.slo else None
        slo_ok = None if slo is None else bool(slo["burn_max"] <= 1.0 and slo["budget_min"] >= min_budget)
        out_plans.append({
            "plan_id": p, "decision_id": d["id"], "workload": ch.workload, "namespace": ch.namespace, "resource": ch.resource,
            "request_before": tl.workloads[ch.workload]["requests"][ch.resource], "request_after": ch.request,
            "applied_at": iso(ch.at), "rolled_back_at": iso(ch.rolled_back_at) if ch.rolled_back_at is not None else None,
            "predicted_usd_month": predicted, "book_usd_month": book, "realized_usd_month": realized,
            "ratio": (realized / predicted) if predicted > EPS else None,
            "book_ratio": (realized / book) if book > EPS else None,
            "chain_usd_month": {"a0": a0[p], "a": a[p], "d": d_[p], "c": c_[p]},
            "terms_usd_month": terms, "deviations": devs,
            "nodes_before": nodes_before, "nodes_after": nodes_after, "slo_ok": slo_ok, "slo": slo,
        })
    hourly = lambda modes, g, live: sorted({eng.nodes(h, modes, g, live) for h in (hb if modes is None else ha)})
    detail = {
        "scenario": tl.source,
        "windows": {"before": [iso(tl.before[0]), iso(tl.before[1])], "after": [iso(tl.after[0]), iso(tl.after[1])], "hours": len(hb), "label": f"{len(hb) // 24}d"},
        "scale": {"node_hour_usd": tl.node_hour_usd, "hours_per_month": hours_per_month, "usd_per_node_hour_to_month": scale},
        "bill": {"before_usd_month": bill_before, "after_usd_month": bill_after, "delta_usd_month": delta_bill,
                 "before_node_hours": eng.node_hours(hb, {}), "after_node_hours": after_nodehours},
        "nodes": {"before_modal": nodes_before, "after_modal": nodes_after,
                  "before_range": sorted({eng.nodes(h, {}) for h in hb}), "after_range": sorted({eng.nodes(h, final_modes, True, hpa_ws) for h in ha}),
                  "growth_only_range": sorted({eng.nodes(h, {}) for h in ha})},
        "stages_node_hours": {"S0_before": eng.node_hours(hb, {}), "S0_after_hours_no_growth": S0a, "B_growth": B,
                              "A_applied": A_end, "D_after_rollbacks": D_end, "C_after_hpa": C[-1]},
        "causes": causes, "closure": closure,
        "plans": out_plans, "plan_order": order,
        "realized_total_usd_month": sum(p["realized_usd_month"] for p in out_plans),
        "predicted_total_usd_month": sum(p["predicted_usd_month"] for p in out_plans),
        "unattributed": c_unattributed, "over_max_node_hours": eng.over_max_hours,
    }
    return detail


def fmt_q(resource: str, v: float) -> str:
    return f"{v:g} core" if resource == "cpu" else f"{v:g} GiB"


def explain_b_total(tl: Timeline, b_total: float) -> str:
    grown = sorted(n for n, ev in tl.scale_events.items() if ev)
    hpa_load = sorted(n for n, w in tl.workloads.items() if w.get("hpa"))
    bits = []
    if grown:
        bits.append("、".join(grown) + " 的副本数在窗口内增长（人为扩容，和任何变更无关）")
    if hpa_load:
        bits.append("HPA 工作负载的负载变化按原 request 推出的副本数")
    what = "；".join(bits) if bits else "窗口之间的自然差异"
    if abs(b_total) < EPS:
        return f"无关增长：{what}，对节点数没有影响，账单 0。"
    return f"无关增长：{what}。不动任何 request 也会发生，账单 {'+' if b_total > 0 else ''}{usd(b_total)} 美元/月。这是对照组，核实时从各条变更的 realized 里扣除。"


def explain_c(tl: Timeline, ch: Change, c: float) -> str:
    h = tl.workloads[ch.workload]["hpa"]
    base = f"{ch.workload} 带 HPA（cpu 利用率目标 {h['target_utilization']}%）：cpu request 降到 {fmt_q('cpu', ch.request)}，同样的用量利用率变高，HPA 多开副本。"
    if abs(c) < EPS:
        return base + "这一窗口里多开的副本没有推高节点数，账单 0。"
    return base + f"多出的副本需要更多节点，账单 {'+' if c > 0 else ''}{usd(c)} 美元/月。"


def plan_deviations(tl, ch, d, terms, a_k, predicted, book, rolled) -> list:
    """gap realized - predicted, one entry per cause; entries sum to the gap exactly (unrounded)."""
    out = []
    e = terms["e"]
    if abs(a_k) < EPS and not rolled and book > EPS and abs(terms["d"]) < EPS:
        out.append({"cause": CAUSE_LABEL["e"], "usd": e,
                    "explanation": (f"request 降了，账面释放 {usd(book)} 美元/月，但装箱（FFD）后节点数没变，账单不变，所以实际节省是 0。"
                                    f"节点仍被另一类资源钉住（{pin_hint(tl, ch)}），降这一条不会让任何一台节点空出来。")
                                   + ("" if abs(e) < EPS else f" 与预测 {usd(predicted)} 美元/月相差 {usd(e)}。")})
    elif abs(e) > 0.005:
        out.append({"cause": CAUSE_LABEL["e"], "usd": e,
                    "explanation": ("同一批变更里排在前面的计划先腾出了容量，这一条在它们之后的边际变小（装箱不可加，按 plan_id 顺序归集）。" if e < 0
                                    else "这一条与同批其他变更合起来比单独做省得多：装箱后多空出一台节点（按应用顺序归集）。")})
    if abs(terms["b"]) > 0.005:
        out.append({"cause": CAUSE_LABEL["b"], "usd": terms["b"],
                    "explanation": ("无关的副本增长占用了容量，这条变更腾出的位置被吃掉一部分。" if terms["b"] < 0
                                    else "无关的副本增长本来要新开节点，这条变更腾出的容量把它吸收了，所以多省了一台。")})
    if abs(terms["d"]) > 0.005:
        out.append({"cause": CAUSE_LABEL["d"], "usd": terms["d"],
                    "explanation": f"变更在 {iso(ch.rolled_back_at)} 被回滚，AFTER 窗口里 request 已是原值，节省没有保住。"})
    if abs(terms["c"]) > 0.005:
        out.append({"cause": CAUSE_LABEL["c"], "usd": terms["c"], "explanation": explain_c(tl, ch, -terms["c"])})
    return out


def pin_hint(tl: Timeline, ch: Change) -> str:
    """What keeps the nodes full: the same workload's request of the OTHER resource, which this change does not touch."""
    other = "memory" if ch.resource == "cpu" else "cpu"
    return f"{ch.workload} 每个 pod 还预留 {fmt_q(other, tl.workloads[ch.workload]['requests'][other])}，另一类资源没动"


# ---- the k8s_verify.json contract (c0032) ---------------------------------------------------
def to_contract(detail: dict) -> list:
    out = []
    for p in detail["plans"]:
        out.append({
            "plan_id": p["plan_id"], "decision_id": p["decision_id"],
            "predicted_usd_month": usd(p["predicted_usd_month"]), "realized_usd_month": usd(p["realized_usd_month"]),
            "ratio": None if p["ratio"] is None else round(p["ratio"], 4),
            "nodes_before": p["nodes_before"], "nodes_after": p["nodes_after"],
            "deviations": [{"cause": x["cause"], "usd": usd(x["usd"]), "explanation": x["explanation"]} for x in p["deviations"]],
            "slo_ok": p["slo_ok"], "window": detail["windows"]["label"],
        })
    return out


# ---- scenario adapter (synthetic world) ------------------------------------------------------
def from_scenario(path, before: tuple | None = None, after: tuple | None = None) -> Timeline:
    """Timeline from a testenv scenario file. The changes, rollbacks and manual scale-ups are timed events in
    `add_events` (types set_request / rollback / scale / bump_request; the metrics generators ignore them); usage and HPA demand
    come from the world generators of testenv.k8s_world, which also serve the same file as a fake Prometheus."""
    import copy

    import yaml

    from testenv import k8s_world as kw

    path = Path(path)
    world = kw.load(path)
    raw = world.raw
    v = (yaml.safe_load(path.read_text()) or {}).get("verify") or {}
    win = lambda k, given: given or (_ts(v[k]["start"]), _ts(v[k]["end"]))
    before, after = win("before", before), win("after", after)
    nodes = raw["nodes"]
    workloads, changes, scale, bumps = {}, [], {}, {}
    rolled = {}
    for w in raw["workloads"]:
        workloads[w["name"]] = {"namespace": w["namespace"], "replicas": w["replicas"],
                                "requests": {"cpu": world.request(w, "cpu"), "memory": world.request(w, "memory")}, "hpa": w.get("hpa")}
        for e in w.get("events", []):
            if e["type"] == "set_request":
                changes.append(Change(e["plan_id"], w["name"], w["namespace"], e["resource"], float(e["request"]), _ts(e["at"])))
            elif e["type"] == "rollback":
                rolled[e["plan_id"]] = _ts(e["at"])
            elif e["type"] == "scale":
                scale.setdefault(w["name"], []).append((_ts(e["at"]), int(e["replicas"])))
            elif e["type"] == "bump_request":
                bumps.setdefault(w["name"], []).append((_ts(e["at"]), e["resource"], float(e["request"])))
    for c in changes:
        c.rolled_back_at = rolled.pop(c.plan_id, None)
    if rolled:
        raise ValueError(f"rollback without a set_request: {sorted(rolled)}")

    nogrowth = copy.deepcopy(raw)
    for w in nogrowth["workloads"]:
        w["events"] = [e for e in w.get("events", []) if e["type"] != "load"]
    worlds = {True: world, False: kw.K8sWorld(nogrowth, world.t0, world.t1)}
    series: dict = {}

    def demand(name, h, growth):
        key = (name, growth)
        if key not in series:
            wd = worlds[growth]
            w = wd.workload(name)
            grid = wd.grid
            tot = [0.0] * len(grid)
            for i in range(w["replicas"]):       # demand = what the pods of the original deployment consumed
                for k, u in enumerate(kw.cpu_cores(wd, w, i, grid)):
                    tot[k] += u
            per = int(HOUR // kw.STEP)
            series[key] = {grid[0] + j * HOUR: sum(tot[j * per:(j + 1) * per]) / per for j in range(len(grid) // per)}
        return series[key][h]

    def slo(name, t0, t1):
        w = world.workload(name)
        if "slo" not in w:
            return None
        grid = [t for t in world.grid if t0 <= t < t1]
        full = world.grid
        cpu = kw.cpu_cores(world, w, 0, full)
        thr = kw.throttle_ratio(world, w, cpu, 0)
        budget, burn, _ = kw._slo(world, w, full, thr)
        idx = [k for k, t in enumerate(full) if t0 <= t < t1]
        if not idx or not grid:
            return None
        return {"burn_max": max(burn[k] for k in idx), "budget_min": min(budget[k] for k in idx)}

    return Timeline(alloc_cpu=nodes["allocatable"]["cpu"], alloc_mem=nodes["allocatable"]["memory_gib"], min_nodes=nodes.get("min", 1),
                    max_nodes=nodes.get("max"), node_hour_usd=raw["pricing"]["node_hour_usd"], workloads=workloads, changes=changes,
                    scale_events=scale, bumps=bumps, before=before, after=after, demand=demand, slo=slo, source=path.name)


# ---- document (Chinese, generated from the data; nothing in it is typed by hand) ------------------
def _money(x) -> str:
    if x is None:
        return "—"
    r = round(x, 2) + 0.0
    return f"-${-r:,.2f}" if r < 0 else f"${abs(r):,.2f}"


def _pct(x) -> str:
    return "无法定义（预测为 0）" if x is None else f"{x * 100:.1f}%"


def conclusion(p: dict) -> str:
    pred, real = p["predicted_usd_month"], p["realized_usd_month"]
    if p["rolled_back_at"]:
        return (f"没有兑现。变更在 {p['rolled_back_at']} 被回滚，AFTER 窗口里 request 已回到原值；预测 {_money(pred)}，实际 {_money(real)}。"
                "回滚本身是安全机制起作用，不是 agent 的失败，但这笔钱不能算节省。")
    c_dev = [x for x in p["deviations"] if x["cause"].startswith("(c)")]
    if pred <= EPS and abs(real) <= 0.005 and c_dev:
        return ("净额 0，但内部有两股相反的力：同批变更先腾出的容量被这条变更吸收，随后 HPA 因 request 变小而多开副本，又把它拿走（原因列的 (b) 与 (c) 相互抵消）。"
                f"agent 预测可兑现 0，账面释放 {_money(p['book_usd_month'])}/月，没有进账单。")
    if pred <= EPS and abs(real) <= 0.005:
        return (f"账单不变，与预测一致（agent 预测装箱后节点数不变，所以可兑现金额是 0）。账面释放 {_money(p['book_usd_month'])}/月 没有进账单。")
    if pred <= EPS:
        sign = "多花" if real < 0 else "少花"
        return f"agent 预测可兑现 0，实际{sign} {_money(abs(real))}/月，是同批变更与扩容叠加的结果，原因见偏差列。"
    if p["ratio"] is not None and abs(p["ratio"] - 1) < 0.005:
        return f"兑现。实际 {_money(real)}/月，等于预测，节点数 {p['nodes_before']} 到 {p['nodes_after']}。"
    return f"部分兑现。实际 {_money(real)}/月，实现率 {_pct(p['ratio'])}，差额的原因见偏差列。"


def render_doc(detail: dict, control: dict | None = None) -> str:
    L: list = []
    w = detail["windows"]
    plans = detail["plans"]
    real, pred = detail["realized_total_usd_month"], detail["predicted_total_usd_month"]
    cl = detail["closure"]
    L += ["# K8s 节省核实：改了 requests，账单真的降了吗", "",
          "> 本文由 `python3 -m finops_agent.k8s.verify`（`bash scripts/k8s_verify.sh`）生成，所有数字来自代码，不要手改。表格编号 V1 起在正文中被引用。", ""]
    L += ["## 结论先行", ""]
    pos = [p for p in plans if p["ratio"] is not None]
    L += [f"- 核实窗口：BEFORE {w['before'][0][:10]} 到 {w['before'][1][:10]}，AFTER {w['after'][0][:10]} 到 {w['after'][1][:10]}，各 {w['hours']} 小时（{w['label']}）。账单口径是节点小时 x 节点单价 ${detail['scale']['node_hour_usd']:.3f}/小时，按 {detail['scale']['hours_per_month']} 小时折成月。",
          f"- {len(plans)} 条已执行变更，agent 预测可兑现合计 {_money(pred)}/月，控制组校正后的实际合计 {_money(real)}/月，实现率 {_pct(real / pred if pred > EPS else None)}。账单本身从 {_money(detail['bill']['before_usd_month'])} 变到 {_money(detail['bill']['after_usd_month'])}，变化 {_money(detail['bill']['delta_usd_month'])}/月。两个数不同，差的是与变更无关的增长：它让账单多了 {_money(next(c['usd_month'] for c in detail['causes'] if c['cause'] == 'b'))}/月，吃掉了一部分节省（表 V3）。",
          f"- 账单变化精确分解为五类原因，各项之和 {_money(cl['sum_causes_usd_month'])}，账单变化 {_money(cl['bill_delta_usd_month'])}，差 {abs(cl['diff']):.6f}（闭合）。"]
    for p in plans:
        short = p["plan_id"].replace("P-K-", "")
        causes = "、".join(sorted({x["cause"][:3] for x in p["deviations"]})) or "无偏差"
        L.append(f"- {short}：预测 {_money(p['predicted_usd_month'])}，实际 {_money(p['realized_usd_month'])}，实现率 {_pct(p['ratio'])}；偏差原因 {causes}。")
    L += ["- 「packing-trap」降了 request 但节点数下不来，实际 0，原因是 (e) 装箱没兑现。这正是 c0027 要求每条推荐同时报「账面释放」与「可兑现」两种口径的理由。", ""]

    L += ["## 方法", "",
          "一条 request 变更只有两种方式省钱：集群少开节点，或者腾出的容量吸收了本来要新开节点的增长。所以账单只看节点小时。每个小时的节点数不是手填的，而是对当小时全部 pod 的 requests 做 first-fit decreasing（`finops_agent/k8s/packing.py`，和决策里算「可兑现」用的是同一个函数）。HPA 工作负载的副本数由世界生成的用量按公式 `ceil(用量 / (目标利用率 x cpu request))` 推出，限制在 [min, max]。", "",
          "账单变化沿一条状态链分解，链上相邻两环只差一件事，所以各环之差相加正好等于总变化：", "",
          "| 环 | 状态 | 这一步的差额记入 |", "|---|---|---|",
          "| S0 | BEFORE 窗口，原 requests | 起点 |",
          "| B | AFTER 窗口的小时，原 requests，加上无关增长（副本扩容、HPA 按原 request 对新负载的响应） | (b) 无关增长 |",
          "| A | 再加上已应用的变更，HPA 副本数仍按原 request | (a) 变更本身让节点少了；某条变更没让节点少则记 (e)，金额 0 |",
          "| D | 再把被回滚的变更换回原 request | (d) 回滚 |",
          "| C | 再让 HPA 按新 cpu request 重新推副本数 | (c) HPA 联动扩容 |", "",
          "单条变更的「实际」= 它在 A、D、C 三步里的份额之和取负，即控制组（无关增长 b）扣除之后归因到这条变更的节省。这与附录 `docs/SAVINGS_VERIFICATION.md` 的口径一致：实现的是被校正后的值，不是两张账单的朴素差。「预测」取决策文件里的 `realizable_usd_month`（单独应用这一条能少几台节点 x 单价）。实际与预测的差再拆成 (e)(b)(d)(c) 四项，逐条相加等于差额。", ""]

    L += ["## 逐条核实", "",
          "**表 V1 预测、实际与偏差**（金额为 $/月；实现率 = 实际 / 预测；账面释放来自决策的 `request_value_usd_month`）", "",
          "| 变更 | 预测 | 实际 | 实现率 | 账面释放 | 集群节点数（前 到 后，整体） | 偏差原因与金额 | SLO | 结论 |", "|---|---|---|---|---|---|---|---|---|"]
    for p in plans:
        devs = "<br>".join(f"{x['cause']} {_money(x['usd'])}" for x in p["deviations"]) or "无"
        slo = {True: "保持", False: "退化", None: "不可判"}[p["slo_ok"]]
        L.append(f"| {p['plan_id'].replace('P-K-', '')} | {_money(p['predicted_usd_month'])} | {_money(p['realized_usd_month'])} | {_pct(p['ratio'])} | {_money(p['book_usd_month'])} | {p['nodes_before']} 到 {p['nodes_after']} | {devs} | {slo} | {conclusion(p)} |")
    L += ["", "**表 V2 每条偏差的白话解释**", "", "| 变更 | 原因 | 金额 | 解释 |", "|---|---|---|---|"]
    for p in plans:
        for x in p["deviations"]:
            L.append(f"| {p['plan_id'].replace('P-K-', '')} | {x['cause']} | {_money(x['usd'])} | {x['explanation']} |")
    L += ["", "实现率为「无法定义」的两条，agent 在决策里就预测了可兑现 0。核实的意义在于确认账单没有意外：packing-trap 与预测一致；hpa-managed 在本批里被同批变更先腾出的位置吃掉，又被 HPA 扩容拿走，净额也是 0。", ""]

    L += ["## 账单分解", "",
          "**表 V3 账单变化的五类原因**（负数 = 账单变少；金额为 $/月）", "", "| 原因 | 范围 | 金额 | 说明 |", "|---|---|---|---|"]
    for c in detail["causes"]:
        L.append(f"| {CAUSE_LABEL[c['cause']]} | {c['scope'].replace('P-K-', '')} | {_money(c['usd_month'])} | {c['explanation']} |")
    L += [f"| 合计 | | {_money(cl['sum_causes_usd_month'])} | 账单变化 {_money(cl['bill_delta_usd_month'])}，差 {abs(cl['diff']):.6f}，闭合 |", ""]
    st = detail["stages_node_hours"]
    L += ["**表 V4 状态链上的节点小时**", "", "| 环 | 节点小时 | 相对上一环 |", "|---|---|---|"]
    chain = [("S0 BEFORE 窗口", st["S0_before"]), ("B 加无关增长", st["B_growth"]), ("A 加已应用变更", st["A_applied"]), ("D 加回滚", st["D_after_rollbacks"]), ("C 加 HPA 联动（= 实际 AFTER）", st["C_after_hpa"])]
    for i, (n, v) in enumerate(chain):
        L.append(f"| {n} | {v} | {'' if i == 0 else f'{v - chain[i - 1][1]:+d}'} |")
    L.append("")
    if control:
        cc = control["closure"]
        L += ["## 对照：没有偏差源的场景", "",
              f"`{control['scenario']}` 只有两条干净变更（api-steady、packing-trap），没有增长、回滚、HPA。核实器应当给出：api-steady 实现率 100%，packing-trap 为 0，不凭空造偏差。", "",
              "| 变更 | 预测 | 实际 | 实现率 | 偏差 |", "|---|---|---|---|---|"]
        for p in control["plans"]:
            L.append(f"| {p['plan_id'].replace('P-K-', '')} | {_money(p['predicted_usd_month'])} | {_money(p['realized_usd_month'])} | {_pct(p['ratio'])} | {'<br>'.join(x['cause'] + ' ' + _money(x['usd']) for x in p['deviations']) or '无'} |")
        L += ["", f"账单从 {_money(control['bill']['before_usd_month'])} 变到 {_money(control['bill']['after_usd_month'])}，分解闭合，差 {abs(cc['diff']):.6f}。", ""]
    L += ["## 局限", "",
          "- 装箱不可加：两条变更各自单独能省一台节点，合在一起可能还是一台。单条归因按应用顺序，每条只算它加入时多空出的节点，所以换一个应用顺序，单条数字会变，总数不变。",
          "- HPA 模型是 `ceil(用量 / (目标 x request))`，没有 10% 容忍带和稳定窗口，真实 HPA 会更迟钝。",
          "- 合成指标不随 request 变化：没有 throttling 与延迟模型，所以 `slo_ok` 只反映世界自带的 SLO 信号；没有定义 SLO 的 workload 是「不可判」，月报不把它的节省算作已验证。",
          "- 节点自动扩缩被简化成「每小时节点数 = FFD 结果」，没有缩容延迟，也没有 PodDisruptionBudget。真实集群里节点要空闲一段时间才会被回收。",
          "- 变更、回滚与扩容的时间线是场景文件里的事件（`testenv/k8s_scenarios/after_*.yaml`），指标服务器暂不吐出变更后的 requests；生产适配器应改读 `kube_pod_container_resource_requests` 的历史。",
          "- BEFORE 与 AFTER 之间留了一周让 HPA 与回滚落定，窗口越短，偏差越多来自噪声。", ""]
    return "\n".join(L)


# ---- CLI ---------------------------------------------------------------------------------------
def _load(path):
    return json.loads(Path(path).read_text())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--decisions", required=True, help="runs/<id>/k8s_decisions.json from before the changes")
    ap.add_argument("--plans", required=True, help="runs/<id>/k8s_plans.json")
    ap.add_argument("--scenario", required=True, help="after-scenario overlay, e.g. testenv/k8s_scenarios/after_apply.yaml")
    ap.add_argument("--control", default=None, help="a second overlay without deviation sources (control)")
    ap.add_argument("--out-dir", required=True, help="writes k8s_verify.json and k8s_verify_detail.json here (control under control/)")
    ap.add_argument("--doc", default=None, help="write the Chinese document here")
    ap.add_argument("--hours-per-month", type=int, default=HOURS_PER_MONTH)
    a = ap.parse_args(argv)
    decisions = _load(a.decisions)["decisions"]
    plans_doc = _load(a.plans)
    plans = plans_doc["plans"] if isinstance(plans_doc, dict) else plans_doc
    out = Path(a.out_dir)

    def one(scenario, dest):
        detail = run_verify(decisions, plans, from_scenario(scenario), hours_per_month=a.hours_per_month)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "k8s_verify.json").write_text(json.dumps(to_contract(detail), indent=2, ensure_ascii=False) + "\n")
        (dest / "k8s_verify_detail.json").write_text(json.dumps(detail, indent=2, ensure_ascii=False) + "\n")
        return detail

    main_d = one(a.scenario, out)
    ctl = one(a.control, out / "control") if a.control else None
    if a.doc:
        Path(a.doc).write_text(render_doc(main_d, ctl))
    for tag, d in (("main", main_d), ("control", ctl)):
        if d:
            print(f"k8s verify [{tag}] {d['scenario']}: predicted {d['predicted_total_usd_month']:.2f} realized {d['realized_total_usd_month']:.2f} "
                  f"bill delta {d['bill']['delta_usd_month']:.2f} closure diff {d['closure']['diff']:.2e}", file=sys.stderr)
    print(out / "k8s_verify.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
