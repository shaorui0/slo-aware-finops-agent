"""Collect: PromQL pulls -> per-(workload, resource) facts, each number backed by an evidence entry.

Read-only. Metric names are the contract's (k8s-mainline spec); the two that are not plain contract names
are single constants below. Every call is appended to the shared Trace (finops_agent.collect.Trace).
The PromQL stays inside the test-metrics-server subset: no subqueries, no binary operators.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

from finops_agent.collect import Trace

# ---- metric names (contract) --------------------------------------------------
CPU_USAGE = "container_cpu_usage_cores"        # NOT in the contract table: per-pod cores gauge, because subqueries are unsupported
MEM_WS = "container_memory_working_set_bytes"
CFS_PERIODS = "container_cpu_cfs_periods_total"
CFS_THROTTLED = "container_cpu_cfs_throttled_periods_total"
REQUESTS = "kube_pod_container_resource_requests"
LIMITS = "kube_pod_container_resource_limits"
LAST_TERMINATED = "kube_pod_container_status_last_terminated_reason"
DEPLOY_REPLICAS = "kube_deployment_spec_replicas"
HPA_TARGET = "kube_horizontalpodautoscaler_spec_target_metric"
HPA_CURRENT = "kube_horizontalpodautoscaler_status_current_replicas"
HPA_MAX = "kube_horizontalpodautoscaler_spec_max_replicas"
NODE_ALLOC = "kube_node_status_allocatable"
POD_PHASE = "kube_pod_status_phase"
SLO_BUDGET = "slo_error_budget_remaining_ratio"
SLO_BURN = "slo_burn_rate_1h"
JOB_DURATION = "job_last_duration_seconds"
# labels that are not in the contract table
HPA_METRIC_LABEL = "metric_name"       # kube-state-metrics standard label on the HPA target metric
DEADLINE_LABEL = "deadline_seconds"    # on job_last_duration_seconds
TEAM_LABEL = "team"                    # on kube_deployment_spec_replicas

GIB = 1024 ** 3
HOUR = 3600


class Prom:
    """Minimal read-only Prometheus HTTP client. `fetch(url) -> dict` is injectable for tests."""

    def __init__(self, base_url: str, trace: Trace | None = None, fetch=None):
        self.base = base_url.rstrip("/")
        self.trace = trace if trace is not None else Trace()
        self._fetch = fetch or self._urlopen

    @staticmethod
    def _urlopen(url):
        with urlopen(url, timeout=60) as r:
            return json.load(r)

    def _get(self, path, **params):
        started = time.time()
        url = f"{self.base}{path}?{urlencode(params)}"
        try:
            out = self._fetch(url)
            if out.get("status") != "success":
                raise RuntimeError(out.get("error", "prometheus error"))
            self.trace.record("prometheus", path, params, started)
            return out["data"]["result"]
        except Exception as e:
            self.trace.record("prometheus", path, params, started, ok=False, error=str(e))
            raise

    def query(self, q, at=None):
        p = {"query": q}
        if at is not None:
            p["time"] = repr(float(at))
        return self._get("/api/v1/query", **p)

    def query_range(self, q, start, end, step=HOUR):
        return self._get("/api/v1/query_range", query=q, start=repr(float(start)), end=repr(float(end)), step=str(step))


def _num(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Evidence:
    """Per-facts evidence book: add() returns the id, ids are E1.. in insertion order."""

    def __init__(self):
        self.items, self.ev = [], {}

    def add(self, key, query, window, value):
        eid = f"E{len(self.items) + 1}"
        self.items.append({"id": eid, "query": query, "window": window, "value": value, "source": "prometheus"})
        self.ev[key] = eid


class Collector:
    def __init__(self, prom: Prom, policy: dict, as_of: float | None = None):
        self.prom, self.policy = prom, policy
        self.days = policy["window_days"]
        self.win = f"{self.days}d"
        self.t = as_of if as_of is not None else self._probe()

    def _probe(self):
        res = self.prom.query(f"max({DEPLOY_REPLICAS})")
        if not res:
            raise RuntimeError(f"no {DEPLOY_REPLICAS} series: cannot determine as_of")
        return float(res[0]["value"][0])

    # ---- primitives -----------------------------------------------------------
    def inst(self, q):
        """Instant value (first series) at as_of; (value, labels) or (None, {})."""
        res = self.prom.query(q, self.t)
        if not res:
            return None, {}
        return _num(res[0]["value"][1]), res[0]["metric"]

    def hourly(self, q):
        """{ts: value} at 1h step over the window; series are merged by max."""
        out = {}
        for s in self.prom.query_range(q, self.t - self.days * 86400, self.t, HOUR):
            for ts, v in s["values"]:
                x = _num(v)
                if x is not None:
                    out[float(ts)] = max(out.get(float(ts), x), x)
        return out

    # ---- discovery ------------------------------------------------------------
    def workloads(self):
        rows = self.prom.query(f"max by (namespace, workload, {TEAM_LABEL})({DEPLOY_REPLICAS})", self.t)
        found = {(m["metric"].get("namespace", ""), m["metric"]["workload"]): m["metric"].get(TEAM_LABEL, "") for m in rows}
        return [(ns, wl, found[(ns, wl)]) for ns, wl in sorted(found)]

    # ---- cluster --------------------------------------------------------------
    def cluster(self, pricing, min_nodes, max_nodes):
        ev = {}

        def inst(key, q, window="instant", scale=1.0):
            v, labels = self.inst(q)
            v = None if v is None else v * scale
            ev[key] = (q, window, v)
            return v, labels

        q = f'sum({POD_PHASE}{{phase="Pending"}})'
        pending, _ = inst("pending", q)
        if pending is None:   # kube-state-metrics emits all phases; a missing Pending series with other phases present means 0
            q2 = f"count({POD_PHASE})"
            any_phase, _ = self.inst(q2)
            if any_phase:
                pending = 0.0
                ev["pending"] = (q2, "instant", 0.0)
        n, _ = inst("node_count", f'count({NODE_ALLOC}{{resource="cpu"}})')
        a_cpu, _ = inst("alloc_cpu", f'min({NODE_ALLOC}{{resource="cpu"}})')
        a_mem, _ = inst("alloc_mem", f'min({NODE_ALLOC}{{resource="memory"}})', scale=1 / GIB)
        pods = {}
        for res, key, scale in (("cpu", "cpu", 1.0), ("memory", "mem", 1 / GIB)):
            q = f'max by (namespace, workload, pod)({REQUESTS}{{resource="{res}"}})'
            rows = self.prom.query(q, self.t)
            ev["pods" if res == "cpu" else "pods_mem"] = (q, "instant", float(len(rows)))
            for r in rows:
                m, v = r["metric"], _num(r["value"][1])
                p = pods.setdefault((m.get("namespace", ""), m.get("pod", "")),
                                    {"namespace": m.get("namespace", ""), "workload": m.get("workload", ""), "pod": m.get("pod", ""),
                                     "cpu": 0.0, "mem": 0.0})
                p[key] = (v or 0.0) * scale
        return {
            "allocatable": {"cpu": a_cpu, "memory_gib": a_mem} if a_cpu and a_mem else None,
            "node_count": None if n is None else int(n),
            "min_nodes": min_nodes, "max_nodes": max_nodes,
            "pending": pending, "pricing": pricing,
            "pods": [pods[k] for k in sorted(pods)],
            "evidence": ev,
        }

    # ---- per workload ---------------------------------------------------------
    def shared(self, ns, wl):
        """Measurements that are the same for the workload's cpu and memory facts."""
        sel = f'namespace="{ns}",workload="{wl}"'
        m = {}

        def inst(key, q, window="instant"):
            v, labels = self.inst(q)
            m[key] = (q, window, v)
            return v, labels

        inst("replicas", f"max by (workload)({DEPLOY_REPLICAS}{{{sel}}})")
        inst("oom", f'sum by (workload)(max_over_time({LAST_TERMINATED}{{{sel},reason="OOMKilled"}}[{self.win}]))', self.win)
        if m["oom"][2] is None:
            m["oom"] = (m["oom"][0], self.win, 0.0)   # no OOMKilled series = no OOM kill in the window
        v, labels = inst("hpa", f"max by (workload, {HPA_METRIC_LABEL})({HPA_TARGET}{{{sel}}})")
        hpa = None
        if v is not None:
            inst("hpa_current", f"max by (workload)({HPA_CURRENT}{{{sel}}})")
            inst("hpa_max", f"max by (workload)({HPA_MAX}{{{sel}}})")
            hpa = {"metric": labels.get(HPA_METRIC_LABEL, ""), "target": v,
                   "current_replicas": m["hpa_current"][2], "max_replicas": m["hpa_max"][2]}
        inst("burn", f"max by (workload)({SLO_BURN}{{{sel}}})")
        inst("budget", f"min by (workload)({SLO_BUDGET}{{{sel}}})")
        job, jl = inst("job", f"max by (workload, {DEADLINE_LABEL})(max_over_time({JOB_DURATION}{{{sel}}}[{self.win}]))", self.win)
        deadline = _num(jl.get(DEADLINE_LABEL)) if job is not None else None
        m["deadline"] = (m["job"][0], self.win, deadline)
        return m, hpa

    def resource_measures(self, ns, wl, resource):
        sel = f'namespace="{ns}",workload="{wl}"'
        cpu = resource == "cpu"
        metric, scale = (CPU_USAGE, 1.0) if cpu else (MEM_WS, 1 / GIB)
        m = {}

        def inst(key, q, window="instant", sc=1.0):
            v, _ = self.inst(q)
            m[key] = (q, window, None if v is None else v * sc)
            return v

        rscale = 1.0 if cpu else 1 / GIB
        inst("request", f'max by (workload)({REQUESTS}{{{sel},resource="{resource}"}})', sc=rscale)
        inst("limit", f'max by (workload)({LIMITS}{{{sel},resource="{resource}"}})', sc=rscale)
        for key, qt in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
            inst(key, f"max by (workload)(quantile_over_time({qt}, {metric}{{{sel}}}[{self.win}]))", self.win, scale)
        inst("max", f"max by (workload)(max_over_time({metric}{{{sel}}}[{self.win}]))", self.win, scale)

        hq = f"max by (workload)(max_over_time({metric}{{{sel}}}[1h]))"
        series = self.hourly(hq)
        pol = self.policy
        per_day = {}
        for ts in series:
            d = _iso(ts)[:10]
            per_day[d] = per_day.get(d, 0) + 1
        valid_days = sum(1 for n in per_day.values() if n >= pol["valid_day_min_hours"])
        coverage = min(1.0, len(series) / (self.days * 24))
        by_hour = {}
        for ts, v in series.items():
            by_hour.setdefault(int(ts // HOUR) % 24, []).append(v)
        means = {h: sum(v) / len(v) for h, v in by_hour.items()}
        peak = sorted(h for h, x in means.items() if means and x >= 0.9 * max(means.values())) if means else []
        w = f"{self.win}@1h"
        m["valid_days"], m["coverage"], m["peak_hours"] = (hq, w, float(valid_days)), (hq, w, round(coverage, 6)), (hq, w, peak)

        throttle = None
        if cpu:
            qt_ = f"sum by (workload)(increase({CFS_THROTTLED}{{{sel}}}[1h]))"
            qp = f"sum by (workload)(increase({CFS_PERIODS}{{{sel}}}[1h]))"
            thr, per = self.hourly(qt_), self.hourly(qp)
            ratios = [thr[ts] / per[ts] for ts in sorted(per) if ts in thr and per[ts] > 0 and int(ts // HOUR) % 24 in peak]
            throttle = max(ratios) if ratios else None
            m["throttle"] = (f"({qt_}) / ({qp})", w, None if throttle is None else round(throttle, 6))
        return m, valid_days, coverage, peak, throttle


def collect_facts(prom: Prom, policy: dict, as_of: float | None = None, pricing: dict | None = None,
                  min_nodes: int = 1, max_nodes: int | None = None) -> dict:
    """-> {as_of, facts: [per (workload, resource) facts], cluster}. as_of is epoch seconds; default = the source's latest point."""
    c = Collector(prom, policy, as_of)
    cluster = c.cluster(pricing or policy["pricing"], min_nodes, max_nodes)
    facts = []
    for ns, wl, team in c.workloads():
        shared, hpa = c.shared(ns, wl)
        for resource in ("cpu", "memory"):
            rm, valid_days, coverage, peak, throttle = c.resource_measures(ns, wl, resource)
            book = Evidence()
            order = ["request", "limit"] + ["replicas"] + ["p50", "p95", "p99", "max", "valid_days", "coverage", "peak_hours"]
            order += ["throttle"] if resource == "cpu" else []
            for k in order:
                src = shared if k == "replicas" else rm
                book.add(k, *src[k])
            for k in ("oom", "hpa", "hpa_current", "hpa_max", "burn", "budget", "job", "deadline"):
                if k in shared:
                    book.add(k, *shared[k])
            for k, meas in cluster["evidence"].items():
                book.add(k, *meas)
            unit = "core" if resource == "cpu" else "GiB"
            facts.append({
                "id": f"K-{ns}-{wl}-{resource}", "workload": wl, "namespace": ns, "team": team, "resource": resource,
                "current": {"request": rm["request"][2], "limit": rm["limit"][2],
                            "replicas": None if shared["replicas"][2] is None else int(shared["replicas"][2]), "unit": unit},
                "usage": {"p50": rm["p50"][2], "p95": rm["p95"][2], "p99": rm["p99"][2], "max": rm["max"][2],
                          "valid_days": valid_days, "coverage": round(coverage, 6), "peak_hours_utc": peak,
                          "throttle_peak": None if throttle is None else round(throttle, 6),
                          "oom_events": shared["oom"][2]},
                "hpa": hpa,
                "slo": {"burn_rate_1h": shared["burn"][2], "budget_remaining": shared["budget"][2],
                        "job_max_duration": shared["job"][2], "deadline_seconds": shared["deadline"][2]},
                "evidence": book.items, "ev": book.ev,
            })
    cluster = {k: v for k, v in cluster.items() if k != "evidence"}
    return {"as_of": _iso(c.t), "facts": facts, "cluster": cluster}
