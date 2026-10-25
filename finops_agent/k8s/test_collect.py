import json
import re
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from finops_agent.collect import Trace
from finops_agent.k8s import collect as C
from finops_agent.k8s.policy import decide, load_policy
from finops_agent.k8s.collect import Prom, collect_facts

POLICY = load_policy()
AS_OF = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()
GIB = 1024 ** 3

# per-workload canned world; cpu/mem are (p50, p95, p99, max)
WORLD = {
    ("shop", "api"): dict(team="pay", days=21, replicas=2, cpu=(0.2, 0.4, 0.5, 0.9), mem=(1.0, 1.4, 1.5, 2.0),
                          req=(2.0, 4.0), lim=(4.0, 6.0), throttle=0.01, oom=None, hpa=None, burn=0.5, budget=0.8),
    ("shop", "web"): dict(team="web", days=5, replicas=3, cpu=(0.1, 0.2, 0.3, 0.4), mem=(0.5, 0.6, 0.7, 0.8),
                          req=(1.0, 2.0), lim=(2.0, 3.0), throttle=0.0, oom=1.0, hpa=("cpu", 70, 3, 10), burn=None, budget=None,
                          job=(5000, 7200)),
}
NODE = {"cpu": 3.8, "memory": 14.5 * GIB}


def vec(metric, v):
    return {"metric": metric, "value": [AS_OF, str(v)]}


def hourly_series(days, base, peak, as_of=AS_OF):
    span = min(days, 14)
    return [[as_of - span * 86400 + k * 3600, str(peak if int((as_of - span * 86400 + k * 3600) // 3600) % 24 in (9, 10) else base)]
            for k in range(int(span * 24) + 1)]


def fake(path, qs):
    q = qs.get("query", [""])[0]
    wl = re.search(r'workload="([^"]+)"', q)
    w = WORLD.get(("shop", wl.group(1))) if wl else None
    res = lambda rows: {"status": "success", "data": {"resultType": "vector", "result": rows}}
    if path == "/api/v1/query":
        if q == f"max({C.DEPLOY_REPLICAS})":
            return res([vec({}, 1)])
        if q.startswith("max by (namespace, workload, team)"):
            return res([vec({"namespace": ns, "workload": n, "team": d["team"]}, d["replicas"]) for (ns, n), d in WORLD.items()])
        if q.startswith(f"sum({C.POD_PHASE}"):
            return res([])                         # no Pending series ...
        if q == f"count({C.POD_PHASE})":
            return res([vec({}, 12)])              # ... but other phases exist: pending = 0
        if q.startswith(f"count({C.NODE_ALLOC}"):
            return res([vec({}, 3)])
        if q.startswith("min(") and C.NODE_ALLOC in q:
            return res([vec({}, NODE["cpu"] if 'resource="cpu"' in q else NODE["memory"])])
        if q.startswith("max by (namespace, workload, pod)"):
            cpu = 'resource="cpu"' in q
            return res([vec({"namespace": "shop", "workload": n, "pod": f"{n}-{i}"}, d["req"][0] if cpu else d["req"][1] * GIB)
                        for (_, n), d in WORLD.items() for i in range(d["replicas"])])
        if w is None:
            return res([])
        if C.DEPLOY_REPLICAS in q:
            return res([vec({"workload": wl.group(1)}, w["replicas"])])
        i = 0 if 'resource="cpu"' in q else 1
        if C.REQUESTS in q:
            return res([vec({}, w["req"][i] * (1 if i == 0 else GIB))])
        if C.LIMITS in q:
            return res([vec({}, w["lim"][i] * (1 if i == 0 else GIB))])
        cpu = C.CPU_USAGE in q
        u, sc = (w["cpu"], 1) if cpu else (w["mem"], GIB)
        for name, k in (("quantile_over_time(0.5,", 0), ("quantile_over_time(0.95,", 1), ("quantile_over_time(0.99,", 2), ("max_over_time(", 3)):
            if name in q and (C.CPU_USAGE in q or C.MEM_WS in q):
                return res([vec({}, u[k] * sc)])
        if C.LAST_TERMINATED in q:
            return res([vec({}, w["oom"])] if w["oom"] else [])
        if C.HPA_TARGET in q:
            return res([vec({"workload": "web", C.HPA_METRIC_LABEL: w["hpa"][0]}, w["hpa"][1])] if w["hpa"] else [])
        if C.HPA_CURRENT in q:
            return res([vec({}, w["hpa"][2])])
        if C.HPA_MAX in q:
            return res([vec({}, w["hpa"][3])])
        if C.SLO_BURN in q:
            return res([vec({}, w["burn"])] if w["burn"] is not None else [])
        if C.SLO_BUDGET in q:
            return res([vec({}, w["budget"])] if w["budget"] is not None else [])
        if C.JOB_DURATION in q:
            j = w.get("job")
            return res([vec({C.DEADLINE_LABEL: str(j[1])}, j[0])] if j else [])
        return res([])
    if path == "/api/v1/query_range":
        if w is None:
            return res([])
        mat = lambda pts: {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {}, "values": pts}]}}
        if C.CPU_USAGE in q:
            return mat(hourly_series(w["days"], 0.2, 0.5))
        if C.MEM_WS in q:
            return mat(hourly_series(w["days"], GIB, 2 * GIB))
        if C.CFS_PERIODS in q:
            return mat(hourly_series(w["days"], 100, 100))
        if C.CFS_THROTTLED in q:
            return mat(hourly_series(w["days"], 0, 100 * w["throttle"]))
        return mat([])
    return {"status": "error", "error": "unknown path"}


