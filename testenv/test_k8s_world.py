"""c0028: the synthetic K8s world and `test-metrics-server --world k8s`.

The packing check below is this file's own first-fit-decreasing; it does not import finops_agent.
"""
import json
import math
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from testenv import k8s_world as kw
from testenv import metrics_server

GIB = 1024 ** 3
SCEN = Path(kw.__file__).parent / "k8s_scenarios" / "drift_day3.yaml"
CONTRACT_METRICS = [
    "container_cpu_usage_seconds_total", "container_memory_working_set_bytes",
    "container_cpu_cfs_periods_total", "container_cpu_cfs_throttled_periods_total",
    "kube_pod_container_resource_requests", "kube_pod_container_resource_limits",
    "kube_pod_container_status_restarts_total", "kube_pod_container_status_last_terminated_reason",
    "kube_deployment_spec_replicas", "kube_horizontalpodautoscaler_spec_target_metric",
    "kube_horizontalpodautoscaler_status_current_replicas", "kube_horizontalpodautoscaler_spec_max_replicas",
    "kube_node_status_allocatable", "kube_pod_status_phase", "http_requests_total",
    "http_request_duration_p99_seconds", "slo_error_budget_remaining_ratio", "slo_burn_rate_1h",
    "job_last_duration_seconds",
]
REQUIRED = {"api-steady", "api-tidal", "batch-nightly", "mem-near-oom", "burning-slo", "new-service",
            "no-slo", "hpa-managed", "packing-trap"}
# cpu recommenders: p99 x1.3 ceil 0.05 core; memory: max ws x1.2 ceil 64Mi
ceil_to = lambda x, step: math.ceil(round(x / step, 9)) * step


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), metrics_server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class K8sServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        metrics_server.use_k8s(True)
        cls.addClassCleanup(metrics_server.use_k8s, False)
        cls.srv, cls.url = _serve()
        cls.addClassCleanup(cls.srv.shutdown)
        cls.world = kw.load()

    def get(self, path, **params):
        with urlopen(f"{self.url}{path}?{urlencode(params, doseq=True)}") as r:
            return json.load(r)

    def q(self, query, time=None):
        p = {"query": query}
        if time:
            p["time"] = time
        return self.get("/api/v1/query", **p)["data"]["result"]

    def per_workload(self, query_fn, name):
        return [float(r["value"][1]) for r in self.q(query_fn(name))]

    def rec_cpu(self, name):
        p99 = max(self.per_workload(lambda n: f'quantile_over_time(0.99, container_cpu_usage_cores{{workload="{n}"}}[14d])', name))
        return ceil_to(p99 * 1.3, 0.05)

    def rec_mem(self, name):
        mx = max(self.per_workload(lambda n: f'max_over_time(container_memory_working_set_bytes{{workload="{n}"}}[14d])', name)) / GIB
        return ceil_to(mx * 1.2, 64 / 1024)


class Schema(unittest.TestCase):
    def setUp(self):
        self.w = kw.load()

    def test_cluster_header(self):
        r = self.w.raw
        self.assertEqual((self.w.t1 - self.w.t0) / 86400, 21)
        self.assertEqual(r["cluster"]["step_seconds"], 300)
        self.assertEqual(r["pricing"], {"node_type": "m6i.xlarge", "node_hour_usd": 0.192,
                                        "cpu_core_hour_usd": 0.0316, "mem_gib_hour_usd": 0.0041})
        self.assertEqual(r["nodes"]["allocatable"], {"cpu": 3.8, "memory_gib": 14.5})
        self.assertTrue(r["nodes"]["min"] <= r["nodes"]["count"] <= r["nodes"]["max"])

    def test_required_workloads_and_expect(self):
        names = {w["name"] for w in self.w.workloads}
        self.assertTrue(REQUIRED <= names, REQUIRED - names)
        for w in self.w.workloads:
            for res in ("cpu", "memory"):
                e = w["expect"][res]
                self.assertIn(e["verdict"], {"ALLOW", "REQUIRE_APPROVAL", "DENY", "UNKNOWN", "NOOP"}, w["name"])
                lo, hi = e["request_range"]
                self.assertLessEqual(lo, hi, (w["name"], res))
                cur = self.w.request(w, res)
                # Only proposals must sit below current; a DENY may be under-provisioned (mem-near-oom/memory,
                # contract revision f307ec9: NOOP is checked after B's risk guards).
                if e["verdict"] in ("ALLOW", "REQUIRE_APPROVAL"):
                    self.assertLess(lo, cur, (w["name"], res))
            self.assertIn("kind", w)
            self.assertLessEqual(w["replicas"], w.get("hpa", {}).get("max", w["replicas"]))

    def test_traps_point_to_workloads(self):
        names = {w["name"] for w in self.w.workloads}
        self.assertTrue(self.w.raw["traps"])
        for t in self.w.raw["traps"]:
            self.assertIn(t["workload"], names)

    def test_no_slo_workload_has_no_slo_block(self):
        self.assertNotIn("slo", self.w.workload("no-slo"))
        self.assertIn("deadline_seconds", self.w.workload("batch-nightly")["slo"])
        self.assertEqual(self.w.workload("hpa-managed")["hpa"]["target_utilization"], 70)
        self.assertEqual(self.w.workload("new-service")["data_days"], 5)


