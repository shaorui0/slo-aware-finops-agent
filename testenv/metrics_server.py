#!/usr/bin/env python3
"""test-metrics-server: a fake Prometheus HTTP API over the synthetic account.

Endpoints: /api/v1/query, /api/v1/query_range, /api/v1/series, /api/v1/labels,
/api/v1/label/<name>/values, /api/v1/metadata, /-/healthy.

PromQL subset:
  selector          name{label="v", label=~"re", label!="v", label!~"re"}
  range functions   rate, increase, avg_over_time, max_over_time, min_over_time,
                    quantile_over_time(q, ...)          over  selector[5m|1h|1d|30d]
  aggregations      sum | avg | max | min | count  [by (labels)] ( expr )
Anything else returns a Prometheus-style bad_data error.

Data exists only inside world.yaml `account.period`, at 5-minute resolution.
Usage: python3 -m testenv.metrics_server [--port 9090] [--world k8s|<file>] [--as-of <ts>]

`--world k8s` (or a k8s world/scenario yaml) serves the synthetic K8s world of testenv/k8s_world.py instead:
cAdvisor / kube-state-metrics / SLO series per pod, over the same PromQL subset. Points before a workload's
`data_days` are absent. Without the flag everything below is unchanged.
"""
from __future__ import annotations

import argparse
import bisect
import functools
import json
import math
import random
import re
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from testenv import k8s_world
from testenv.world import JOB_BY_PROFILE, STEP, cpu, load, use as use_world, _hour, _office, _weekday, _bell

METADATA = {
    "aws_ec2_cpuutilization_average": ("gauge", "CloudWatch CPUUtilization, percent (cloudwatch-exporter)"),
    "aws_ec2_network_out_bytes_sum": ("gauge", "CloudWatch NetworkOut bytes per 5m period"),
    "aws_natgateway_bytes_out_to_destination_sum": ("gauge", "CloudWatch NAT BytesOutToDestination per 5m period"),
    "up": ("gauge", "Scrape target health"),
    "http_requests_total": ("counter", "HTTP requests served"),
    "http_request_duration_p99_seconds": ("gauge", "p99 request latency over the last 5m"),
    "ingest_jobs_total": ("counter", "Ingest jobs finished, by final status"),
    "ingest_job_retries_total": ("counter", "Ingest job attempts that were retried"),
    "ingest_downstream_errors_total": ("counter", "Errors returned by the downstream queue"),
    "db_client_bytes_total": ("counter", "Bytes exchanged between a service and its database"),
}


class Series:
    __slots__ = ("labels", "kind", "values")

    def __init__(self, labels, kind, values):
        self.labels, self.kind, self.values = labels, kind, values


K8S = None   # None = AWS world; else {"path": ..., "as_of": ...} (set with use_k8s)


def use_k8s(enabled=True, path=None, as_of=None):
    """Serve the K8s world (testenv/k8s_world.py) instead of the AWS one; use_k8s(False) switches back."""
    global K8S
    K8S = {"path": path, "as_of": as_of} if enabled else None
    build.cache_clear()


def _build_k8s():
    grid, raw = k8s_world.build_series(k8s_world.load(K8S["path"], K8S["as_of"]))
    return grid, [Series({"__name__": l["__name__"], **{k: v for k, v in l.items() if k != "__name__"}}, kind, vals) for l, kind, vals in raw]