def stub_fetch(url):
    u = urlparse(url)
    return fake(u.path, parse_qs(u.query))


def collect(**kw):
    return collect_facts(Prom("http://prom", Trace(), stub_fetch), POLICY, **kw)


def pick(out, wl, resource):
    return next(f for f in out["facts"] if f["workload"] == wl and f["resource"] == resource)


class CollectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = collect(as_of=AS_OF)

    def test_one_facts_per_workload_and_resource(self):
        self.assertEqual([f["id"] for f in self.out["facts"]],
                         ["K-shop-api-cpu", "K-shop-api-memory", "K-shop-web-cpu", "K-shop-web-memory"])
        self.assertEqual(self.out["as_of"], "2026-10-01T00:00:00Z")

    def test_cpu_facts(self):
        f = pick(self.out, "api", "cpu")
        self.assertEqual(f["team"], "pay")
        self.assertEqual(f["current"], {"request": 2.0, "limit": 4.0, "replicas": 2, "unit": "core"})
        u = f["usage"]
        self.assertEqual((u["p50"], u["p95"], u["p99"], u["max"]), (0.2, 0.4, 0.5, 0.9))
        self.assertEqual((u["valid_days"], u["coverage"]), (14, 1.0))
        self.assertEqual(u["peak_hours_utc"], [9, 10])
        self.assertAlmostEqual(u["throttle_peak"], 0.01)   # only measured in peak hours
        self.assertEqual(u["oom_events"], 0.0)
        self.assertIsNone(f["hpa"])
        self.assertEqual(f["slo"]["burn_rate_1h"], 0.5)

    def test_memory_facts_in_gib(self):
        f = pick(self.out, "api", "memory")
        self.assertEqual(f["current"], {"request": 4.0, "limit": 6.0, "replicas": 2, "unit": "GiB"})
        self.assertEqual((f["usage"]["p99"], f["usage"]["max"]), (1.5, 2.0))
        self.assertIsNone(f["usage"]["throttle_peak"])

    def test_short_history_valid_days_and_coverage(self):
        f = pick(self.out, "web", "cpu")
        self.assertEqual(f["usage"]["valid_days"], 5)
        self.assertAlmostEqual(f["usage"]["coverage"], 121 / 336, places=4)

    def test_hpa_oom_and_job_facts(self):
        f = pick(self.out, "web", "cpu")
        self.assertEqual(f["hpa"], {"metric": "cpu", "target": 70.0, "current_replicas": 3.0, "max_replicas": 10.0})
        self.assertEqual(f["usage"]["oom_events"], 1.0)
        self.assertEqual((f["slo"]["job_max_duration"], f["slo"]["deadline_seconds"]), (5000.0, 7200.0))
        self.assertIsNone(f["slo"]["burn_rate_1h"])

    def test_cluster_facts(self):
        c = self.out["cluster"]
        self.assertEqual(c["allocatable"], {"cpu": 3.8, "memory_gib": 14.5})
        self.assertEqual((c["node_count"], c["pending"]), (3, 0.0))
        self.assertEqual(len(c["pods"]), 5)
        p = next(p for p in c["pods"] if p["pod"] == "api-0")
        self.assertEqual((p["cpu"], p["mem"]), (2.0, 4.0))
        self.assertEqual(c["pricing"]["node_hour_usd"], 0.192)

    def test_every_number_has_evidence(self):
        for f in self.out["facts"]:
            ids = [e["id"] for e in f["evidence"]]
            self.assertEqual(len(ids), len(set(ids)))
            for e in f["evidence"]:
                self.assertEqual(set(e), {"id", "query", "window", "value", "source"})
                self.assertEqual(e["source"], "prometheus")
            vals = [e["value"] for e in f["evidence"]]
            numbers = [v for k in ("request", "limit", "replicas") for v in [f["current"][k]] if v is not None]
            numbers += [f["usage"][k] for k in ("p50", "p95", "p99", "max", "valid_days", "coverage", "throttle_peak", "oom_events")
                        if f["usage"][k] is not None]
            numbers += [v for v in f["slo"].values() if v is not None]
            for n in numbers:
                self.assertTrue(any(isinstance(v, (int, float)) and abs(v - n) < 1e-9 for v in vals), (f["id"], n))
            for k, eid in f["ev"].items():
                self.assertIn(eid, ids)

    def test_queries_only_use_contract_metric_names(self):
        known = {getattr(C, n) for n in dir(C) if n.isupper() and isinstance(getattr(C, n), str)}
        for f in self.out["facts"]:
            for e in f["evidence"]:
                for tok in re.findall(r"\b[a-z]+_[a-z_0-9]+\b", re.sub(r'"[^"]*"', "", e["query"])):
                    if tok in {"max_over_time", "quantile_over_time", "min_over_time", "avg_over_time"}:
                        continue
                    self.assertIn(tok, known | {"by"}, (e["query"], tok))

    def test_nothing_unsupported_in_queries(self):
        for f in self.out["facts"]:
            for e in f["evidence"]:
                self.assertNotRegex(e["query"], r"\[\w+:\w*\]")   # no subqueries
                self.assertNotIn("histogram_quantile", e["query"])

    def test_as_of_defaults_to_source_time_and_is_fixed(self):
        out = collect()
        self.assertEqual(out["as_of"], "2026-10-01T00:00:00Z")

    def test_trace_records_every_call_and_errors(self):
        trace = Trace()
        collect_facts(Prom("http://prom", trace, stub_fetch), POLICY, as_of=AS_OF)
        self.assertGreater(len(trace.calls), 20)
        self.assertTrue(all(c["source"] == "prometheus" and c["ok"] for c in trace.calls))

        def boom(url):
            raise OSError("down")
        t2 = Trace()
        with self.assertRaises(OSError):
            collect_facts(Prom("http://prom", t2, boom), POLICY, as_of=AS_OF)
        self.assertFalse(t2.calls[-1]["ok"])

    def test_no_data_means_error_not_guess(self):
        empty = lambda url: {"status": "success", "data": {"resultType": "vector", "result": []}}
        with self.assertRaises(RuntimeError):
            collect_facts(Prom("http://prom", Trace(), empty), POLICY)


