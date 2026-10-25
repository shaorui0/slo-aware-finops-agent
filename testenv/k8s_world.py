"""Synthetic K8s world for the SLO-aware FinOps Agent mainline (c0028).

Single source of truth: `testenv/k8s_world.yaml` (schema fixed by the c0027 `k8s-mainline` spec).
`testenv.metrics_server --world k8s` serves every series built here as a fake Prometheus.
The agent never reads the yaml; the eval reads `expect`.

Generators are deterministic: one `random.Random` per series, seeded by name, drawn a fixed number of
times per 5-minute step, so extending the period never changes the earlier samples.

Drift / "day 3" runs. Two equivalent ways, pick one:
  1. `load(as_of=<ts>)`: the world is truncated at `as_of`; the metrics server takes the same
     value as `--as-of` (or the usual `time` query param, which only moves the instant-query cursor).
  2. A scenario file extends the period and adds events. `testenv/k8s_scenarios/drift_day3.yaml`
     moves period.end two days later and raises api-steady load by 60% from the last day.
     `python3 -m testenv.metrics_server --world testenv/k8s_scenarios/drift_day3.yaml` serves it;
     `--as-of 2026-10-26T00:00:00Z` on the same file reproduces the day-1 world.
Scenario schema: `scenario: {name, base}`, optional `cluster: {period: {end}}`, and
`add_events: {<workload>: [event, ...]}` appended to that workload's events.

Event types (all absolute timestamps):
  load      {at, factor}                    multiplies cpu usage and request rate from `at`
  oom       {at, pod}                       working set hits the limit, container restarts, last_terminated_reason=OOMKilled
  slo_burn  {at, burn_rate, budget_remaining, ramp_hours}   burn rate and error ratio ramp up, budget runs down

Profiles (per workload): cpu.kind = flat | tidal | batch; mem.kind = flat | batch | leak.
`data_days` limits the samples to the last N days of the (possibly truncated) period; earlier points are None.
"""
from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

STEP = 300
WORLD_FILE = Path(__file__).with_name("k8s_world.yaml")
GIB = 1024 ** 3
PERIODS_PER_STEP = 3000          # 100ms CFS period -> 10/s * 300s
PODS_HASH = "6f7c9d"

K8S_METADATA = {
    "container_cpu_usage_seconds_total": ("counter", "Cumulative cpu time consumed by the container in core-seconds (cAdvisor)"),
    "container_cpu_usage_cores": ("gauge", "Average cpu cores used by the container over the last 5m (recording rule over rate of container_cpu_usage_seconds_total)"),
    "container_memory_working_set_bytes": ("gauge", "Current working set of the container in bytes (cAdvisor)"),
    "container_cpu_cfs_periods_total": ("counter", "Number of elapsed enforcement period intervals (cAdvisor)"),
    "container_cpu_cfs_throttled_periods_total": ("counter", "Number of throttled period intervals (cAdvisor)"),
    "kube_pod_container_resource_requests": ("gauge", "Requested resources by a container (kube-state-metrics)"),
    "kube_pod_container_resource_limits": ("gauge", "Resource limits by a container (kube-state-metrics)"),
    "kube_pod_container_status_restarts_total": ("counter", "Number of container restarts (kube-state-metrics)"),
    "kube_pod_container_status_last_terminated_reason": ("gauge", "Last termination reason of a container, 1 when set (kube-state-metrics)"),
    "kube_deployment_spec_replicas": ("gauge", "Desired replicas of a deployment (kube-state-metrics)"),
    "kube_horizontalpodautoscaler_spec_target_metric": ("gauge", "Target of the HPA metric, utilization percent (kube-state-metrics)"),
    "kube_horizontalpodautoscaler_status_current_replicas": ("gauge", "Current replicas seen by the HPA (kube-state-metrics)"),
    "kube_horizontalpodautoscaler_spec_max_replicas": ("gauge", "Upper replica bound of the HPA (kube-state-metrics)"),
    "kube_node_status_allocatable": ("gauge", "Allocatable resources of a node (kube-state-metrics)"),
    "kube_pod_status_phase": ("gauge", "Pod phase, 1 for the current phase (kube-state-metrics)"),
    "http_requests_total": ("counter", "HTTP requests served, by status code"),
    "http_request_duration_p99_seconds": ("gauge", "p99 request latency over the last 5m (pre-aggregated recording rule)"),
    "slo_error_budget_remaining_ratio": ("gauge", "Remaining error budget of the SLO, 0..1 (recording rule)"),
    "slo_burn_rate_1h": ("gauge", "Error budget burn rate over the last 1h, 1 = exactly on budget (recording rule)"),
    "job_last_duration_seconds": ("gauge", "Wall-clock duration of the last completed batch run"),
}