@functools.lru_cache(maxsize=1)
def build():
    if K8S is not None:
        return _build_k8s()
    w = load()
    s, e = w.period
    t0 = datetime(s.year, s.month, s.day, tzinfo=timezone.utc).timestamp()
    t1 = datetime(e.year, e.month, e.day, tzinfo=timezone.utc).timestamp()
    grid = [t0 + k * STEP for k in range(int((t1 - t0) // STEP))]
    out = []

    def gauge(labels, fn, seed):
        rng = random.Random(seed)
        out.append(Series(labels, "gauge", [fn(t, rng) for t in grid]))

    def counter(labels, rate_fn, seed):
        rng = random.Random(seed)
        acc, vals = 0.0, []
        for t in grid:
            acc += max(0.0, rate_fn(t, rng)) * STEP
            vals.append(acc)
        out.append(Series(labels, "counter", vals))

    u = w.raw["usage"]
    api_ids = [i["id"] for i in w.instances if i["profile"] == "api_steady"]
    ingest_ids = [i["id"] for i in w.instances if i["profile"] == "ingest_retry_storm"]
    db_bytes_per_sec = u["api_to_db_gb_month"] * 1e9 / (30 * 86400) / len(api_ids)
    net_base = {"web_tidal": 4e6, "api_steady": db_bytes_per_sec, "db_steady": db_bytes_per_sec * len(api_ids) * 0.6,
                "ingest_retry_storm": 2e6, "etl_nightly": 1e6, "dev_office_hours": 2e5, "dr_standby": 5e4}

    def web_load(t):
        return (0.6 if _weekday(t) >= 5 else 1.0) * _bell(_hour(t), 0, 11)

    for inst in w.instances:
        if inst.get("state") == "stopped":
            continue
        iid, prof, az = inst["id"], inst["profile"], inst["az"]
        job = JOB_BY_PROFILE[prof]
        base = {"instance_id": iid, "availability_zone": az}
        gauge({"__name__": "aws_ec2_cpuutilization_average", **base}, lambda t, r, i=inst: round(cpu(w, i, t, r), 2), iid + "cpu")
        nb = net_base[prof]
        gauge({"__name__": "aws_ec2_network_out_bytes_sum", **base},
              lambda t, r, nb=nb, p=prof: nb * STEP * ((0.2 + web_load(t)) if p == "web_tidal" else (1 if p != "dev_office_hours" or _office(t) else 0.05)) * r.uniform(0.9, 1.1),
              iid + "net")
        gauge({"__name__": "up", "job": job, **base}, lambda t, r: 1.0, iid + "up")

        if prof == "web_tidal":
            counter({"__name__": "http_requests_total", "job": job, "instance_id": iid}, lambda t, r: 5 + 220 * web_load(t) * r.uniform(0.9, 1.1), iid + "rq")
        if prof == "api_steady":
            counter({"__name__": "http_requests_total", "job": job, "instance_id": iid}, lambda t, r: 400 * r.uniform(0.95, 1.05), iid + "rq")
            counter({"__name__": "db_client_bytes_total", "job": job, "instance_id": iid, "db": "orders-db"},
                    lambda t, r: db_bytes_per_sec * r.uniform(0.97, 1.03), iid + "db")
        if prof == "dev_office_hours":
            counter({"__name__": "http_requests_total", "job": job, "instance_id": iid}, lambda t, r: (3 * r.uniform(0.5, 1.5)) if _office(t) else 0.0, iid + "rq")
        if prof == "ingest_retry_storm":
            n = len(ingest_ids)
            ok = u["ingest_attempts_per_hour_ok"] / 3600 / n
            mult = u["ingest_retry_multiplier"]
            inc = w.incident_ts
            counter({"__name__": "ingest_jobs_total", "job": job, "instance_id": iid, "status": "succeeded"},
                    lambda t, r: ok * r.uniform(0.9, 1.1) if t < inc else 0.0, iid + "ok")
            counter({"__name__": "ingest_jobs_total", "job": job, "instance_id": iid, "status": "failed"},
                    lambda t, r: ok * 0.002 if t < inc else ok * r.uniform(0.9, 1.1), iid + "fail")
            counter({"__name__": "ingest_job_retries_total", "job": job, "instance_id": iid},
                    lambda t, r: ok * 0.01 if t < inc else ok * (mult - 1) * r.uniform(0.95, 1.05), iid + "retry")
            counter({"__name__": "ingest_downstream_errors_total", "job": job, "instance_id": iid, "reason": "QueueDoesNotExist"},
                    lambda t, r: 0.0 if t < inc else ok * mult * r.uniform(0.95, 1.05), iid + "derr")

    web_jobs = {"web": web_load, "api": lambda t: 0.5}
    for job, ld in web_jobs.items():
        gauge({"__name__": "http_request_duration_p99_seconds", "job": job},
              lambda t, r, ld=ld: round(0.12 + 0.25 * ld(t) ** 3 + r.uniform(0, 0.02), 4), job + "p99")

    for nat in w.raw["nat_gateways"]:
        def nat_bytes(t, r):
            day = datetime.fromtimestamp(t, timezone.utc).date()
            base = u["nat_gb_baseline_month"] / 30 / 288
            attempts = w.ingest_attempts_per_hour(t) / 12
            return (base + attempts * u["ingest_s3_gb_per_attempt"]) * 1e9 * r.uniform(0.97, 1.03)
        gauge({"__name__": "aws_natgateway_bytes_out_to_destination_sum", "nat_gateway_id": nat["id"]}, nat_bytes, nat["id"])
    return grid, out


# ---- PromQL subset -----------------------------------------------------------
class BadQuery(Exception):
    pass


DUR = re.compile(r"^(\d+)([smhdw])$")
UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
SEL = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)?\s*(\{(.*)\})?\s*(\[(\w+)\])?$", re.S)
MATCHER = re.compile(r'\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*"((?:[^"\\]|\\.)*)"\s*,?')
AGG = re.compile(r"^(sum|avg|max|min|count)\s*(?:by\s*\(([^)]*)\))?\s*\((.*)\)\s*(?:by\s*\(([^)]*)\))?$", re.S)
FUNC = re.compile(r"^(rate|increase|avg_over_time|max_over_time|min_over_time|quantile_over_time)\s*\((.*)\)$", re.S)


def duration(s):
    m = DUR.match(s.strip())
    if not m:
        raise BadQuery(f"invalid duration {s!r}")
    return int(m.group(1)) * UNIT[m.group(2)]


def parse_selector(text):
    m = SEL.match(text.strip())
    if not m:
        raise BadQuery(f"unsupported expression: {text.strip()!r}")
    name, body, rng = m.group(1), m.group(3) or "", m.group(5)
    matchers = []
    if name:
        matchers.append(("__name__", "=", name))
    pos = 0
    while pos < len(body.strip()):
        mm = MATCHER.match(body, pos)
        if not mm:
            raise BadQuery(f"bad label matcher in {text.strip()!r}")
        matchers.append((mm.group(1), mm.group(2), mm.group(3)))
        pos = mm.end()
    if not matchers:
        raise BadQuery("vector selector must contain at least one matcher")
    return matchers, (duration(rng) if rng else None)


def match(series, matchers):
    for k, op, v in matchers:
        actual = series.labels.get(k, "")
        if op == "=" and actual != v:
            return False
        if op == "!=" and actual == v:
            return False
        if op == "=~" and not re.fullmatch(v, actual):
            return False
        if op == "!~" and re.fullmatch(v, actual):
            return False
    return True


def _split_args(s):
    depth, cur, out = 0, "", []
    for ch in s:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out]


def evaluate(expr, t):
    """Return list of (labels, value) at time t."""
    grid, series = build()
    expr = expr.strip()
    m = AGG.match(expr)
    if m:
        op, by1, inner, by2 = m.groups()
        by = [x.strip() for x in (by1 or by2 or "").split(",") if x.strip()]
        groups = {}
        for labels, v in evaluate(inner, t):
            key = tuple((k, labels.get(k, "")) for k in by)
            groups.setdefault(key, []).append(v)
        fn = {"sum": sum, "avg": lambda xs: sum(xs) / len(xs), "max": max, "min": min, "count": len}[op]
        return [(dict(k), fn(vs)) for k, vs in groups.items()]
    m = FUNC.match(expr)
    if m:
        fn, args = m.group(1), _split_args(m.group(2))
        q = None
        if fn == "quantile_over_time":
            if len(args) != 2:
                raise BadQuery("quantile_over_time expects 2 arguments")
            q, args = float(args[0]), args[1:]
        matchers, rng = parse_selector(args[0])
        if rng is None:
            raise BadQuery(f"{fn} expects a range vector")
        out = []
        for s in series:
            if not match(s, matchers):
                continue
            lo, hi = bisect.bisect_right(grid, t - rng), bisect.bisect_right(grid, t)
            window = [v for v in s.values[lo:hi] if v is not None]
            if len(window) < 2 and fn in ("rate", "increase") or not window:
                continue
            labels = {k: v for k, v in s.labels.items() if k != "__name__"}
            if fn in ("rate", "increase"):
                inc = window[-1] - window[0]
                val = inc / (STEP * (len(window) - 1)) if fn == "rate" else inc * (len(window)) / (len(window) - 1)
            elif fn == "avg_over_time":
                val = sum(window) / len(window)
            elif fn == "max_over_time":
                val = max(window)
            elif fn == "min_over_time":
                val = min(window)
            else:
                xs = sorted(window)
                val = xs[min(len(xs) - 1, max(0, math.ceil(q * len(xs)) - 1))]
            out.append((labels, val))
        return out
    matchers, rng = parse_selector(expr)
    if rng is not None:
        raise BadQuery("range vector selectors are only supported inside range functions")
    out = []
    idx = bisect.bisect_right(grid, t) - 1
    if idx < 0 or t - grid[idx] > 300:
        return []
    for s in series:
        if match(s, matchers) and s.values[idx] is not None:
            out.append((dict(s.labels), s.values[idx]))
    return out


# ---- HTTP -------------------------------------------------------------------
def _ts(v, default):
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()


def _fmt(v):
    return str(round(v, 6)) if math.isfinite(v) else "NaN"


class Handler(BaseHTTPRequestHandler):
    server_version = "test-metrics-server/0.1"

    def log_message(self, *a):
        pass

    def _json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        self._handle(urlparse(self.path).path, parse_qs(body))

    def do_GET(self):
        u = urlparse(self.path)
        self._handle(u.path, parse_qs(u.query))

    def _handle(self, path, qs):
        grid, series = build()
        one = lambda k, d=None: qs.get(k, [d])[0]
        end_default = grid[-1]
        try:
            if path == "/-/healthy":
                return self._json(200, {"status": "success"})
            if path == "/api/v1/query":
                t = _ts(one("time"), end_default)
                res = evaluate(one("query", ""), t)
                return self._json(200, {"status": "success", "data": {"resultType": "vector",
                                  "result": [{"metric": l, "value": [t, _fmt(v)]} for l, v in res]}})
            if path == "/api/v1/query_range":
                start, end = _ts(one("start"), grid[0]), _ts(one("end"), end_default)
                step = duration(one("step")) if not str(one("step", "300")).replace(".", "").isdigit() else float(one("step", "300"))
                if (end - start) / step > 11000:
                    raise BadQuery("exceeded maximum resolution of 11,000 points per timeseries. Try decreasing the query resolution (?step=XX)")
                acc = {}
                t = start
                while t <= end:
                    for l, v in evaluate(one("query", ""), t):
                        acc.setdefault(json.dumps(l, sort_keys=True), []).append([t, _fmt(v)])
                    t += step
                return self._json(200, {"status": "success", "data": {"resultType": "matrix",
                                  "result": [{"metric": json.loads(k), "values": v} for k, v in acc.items()]}})
            if path == "/api/v1/series":
                ms = [parse_selector(m)[0] for m in qs.get("match[]", [])]
                if not ms:
                    raise BadQuery("no match[] parameter provided")
                return self._json(200, {"status": "success", "data": [s.labels for s in series if any(match(s, m) for m in ms)]})
            if path == "/api/v1/labels":
                return self._json(200, {"status": "success", "data": sorted({k for s in series for k in s.labels})})
            m = re.match(r"^/api/v1/label/([^/]+)/values$", path)
            if m:
                return self._json(200, {"status": "success", "data": sorted({s.labels[m.group(1)] for s in series if m.group(1) in s.labels})})
            if path == "/api/v1/metadata":
                return self._json(200, {"status": "success", "data": {k: [{"type": t, "help": h, "unit": ""}] for k, (t, h) in ({**METADATA, **k8s_world.K8S_METADATA} if K8S is not None else METADATA).items()}})
            return self._json(404, {"status": "error", "errorType": "not_found", "error": path})
        except BadQuery as e:
            return self._json(400, {"status": "error", "errorType": "bad_data", "error": str(e)})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9090)
    ap.add_argument("--world", help="world file (full world or scenario overlay); default testenv/world.yaml; "
                                    "`k8s` or a k8s world/scenario yaml serves the K8s world")
    ap.add_argument("--as-of", help="k8s world only: truncate the world at this timestamp (ISO or epoch)")
    a = ap.parse_args(argv)
    if a.world == "k8s" or (a.world and k8s_world.is_k8s_world_file(a.world)):
        use_k8s(True, None if a.world == "k8s" else a.world, a.as_of)
    else:
        use_k8s(False)
        use_world(a.world)
    build()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"test-metrics-server listening on http://{a.host}:{a.port}", file=sys.stderr, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