class Metrics(K8sServerCase):
    def test_every_contract_metric_has_series(self):
        for m in CONTRACT_METRICS:
            data = self.get("/api/v1/series", **{"match[]": m})["data"]
            self.assertTrue(data, m)
        # metadata lists them all with help text
        meta = self.get("/api/v1/metadata")["data"]
        for m in CONTRACT_METRICS + ["container_cpu_usage_cores"]:
            self.assertTrue(meta[m][0]["help"], m)

    def test_contract_labels(self):
        for m in CONTRACT_METRICS:
            if m == "kube_node_status_allocatable":
                need = {"node", "resource"}
            elif m in ("kube_deployment_spec_replicas", "slo_error_budget_remaining_ratio", "slo_burn_rate_1h",
                       "job_last_duration_seconds") or m.startswith("kube_horizontalpodautoscaler"):
                need = {"namespace", "workload", "container"}
            elif m == "kube_pod_status_phase":
                need = {"namespace", "workload", "pod", "phase"}
            else:
                need = {"namespace", "workload", "pod", "container"}
            for s in self.get("/api/v1/series", **{"match[]": m})["data"]:
                self.assertTrue(need <= set(s), (m, s))
        res = {(s["resource"], s["unit"]) for s in self.get("/api/v1/series", **{"match[]": "kube_pod_container_resource_requests"})["data"]}
        self.assertEqual(res, {("cpu", "core"), ("memory", "byte")})
        self.assertTrue(self.get("/api/v1/series", **{"match[]": 'kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}'})["data"])
        self.assertTrue(self.get("/api/v1/series", **{"match[]": 'kube_pod_status_phase{phase="Pending"}'})["data"])
        self.assertEqual({s["code"] for s in self.get("/api/v1/series", **{"match[]": "http_requests_total"})["data"]}, {"200", "500"})

    def test_optional_metrics_only_where_defined(self):
        def workloads(m):
            return {s["workload"] for s in self.get("/api/v1/series", **{"match[]": m})["data"]}
        self.assertEqual(workloads("kube_horizontalpodautoscaler_spec_max_replicas"), {"hpa-managed"})
        self.assertNotIn("no-slo", workloads("slo_burn_rate_1h"))
        self.assertIn("burning-slo", workloads("slo_burn_rate_1h"))
        self.assertEqual(workloads("job_last_duration_seconds"), {"batch-nightly"})

    def test_nested_subquery_unsupported_so_gauge_exists(self):
        """The PromQL subset has no `expr[14d:5m]` subquery; container_cpu_usage_cores is the contract addition."""
        with self.assertRaises(HTTPError) as cm:
            self.q("quantile_over_time(0.99, rate(container_cpu_usage_seconds_total[10m])[14d:5m])")
        self.assertEqual(cm.exception.code, 400)
        self.assertTrue(self.q('quantile_over_time(0.99, container_cpu_usage_cores{workload="api-steady"}[14d])'))

    def test_gauge_equals_counter_rate(self):
        t = self.world.t1 - 3600
        g = self.q('container_cpu_usage_cores{workload="api-steady"}', time=str(t))
        r = self.q('rate(container_cpu_usage_seconds_total{workload="api-steady"}[10m])', time=str(t))
        gm = {x["metric"]["pod"]: float(x["value"][1]) for x in g}
        rm = {x["metric"]["pod"]: float(x["value"][1]) for x in r}
        self.assertEqual(gm.keys(), rm.keys())
        for p in gm:
            self.assertAlmostEqual(gm[p], rm[p], places=3)

    def test_new_service_has_five_days(self):
        res = self.get("/api/v1/query_range", query='container_cpu_usage_cores{workload="new-service"}',
                       start=self.world.t0, end=self.world.t1 - 300, step=86400)["data"]["result"]
        self.assertTrue(res)
        for s in res:
            self.assertEqual(len(s["values"]), 5, s["metric"])
        full = self.get("/api/v1/query_range", query='container_cpu_usage_cores{workload="api-steady"}',
                        start=self.world.t0, end=self.world.t1 - 300, step=86400)["data"]["result"]
        self.assertEqual(len(full[0]["values"]), 21)

    def test_default_world_metadata_not_polluted(self):
        metrics_server.use_k8s(False)
        try:
            self.assertNotIn("container_cpu_usage_cores", self.get("/api/v1/metadata")["data"])
            self.assertTrue(self.q("up"))
        finally:
            metrics_server.use_k8s(True)