def _ts(s) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    d = s if isinstance(s, datetime) else datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def parse_qty(v, unit: str) -> float:
    """'2' / '500m' -> cores; '4Gi' / '512Mi' -> GiB (unit 'core' or 'GiB')."""
    s = str(v)
    if unit == "core":
        return float(s[:-1]) / 1000 if s.endswith("m") else float(s)
    if s.endswith("Gi"):
        return float(s[:-2])
    if s.endswith("Mi"):
        return float(s[:-2]) / 1024
    raise ValueError(f"bad memory quantity {v!r}")


@dataclass
class K8sWorld:
    raw: dict
    t0: float
    t1: float

    @property
    def grid(self) -> list[float]:
        return [self.t0 + k * STEP for k in range(int((self.t1 - self.t0) // STEP))]

    @property
    def pricing(self) -> dict:
        return self.raw["pricing"]

    @property
    def workloads(self) -> list[dict]:
        return self.raw["workloads"]

    def workload(self, name: str) -> dict:
        return next(w for w in self.workloads if w["name"] == name)

    @property
    def node_count(self) -> int:
        return self.raw["nodes"]["count"]

    def pods(self, w: dict) -> list[str]:
        return [f"{w['name']}-{PODS_HASH}-{'abcdefghij'[i]}" for i in range(w["replicas"])]

    def request(self, w: dict, resource: str) -> float:
        c = w["container"]["requests"]
        return parse_qty(c[resource], "core" if resource == "cpu" else "GiB")

    def limit(self, w: dict, resource: str) -> float:
        c = w["container"]["limits"]
        return parse_qty(c[resource], "core" if resource == "cpu" else "GiB")


def read_world(path=None) -> dict:
    p = Path(path) if path else WORLD_FILE
    raw = yaml.safe_load(p.read_text())
    if "scenario" in raw:
        base = Path(raw["scenario"]["base"])
        if not base.is_absolute():
            base = (Path(__file__).parent.parent / base) if (Path(__file__).parent.parent / base).exists() else (p.parent / base)
        w = read_world(base)
        if "cluster" in raw:
            _merge(w["cluster"], raw["cluster"])
        for name, evs in (raw.get("add_events") or {}).items():
            wl = next(x for x in w["workloads"] if x["name"] == name)
            wl.setdefault("events", []).extend(copy.deepcopy(evs))
        w["scenario"] = raw["scenario"]
        return w
    return raw


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


def is_k8s_world_file(path) -> bool:
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except Exception:
        return False
    return isinstance(raw, dict) and ("cluster" in raw and "workloads" in raw or "scenario" in raw and "k8s" in str(raw["scenario"].get("base", "")))


def load(path=None, as_of=None) -> K8sWorld:
    raw = read_world(path)
    p = raw["cluster"]["period"]
    t0, t1 = _ts(p["start"]), _ts(p["end"])
    if as_of is not None:
        t1 = min(t1, _ts(as_of))
        t1 = t0 + ((t1 - t0) // STEP) * STEP
    return K8sWorld(raw, t0, t1)


# ---- generators -----------------------------------------------------------------
def _hour(t: float) -> float:
    return (t % 86400) / 3600


def _events(w: dict, kind: str) -> list[dict]:
    return [e for e in w.get("events", []) if e["type"] == kind]


def _bell(h: float, peak: float, sigma: float) -> float:
    d = min(abs(h - peak), 24 - abs(h - peak))
    return math.exp(-0.5 * (d / sigma) ** 2)


def _load_factor(w: dict, t: float) -> float:
    f = 1.0
    for e in _events(w, "load"):
        if t >= _ts(e["at"]):
            f *= e["factor"]
    return f


def cpu_cores(world: K8sWorld, w: dict, i: int, grid: list[float]) -> list[float]:
    p = w["profile"]["cpu"]
    n = w["replicas"]
    spread = 1 + w["profile"].get("per_pod_spread", 0.02) * (i - (n - 1) / 2)
    rng = random.Random(f"{w['name']}:{i}:cpu")
    out = []
    for t in grid:
        j = rng.uniform(-1, 1) * p.get("jitter", 0.05)
        h = _hour(t)
        if p["kind"] == "flat":
            u = p["base"] * (1 + j)
        elif p["kind"] == "tidal":
            u = p["base"] + p["amp"] * _bell(h, p["peak_hour"], p["sigma_h"])
            if abs(h - p["peak_hour"]) < p.get("spike_samples", 0) * STEP / 3600 / 2:
                u = p["spike_value"]
            u *= (1 + j)
        elif p["kind"] == "batch":
            on = p["start_hour"] <= h < p["start_hour"] + p["hours"]
            u = (p["active"] if on else p["idle"]) * (1 + j)
        else:
            raise ValueError(p["kind"])
        out.append(max(0.0, u * spread * _load_factor(w, t)))
    return out


def mem_gib(world: K8sWorld, w: dict, i: int, grid: list[float]) -> list[float]:
    p = w["profile"]["mem"]
    rng = random.Random(f"{w['name']}:{i}:mem")
    cpu = w["profile"]["cpu"]
    limit = world.limit(w, "memory")
    oom = [e for e in _events(w, "oom") if e["pod"] == i]
    t_ref = _ts(oom[0]["at"]) if oom else None
    out = []
    for t in grid:
        j = rng.uniform(-1, 1) * p.get("jitter", 0.02)
        if p["kind"] == "flat":
            v = p["base_gib"] * (1 + j)
        elif p["kind"] == "batch":
            h = _hour(t)
            on = cpu["start_hour"] <= h < cpu["start_hour"] + cpu["hours"]
            v = (p["active_gib"] if on else p["idle_gib"]) * (1 + j)
        elif p["kind"] == "leak":
            cyc = p["cycle_hours"] * 3600
            ref = t_ref if (t_ref is not None and t >= t_ref) else world.t0
            v = (p["base_gib"] + (p["peak_gib"] - p["base_gib"]) * ((t - ref) % cyc) / cyc) * (1 + j)
        else:
            raise ValueError(p["kind"])
        if oom and abs(t - t_ref) < STEP / 2:
            v = limit * 0.995
        out.append(min(v, limit * 0.995))
    return out


def throttle_ratio(world: K8sWorld, w: dict, cpu: list[float], i: int) -> list[float]:
    th = w["profile"].get("throttle", {})
    limit = world.limit(w, "cpu")
    rng = random.Random(f"{w['name']}:{i}:thr")
    out = []
    for u in cpu:
        r = max(0.0, (u / limit - th.get("onset", 0.95)) * th.get("slope", 1.0))
        out.append(min(th.get("cap", 0.6), r) + rng.uniform(0, 0.002))
    return out


def _slo(world: K8sWorld, w: dict, grid: list[float], thr: list[float]):
    """(budget_remaining, burn_rate_1h, error_ratio) per step, workload level."""
    rng = random.Random(f"{w['name']}:slo")
    burns = _events(w, "slo_burn")
    budget, burn, err = [], [], []
    for k, t in enumerate(grid):
        b = 0.82 + 0.02 * math.sin(t / 86400 / 3) + rng.uniform(-0.005, 0.005)
        r = 0.3 + 0.8 * thr[k] + rng.uniform(-0.05, 0.05)
        for e in burns:
            a = _ts(e["at"])
            if t >= a:
                f = min(1.0, (t - a) / (e.get("ramp_hours", 8) * 3600))
                b = 0.6 + (e["budget_remaining"] - 0.6) * f
                r = 0.4 + (e["burn_rate"] - 0.4) * min(1.0, (t - a) / 3600) + rng.uniform(-0.05, 0.05)
            else:
                b = 0.6
        budget.append(max(0.0, b))
        burn.append(max(0.0, r))
        err.append(0.001 * (1 + rng.uniform(-0.2, 0.2)) + max(0.0, r - 1) * 0.012)
    return budget, burn, err


def build_series(world: K8sWorld):
    """-> (grid, [(labels, kind, values)]); values align with grid, None = no sample (before data_days)."""
    grid = world.grid
    out: list[tuple[dict, str, list]] = []
    end = world.t1
    for w in world.workloads:
        ns, name, cname = w["namespace"], w["name"], w["container"]["name"]
        first = max(0, int((end - w.get("data_days", 10 ** 6) * 86400 - world.t0) // STEP)) if w.get("data_days") else 0
        mask = lambda vals: [None] * first + vals[first:]
        base = {"namespace": ns, "workload": name, "container": cname}

        def emit(metric, labels, kind, vals):
            out.append(({"__name__": metric, **labels}, kind, mask(vals)))

        n = len(grid)
        cpu_req, cpu_lim = world.request(w, "cpu"), world.limit(w, "cpu")
        mem_req, mem_lim = world.request(w, "memory") * GIB, world.limit(w, "memory") * GIB
        slo_thr = [0.0] * n
        pod_thr = []
        for i, pod in enumerate(world.pods(w)):
            lab = {**base, "pod": pod}
            cpu = cpu_cores(world, w, i, grid)
            mem = mem_gib(world, w, i, grid)
            thr = throttle_ratio(world, w, cpu, i)
            pod_thr.append(thr)
            acc, cnt = 0.0, []
            for u in cpu:
                acc += u * STEP
                cnt.append(round(acc, 3))
            emit("container_cpu_usage_seconds_total", lab, "counter", cnt)
            emit("container_cpu_usage_cores", lab, "gauge", [round(u, 5) for u in cpu])
            emit("container_memory_working_set_bytes", lab, "gauge", [round(m * GIB) for m in mem])
            periods = [float(PERIODS_PER_STEP * (k + 1)) for k in range(n)]
            emit("container_cpu_cfs_periods_total", lab, "counter", periods)
            acc, th_cnt = 0.0, []
            for r in thr:
                acc += PERIODS_PER_STEP * r
                th_cnt.append(round(acc, 2))
            emit("container_cpu_cfs_throttled_periods_total", lab, "counter", th_cnt)
            for res, unit, rq, lm in (("cpu", "core", cpu_req, cpu_lim), ("memory", "byte", mem_req, mem_lim)):
                emit("kube_pod_container_resource_requests", {**lab, "resource": res, "unit": unit}, "gauge", [float(rq)] * n)
                emit("kube_pod_container_resource_limits", {**lab, "resource": res, "unit": unit}, "gauge", [float(lm)] * n)
            ooms = [_ts(e["at"]) for e in _events(w, "oom") if e["pod"] == i]
            emit("kube_pod_container_status_restarts_total", lab, "counter", [float(sum(t >= o for o in ooms)) for t in grid])
            emit("kube_pod_container_status_last_terminated_reason", {**lab, "reason": "OOMKilled"}, "gauge",
                 [1.0 if any(t >= o for o in ooms) else 0.0 for t in grid])
            for phase in ("Running", "Pending"):
                emit("kube_pod_status_phase", {"namespace": ns, "workload": name, "pod": pod, "phase": phase}, "gauge",
                     [1.0 if phase == "Running" else 0.0] * n)
            if "http" in w["profile"]:
                h = w["profile"]["http"]
                ref = w["profile"]["cpu"].get("base", 1.0)
                lat = random.Random(f"{w['name']}:{i}:lat")
                rq_rng = random.Random(f"{w['name']}:{i}:rq")
                _, burn, err = _slo(world, w, grid, thr)
                ok_c, bad_c, acc_ok, acc_bad, p99 = [], [], 0.0, 0.0, []
                for k, t in enumerate(grid):
                    rps = h["rps"] * min(cpu[k] / ref, 12) * rq_rng.uniform(0.95, 1.05)
                    acc_ok += rps * (1 - err[k]) * STEP
                    acc_bad += rps * err[k] * STEP
                    ok_c.append(round(acc_ok, 1))
                    bad_c.append(round(acc_bad, 1))
                    p99.append(round(h["p99_base"] + h.get("p99_per_throttle", 0.3) * thr[k] + (0.15 if burn[k] > 1 else 0) + lat.uniform(0, 0.01), 4))
                emit("http_requests_total", {**lab, "code": "200"}, "counter", ok_c)
                emit("http_requests_total", {**lab, "code": "500"}, "counter", bad_c)
                emit("http_request_duration_p99_seconds", lab, "gauge", p99)
        # workload level
        emit("kube_deployment_spec_replicas", base, "gauge", [float(w["replicas"])] * n)
        if "hpa" in w:
            h = w["hpa"]
            hl = {**base, "horizontalpodautoscaler": name}
            emit("kube_horizontalpodautoscaler_spec_target_metric", {**hl, "metric_name": h["metric"], "metric_target_type": "utilization"}, "gauge", [float(h["target_utilization"])] * n)
            emit("kube_horizontalpodautoscaler_status_current_replicas", hl, "gauge", [float(w["replicas"])] * n)
            emit("kube_horizontalpodautoscaler_spec_max_replicas", hl, "gauge", [float(h["max"])] * n)
        if "slo" in w:
            thr0 = pod_thr[0]
            budget, burn, _ = _slo(world, w, grid, thr0)
            emit("slo_error_budget_remaining_ratio", base, "gauge", [round(x, 4) for x in budget])
            emit("slo_burn_rate_1h", base, "gauge", [round(x, 4) for x in burn])
        if w["profile"]["cpu"]["kind"] == "batch":
            rng = random.Random(f"{name}:job")
            p = w["profile"]["job"]
            by_day, vals = {}, []
            for t in grid:
                d = int(t // 86400)
                if d not in by_day:
                    by_day[d] = p["base_seconds"] + rng.uniform(-1, 1) * p["jitter_seconds"]
                finished = (t % 86400) / 3600 >= p["finish_hour"]
                vals.append(round(by_day[d] if finished or d - 1 not in by_day else by_day[d - 1], 1))
            emit("job_last_duration_seconds", base, "gauge", vals)
    nodes = world.raw["nodes"]
    for k in range(nodes["count"]):
        nl = {"node": f"ip-10-0-1-{10 + k}"}
        n = len(grid)
        out.append(({"__name__": "kube_node_status_allocatable", **nl, "resource": "cpu", "unit": "core"}, "gauge", [float(nodes["allocatable"]["cpu"])] * n))
        out.append(({"__name__": "kube_node_status_allocatable", **nl, "resource": "memory", "unit": "byte"}, "gauge", [nodes["allocatable"]["memory_gib"] * GIB] * n))
    return grid, out