class EndToEndTest(unittest.TestCase):
    def test_collect_feeds_decide(self):
        out = collect(as_of=AS_OF)
        ds = {d["id"]: d for d in decide(out["facts"], out["cluster"], POLICY)}
        self.assertEqual(ds["K-shop-web-cpu"]["verdict"], "UNKNOWN")        # 5 days of data
        self.assertTrue(any("当前 5 天" in n for n in ds["K-shop-web-cpu"]["unknown_needs"]))
        self.assertEqual(ds["K-shop-api-cpu"]["recommendation"]["request"], 0.65)
        self.assertEqual(ds["K-shop-api-cpu"]["conditions"]["C"]["status"], "PASS")
        self.assertEqual(ds["K-shop-api-memory"]["recommendation"]["request"], 2.4375)
        for d in ds.values():
            ids = {e["id"] for e in d["evidence"]}
            for c in d["conditions"].values():
                self.assertTrue(set(c["evidence"]) <= ids)


class HttpTransportTest(unittest.TestCase):
    def test_default_urlopen_transport_against_local_http(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlparse(self.path)
                data = json.dumps(fake(u.path, parse_qs(u.query))).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            out = collect_facts(Prom(f"http://127.0.0.1:{srv.server_port}"), POLICY)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(len(out["facts"]), 4)
        self.assertEqual(out["as_of"], "2026-10-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