class Shapes(K8sServerCase):
    def avg_usage(self, name):
        v = self.per_workload(lambda n: f'avg_over_time(container_cpu_usage_cores{{workload="{n}"}}[14d])', name)
        return sum(v) / len(v)

    def test_api_steady_flat_low(self):
        w = self.world.workload("api-steady")
        self.assertAlmostEqual(self.avg_usage("api-steady") / self.world.request(w, "cpu"), 0.15, delta=0.02)
        mx = max(self.per_workload(lambda n: f'max_over_time(container_cpu_usage_cores{{workload="{n}"}}[14d])', "api-steady"))
        self.assertLess(mx / self.world.request(w, "cpu"), 0.25)   # no peaks

    def test_api_tidal_peak_and_throttle(self):
        w = self.world.workload("api-tidal")
        req = self.world.request(w, "cpu")
        self.assertAlmostEqual(self.avg_usage("api-tidal") / req, 0.15, delta=0.02)
        mx = max(self.per_workload(lambda n: f'max_over_time(container_cpu_usage_cores{{workload="{n}"}}[14d])', "api-tidal"))
        self.assertAlmostEqual(mx / req, 0.9, delta=0.05)
        # throttling ratio = throttled / periods, in the last day: high at the 14:00 peak, ~0 at 03:00
        def ratio(hour):
            t = self.world.t1 - 86400 * 2 + hour * 3600
            a = {x["metric"]["pod"]: float(x["value"][1]) for x in self.q('increase(container_cpu_cfs_throttled_periods_total{workload="api-tidal"}[10m])', str(t))}
            b = {x["metric"]["pod"]: float(x["value"][1]) for x in self.q('increase(container_cpu_cfs_periods_total{workload="api-tidal"}[10m])', str(t))}
            return max(a[p] / b[p] for p in a)
        self.assertGreater(ratio(14), 0.2)
        self.assertLess(ratio(3), 0.01)
        # p99 latency correlates with throttling
        thr, lat = [], []
        for k in range(288):
            t = self.world.t1 - 86400 + k * 300
            a = self.q('increase(container_cpu_cfs_throttled_periods_total{pod=~"api-tidal.*"}[10m])', str(t))
            thr.append(sum(float(x["value"][1]) for x in a))
            lat.append(max(float(x["value"][1]) for x in self.q('http_request_duration_p99_seconds{workload="api-tidal"}', str(t))))
        mt, ml = sum(thr) / 288, sum(lat) / 288
        cov = sum((a - mt) * (b - ml) for a, b in zip(thr, lat))
        corr = cov / math.sqrt(sum((a - mt) ** 2 for a in thr) * sum((b - ml) ** 2 for b in lat))
        self.assertGreater(corr, 0.9)

    def test_batch_window_and_deadline(self):
        w = self.world.workload("batch-nightly")
        day = self.world.t1 - 86400 * 3
        at = lambda h: float(self.q('container_cpu_usage_cores{workload="batch-nightly"}', str(day + h * 3600))[0]["value"][1])
        self.assertLess(at(12), 0.05)
        self.assertGreater(at(3), 0.9)
        self.assertLess(at(5), 0.05)
        dl = w["slo"]["deadline_seconds"]
        mx = float(self.q('max_over_time(job_last_duration_seconds{workload="batch-nightly"}[14d])')[0]["value"][1])
        self.assertGreater(mx / dl, 0.9)
        self.assertLess(mx, dl)

    def test_mem_near_oom(self):
        w = self.world.workload("mem-near-oom")
        lim = self.world.limit(w, "memory")
        mx = max(self.per_workload(lambda n: f'max_over_time(container_memory_working_set_bytes{{workload="{n}"}}[21d])', "mem-near-oom")) / GIB
        self.assertGreaterEqual(mx / lim, 0.9)
        self.assertLess(max(self.per_workload(lambda n: f'max_over_time(container_cpu_usage_cores{{workload="{n}"}}[14d])', "mem-near-oom")), 0.1)
        res = self.q('increase(kube_pod_container_status_restarts_total{workload="mem-near-oom"}[21d])')
        self.assertEqual(sum(round(float(r["value"][1])) for r in res), 1)
        # the one OOMKilled is inside the 14d window, on one pod
        oom = self.q('max_over_time(kube_pod_container_status_last_terminated_reason{reason="OOMKilled",workload="mem-near-oom"}[14d])')
        self.assertEqual(sorted(float(r["value"][1]) for r in oom), [0.0, 1.0])

    def test_burning_slo(self):
        burn = float(self.q('slo_burn_rate_1h{workload="burning-slo"}')[0]["value"][1])
        budget = float(self.q('slo_error_budget_remaining_ratio{workload="burning-slo"}')[0]["value"][1])
        self.assertGreater(burn, 1)
        self.assertLess(budget, 0.25)
        for ok in ("api-steady", "api-tidal", "hpa-managed", "packing-trap", "mem-near-oom"):
            self.assertLessEqual(max(float(r["value"][1]) for r in self.q(f'max_over_time(slo_burn_rate_1h{{workload="{ok}"}}[1d])')), 1, ok)
            self.assertGreaterEqual(float(self.q(f'slo_error_budget_remaining_ratio{{workload="{ok}"}}')[0]["value"][1]), 0.25, ok)

    def test_request_ranges_derive_from_recommender_rule(self):
        for w in self.world.workloads:
            n = w["name"]
            for res, rec in (("cpu", self.rec_cpu(n)), ("memory", self.rec_mem(n))):
                lo, hi = w["expect"][res]["request_range"]
                self.assertTrue(lo - 1e-9 <= rec <= hi + 1e-9, f"{n}/{res}: recommender {rec} outside [{lo}, {hi}]")
                if hi > lo:
                    self.assertLessEqual(hi - lo, 0.25 * rec + 1e-9, f"{n}/{res}: range wider than ~+-10%")
                cur = self.world.request(w, res)
                if w["expect"][res]["verdict"] == "NOOP":
                    self.assertGreaterEqual(rec, cur, f"{n}/{res}")
                elif w["expect"][res]["verdict"] in ("ALLOW", "REQUIRE_APPROVAL"):
                    self.assertLess(rec, cur, f"{n}/{res}")


# ---- packing: own first-fit decreasing --------------------------------------------
ALLOC_CPU, ALLOC_MEM = 3.8, 14.5
KEYS = {"cpu": lambda p: (p[0], p[1]), "mem": lambda p: (p[1], p[0]),
        "norm": lambda p: max(p[0] / ALLOC_CPU, p[1] / ALLOC_MEM), "sum": lambda p: p[0] / ALLOC_CPU + p[1] / ALLOC_MEM}


def ffd(pods, key="cpu"):
    nodes = []
    for c, m in sorted(pods, key=KEYS[key], reverse=True):
        for n in nodes:
            if n[0] + c <= ALLOC_CPU + 1e-9 and n[1] + m <= ALLOC_MEM + 1e-9:
                n[0] += c
                n[1] += m
                break
        else:
            nodes.append([c, m])
    return len(nodes)


class Packing(K8sServerCase):
    def pods(self, override=None):
        override = override or {}
        out = []
        for w in self.world.workloads:
            c, m = override.get(w["name"], (self.world.request(w, "cpu"), self.world.request(w, "memory")))
            out += [(c, m)] * w["replicas"]
        return out

    def nodes_after(self, name, resource):
        w = self.world.workload(name)
        c, m = self.world.request(w, "cpu"), self.world.request(w, "memory")
        if resource == "cpu":
            c = min(c, self.rec_cpu(name))
        else:
            m = min(m, self.rec_mem(name))
        return {k: ffd(self.pods({name: (c, m)}), k) for k in KEYS}

    def test_baseline_node_count_matches_world(self):
        for k in KEYS:
            self.assertEqual(ffd(self.pods(), k), self.world.node_count, k)
        self.assertTrue(sum(self.world.request(w, "cpu") * w["replicas"] for w in self.world.workloads) <= self.world.node_count * ALLOC_CPU)

    def test_packing_trap_saves_no_node(self):
        w = self.world.workload("packing-trap")
        self.assertEqual(w["expect"]["cpu"]["verdict"], "ALLOW")
        self.assertEqual(w["expect"]["cpu"]["realizable_usd_month"], [0, 0])
        after = self.nodes_after("packing-trap", "cpu")
        self.assertEqual(set(after.values()), {self.world.node_count}, after)
        self.assertLess(self.rec_cpu("packing-trap"), self.world.request(w, "cpu"))   # the request really drops

    def test_api_steady_drops_a_node(self):
        w = self.world.workload("api-steady")
        after = self.nodes_after("api-steady", "cpu")
        for k, n in after.items():
            self.assertLessEqual(n, self.world.node_count - 1, k)
        dropped = self.world.node_count - after["cpu"]
        usd = dropped * self.world.pricing["node_hour_usd"] * 730
        lo, hi = w["expect"]["cpu"]["realizable_usd_month"]
        self.assertTrue(lo <= usd <= hi, (usd, lo, hi))

    def test_allow_changes_keep_rollback_headroom(self):
        """D-style: after applying one change, free allocatable still covers the freed request x replicas."""
        for name, res in (("api-steady", "cpu"), ("packing-trap", "cpu"), ("mem-near-oom", "cpu"),
                          ("no-slo", "cpu"), ("hpa-managed", "cpu"), ("api-steady", "memory"), ("hpa-managed", "memory")):
            w = self.world.workload(name)
            n_after = self.nodes_after(name, res)["cpu"]
            idx = 0 if res == "cpu" else 1
            cur = self.world.request(w, res)
            rec = self.rec_cpu(name) if res == "cpu" else self.rec_mem(name)
            total_after = sum(p[idx] for p in self.pods({name: ((rec, self.world.request(w, "memory")) if res == "cpu" else (self.world.request(w, "cpu"), rec))}))
            free = n_after * (ALLOC_CPU, ALLOC_MEM)[idx] - total_after
            self.assertGreaterEqual(free, (cur - rec) * w["replicas"] - 1e-9, f"{name}/{res}")


class Drift(unittest.TestCase):
    def tearDown(self):
        metrics_server.use_k8s(False)

    def test_as_of_truncates_without_changing_history(self):
        full = kw.load()
        cut = kw.load(as_of="2026-10-20T00:00:00Z")
        _, a = kw.build_series(full)
        g, b = kw.build_series(cut)
        self.assertEqual(len(g), 15 * 288)
        sa = {json.dumps(l, sort_keys=True): v for l, _, v in a}
        for l, _, v in b:
            if l.get("workload") == "new-service":      # data_days counts back from the (truncated) end
                continue
            self.assertEqual(sa[json.dumps(l, sort_keys=True)][:len(v)], v)

    def test_scenario_extends_period_and_drifts_api_steady(self):
        base = kw.load()
        day3 = kw.load(SCEN)
        self.assertEqual(day3.t1 - base.t1, 2 * 86400)
        # day-1 view of the scenario equals the base world
        self.assertEqual(kw.build_series(kw.load(SCEN, as_of=base.t1)), kw.build_series(base))
        metrics_server.use_k8s(True, str(SCEN))
        srv, url = _serve()
        self.addCleanup(srv.shutdown)
        def q(expr, t):
            with urlopen(f"{url}/api/v1/query?{urlencode({'query': expr, 'time': t})}") as r:
                return json.load(r)["data"]["result"]
        for name, ratio in (("api-steady", 1.6), ("api-tidal", 1.0)):
            e = f'avg_over_time(container_cpu_usage_cores{{workload="{name}"}}[1d])'
            before = float(q(e, base.t1 - 1)[0]["value"][1])      # last day of day-1 world
            after = float(q(e, day3.t1 - 1)[0]["value"][1])       # last day of the day-3 world
            self.assertAlmostEqual(after / before, ratio, delta=0.05, msg=name)
        # the 14d p99 recommendation moves by more than the 5% ledger tolerance
        p = lambda t: max(float(r["value"][1]) for r in q('quantile_over_time(0.99, container_cpu_usage_cores{workload="api-steady"}[14d])', t))
        self.assertGreater(p(day3.t1 - 1) / p(base.t1 - 1), 1.05)


if __name__ == "__main__":
    unittest.main()
