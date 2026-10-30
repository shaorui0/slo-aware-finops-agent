"""c0045: the fourth pre-registered K8s world: self-consistency, catalog agreement, shape of the hard situations,
node count and realizable savings (first-fit decreasing), that the metrics server serves it, and that the base world is untouched.
Does not import finops_agent."""
import hashlib
import json
import socket
import subprocess
import sys
import threading
import time
import unittest
from collections import Counter
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import yaml

from testenv import k8s_world as kw
from testenv import metrics_server

HERE = Path(__file__).parent
ROOT = HERE.parent
WORLD_PATH = HERE / "heldout" / "k8s_fresh4_world.yaml"
CATALOG_PATH = HERE / "heldout" / "k8s_fresh4_slos.yaml"
WORLD = yaml.safe_load(WORLD_PATH.read_text())
CATALOG = yaml.safe_load(CATALOG_PATH.read_text())
BASE = yaml.safe_load((HERE / "k8s_world.yaml").read_text())
VERDICTS = {"ALLOW", "REQUIRE_APPROVAL", "DENY", "UNKNOWN", "NOOP"}
BASE_SERIES_SHA = "c13c2fa6b443cac8e6d095e36c7f04b0c26ffc4108d191fda5ed316141a12822"   # same digest as test_k8s_fresh2
AL = WORLD["nodes"]["allocatable"]
NODE_MONTH = WORLD["pricing"]["node_hour_usd"] * 730


def _res(w, res):
    return kw.parse_qty(w["container"]["requests"][res], "core" if res == "cpu" else "GiB")


def _ffd(pods, key):
    nodes = []
    for c, m in sorted(pods, key=key, reverse=True):
        for n in nodes:
            if n[0] + c <= AL["cpu"] + 1e-9 and n[1] + m <= AL["memory_gib"] + 1e-9:
                n[0] += c
                n[1] += m
                break
        else:
            nodes.append([c, m])
    return len(nodes)


def _pods(over=None):
    pods = []
    for x in WORLD["workloads"]:
        c, m = _res(x, "cpu"), _res(x, "memory")
        if over and over[0] == x["name"]:
            if over[1] == "cpu":
                c = over[2]
            else:
                m = over[2]
        pods += [(c, m)] * x["replicas"]
    return pods


KEYS = [lambda p: (p[1], p[0]), lambda p: (p[0], p[1]), lambda p: (p[1],), lambda p: (p[0],),
        lambda p: (p[0] / AL["cpu"] + p[1] / AL["memory_gib"],), lambda p: (max(p[0] / AL["cpu"], p[1] / AL["memory_gib"]),)]


class Fresh4WorldTest(unittest.TestCase):
    def test_workloads_are_new(self):
        names = [w["name"] for w in WORLD["workloads"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertFalse({w["name"] for w in BASE["workloads"]} & set(names))
        self.assertTrue(10 <= len(names) <= 14)

    def test_pricing_and_nodes_differ_from_base(self):
        self.assertNotEqual(WORLD["pricing"]["node_type"], BASE["pricing"]["node_type"])
        self.assertNotEqual(WORLD["pricing"]["node_hour_usd"], BASE["pricing"]["node_hour_usd"])
        self.assertNotEqual(AL, BASE["nodes"]["allocatable"])
        n = WORLD["nodes"]
        self.assertLessEqual(n["min"], n["count"])
        self.assertLessEqual(n["count"], n["max"])

    def test_expect_is_complete_and_consistent(self):
        for w in WORLD["workloads"]:
            self.assertEqual(set(w["expect"]), {"cpu", "memory"}, w["name"])
            for res, e in w["expect"].items():
                self.assertIn(e["verdict"], VERDICTS)
                self.assertTrue(e["why"].strip())
                lo, hi = e["request_range"]
                self.assertLessEqual(lo, hi)
                cur = _res(w, res)
                if e["verdict"] == "NOOP" or e.get("range_note"):
                    self.assertGreaterEqual(hi, cur * 0.95, f"{w['name']}/{res}")
                else:
                    self.assertLess(lo, cur, f"{w['name']}/{res}")
                if e.get("judgment_conflict"):
                    self.assertIn("judgment", e["why"].lower(), f"{w['name']}/{res}")

    def test_judgment_conflict_is_flagged(self):
        flagged = [(w["name"], r) for w in WORLD["workloads"] for r, e in w["expect"].items() if e.get("judgment_conflict")]
        self.assertGreaterEqual(len(flagged), 1)

    def test_catalog_matches_world_slo_blocks(self):
        want = {}
        for w in WORLD["workloads"]:
            s = w.get("slo")
            if s:
                want[(w["namespace"], w["name"])] = {k: s[k] for k in ("availability", "p99_seconds", "deadline_seconds") if k in s}
        got = {}
        for e in CATALOG["slos"]:
            d = dict(e.get("objectives") or {})
            if "deadline_seconds" in e:
                d["deadline_seconds"] = e["deadline_seconds"]
            key = (e["namespace"], e["workload"])
            self.assertNotIn(key, got)
            got[key] = d
        self.assertEqual(got, want)
        self.assertEqual(CATALOG["kind"], "SLOCatalog")

    def test_hard_workloads_span_categories(self):
        cats = Counter(w["hard_category"] for w in WORLD["workloads"] if w.get("hard_category"))
        self.assertGreaterEqual(sum(cats.values()), 6)
        self.assertLessEqual(max(cats.values()), 2, cats)
        self.assertGreaterEqual(len(cats), 6, cats)

    def test_traps_point_at_workloads(self):
        names = {w["name"] for w in WORLD["workloads"]}
        for t in WORLD["traps"]:
            self.assertIn(t["workload"], names)

    def test_node_count_is_first_fit_decreasing(self):
        self.assertEqual(_ffd(_pods(), KEYS[0]), WORLD["nodes"]["count"])

    def test_node_count_is_stable_across_sort_keys(self):
        self.assertEqual({_ffd(_pods(), k) for k in KEYS}, {WORLD["nodes"]["count"]})

    def test_realizable_ranges_match_single_change_packing(self):
        """Applied alone, at the low end, the high end and the midpoint of request_range, under every sort key tried."""
        checked = 0
        for w in WORLD["workloads"]:
            for res, e in w["expect"].items():
                r = e.get("realizable_usd_month")
                if r is None:
                    continue
                self.assertIn(e["verdict"], {"ALLOW", "REQUIRE_APPROVAL"})
                lo, hi = e["request_range"]
                for v in (lo, (lo + hi) / 2, hi):
                    for k in KEYS:
                        freed = WORLD["nodes"]["count"] - _ffd(_pods((w["name"], res, v)), k)
                        self.assertTrue(r[0] <= freed * NODE_MONTH <= r[1], f"{w['name']}/{res}@{v}: freed {freed}")
                checked += 1
        self.assertGreaterEqual(checked, 12)


class GeneratorExtensionTest(unittest.TestCase):
    def test_base_world_series_unchanged(self):
        _, s = kw.build_series(kw.load())
        sha = hashlib.sha256(json.dumps([[l, k, v] for l, k, v in s], sort_keys=True).encode()).hexdigest()
        self.assertEqual(sha, BASE_SERIES_SHA)

    def setUp(self):
        self.world = kw.load(WORLD_PATH)
        _, self.series = kw.build_series(self.world)

    def pick(self, metric, workload, **labels):
        return [(l, v) for l, k, v in self.series
                if l["__name__"] == metric and l.get("workload") == workload and all(l.get(a) == b for a, b in labels.items())]

    @staticmethod
    def p99(vals):
        v = sorted(x for x in vals if x is not None)
        return v[int(0.99 * (len(v) - 1))]

    def test_daily_burst_is_below_p99_and_real(self):
        (_, v), = self.pick("container_cpu_usage_cores", "thumbnail-svc", pod="thumbnail-svc-6f7c9d-a")
        hi = [x for x in v if x > 0.5]
        self.assertEqual(len(hi), 2 * 21)
        self.assertLess(len(hi) / len(v), 0.01)
        self.assertLess(self.p99(v), 0.2)
        (_, th), = self.pick("container_cpu_cfs_throttled_periods_total", "thumbnail-svc", pod="thumbnail-svc-6f7c9d-a")
        self.assertLess(th[-1] / kw.PERIODS_PER_STEP / len(v), 0.005)

    def test_pods_are_skewed(self):
        p = [self.p99(v) for _, v in self.pick("container_cpu_usage_cores", "fraud-scorer")]
        self.assertEqual(len(p), 4)
        self.assertGreater(max(p), 6 * min(p))
        self.assertGreater(max(p), 1.6 * sum(p) / len(p))

    def test_mid_window_hole_leaves_eleven_days_and_fresh_tail(self):
        (_, v), = self.pick("container_cpu_usage_cores", "ledger-sync", pod="ledger-sync-6f7c9d-a")
        none = [x is None for x in v]
        self.assertEqual(sum(none), 10 * 288)
        self.assertFalse(any(none[-6 * 288:]))
        self.assertFalse(any(none[:5 * 288]))

    def test_tail_gap_is_five_days(self):
        (_, v), = self.pick("container_cpu_usage_cores", "cart-archiver", pod="cart-archiver-6f7c9d-a")
        none = [x is None for x in v]
        self.assertEqual(sum(none), 5 * 288)
        self.assertTrue(all(none[-5 * 288:]))
        self.assertFalse(any(none[:-5 * 288]))
        self.assertEqual(self.pick("kube_deployment_spec_replicas", "cart-archiver")[0][1][-1], None)

    def test_batch_worst_run_near_deadline_mean_and_last_fine(self):
        (_, v), = self.pick("job_last_duration_seconds", "reconcile-nightly")
        v = [x for x in v if x is not None]
        self.assertGreater(max(v), 0.97 * 7200)
        self.assertLess(sum(v) / len(v), 0.75 * 7200)
        self.assertLess(v[-1], 0.75 * 7200)
        self.assertGreater((len(v) - 1 - v.index(max(v))) / 288, 8)      # the worst run is >8 days before the end: inside a 14-day window

    def test_burn_above_one_with_healthy_budget(self):
        (_, burn), = self.pick("slo_burn_rate_1h", "settle-api")
        (_, bud), = self.pick("slo_error_budget_remaining_ratio", "settle-api")
        self.assertGreater(burn[-1], 1.5)
        self.assertGreater(min(bud), 0.5)

    def test_hpa_is_at_max_and_throttling_at_peak(self):
        (_, cur), = self.pick("kube_horizontalpodautoscaler_status_current_replicas", "edge-gateway")
        (_, mx), = self.pick("kube_horizontalpodautoscaler_spec_max_replicas", "edge-gateway")
        self.assertEqual(cur[-1], mx[-1])
        (_, th), = self.pick("container_cpu_cfs_throttled_periods_total", "edge-gateway", pod="edge-gateway-6f7c9d-a")
        peak = max(th[i + 1] - th[i] for i in range(len(th) - 1)) / kw.PERIODS_PER_STEP
        self.assertGreater(peak, 0.3)
        self.assertEqual(self.pick("kube_horizontalpodautoscaler_spec_target_metric", "edge-gateway")[0][0]["metric_name"], "cpu")

    def test_recent_step_is_under_two_percent_of_window_and_throttles(self):
        (_, v), = self.pick("container_cpu_usage_cores", "search-suggest", pod="search-suggest-6f7c9d-a")
        n = 12 * 12
        self.assertLess(max(v[:-n]), 0.5)
        self.assertGreater(min(v[-n:]), 1.0)
        (_, th), = self.pick("container_cpu_cfs_throttled_periods_total", "search-suggest", pod="search-suggest-6f7c9d-a")
        d = [(th[i + 1] - th[i]) / kw.PERIODS_PER_STEP for i in range(len(th) - 1)]
        self.assertLess(sum(d) / len(d), 0.02)
        self.assertGreater(max(d), 0.2)

    def test_under_requested_usage_exceeds_request(self):
        (_, v), = self.pick("container_cpu_usage_cores", "recommend-svc", pod="recommend-svc-6f7c9d-a")
        self.assertGreater(min(v), 3 * 0.5 * 0.9)
        (_, m), = self.pick("container_memory_working_set_bytes", "recommend-svc", pod="recommend-svc-6f7c9d-a")
        self.assertGreater(min(m), 2 * kw.GIB)

    def test_slo_metrics_only_for_catalog_workloads(self):
        have = {l["workload"] for l, k, v in self.series if l["__name__"] == "slo_burn_rate_1h"}
        self.assertNotIn("audit-trail", have)
        self.assertTrue(have <= {e["workload"] for e in CATALOG["slos"]})


class ServedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        metrics_server.use_k8s(True, str(WORLD_PATH))
        cls.addClassCleanup(metrics_server.use_k8s, False)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), metrics_server.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.srv.shutdown)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    def q(self, query):
        with urlopen(f"{self.url}/api/v1/query?{urlencode({'query': query})}") as r:
            return json.load(r)["data"]["result"]

    def test_serves_fresh4_world(self):
        r = self.q('kube_deployment_spec_replicas{workload="fraud-scorer"}')
        self.assertEqual(float(r[0]["value"][1]), 4.0)
        self.assertEqual(len(self.q("kube_node_status_allocatable")), 2 * WORLD["nodes"]["count"])
        self.assertEqual(self.q('slo_burn_rate_1h{workload="audit-trail"}'), [])
        self.assertTrue(self.q('slo_burn_rate_1h{workload="settle-api"}'))
        self.assertEqual(self.q('kube_deployment_spec_replicas{workload="cart-archiver"}'), [])   # stale: no instant sample


class SubprocessServerTest(unittest.TestCase):
    def test_cli_serves_the_world_file(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen([sys.executable, "-m", "testenv.metrics_server", "--world", str(WORLD_PATH.relative_to(ROOT)), "--port", str(port)],
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        url = f"http://127.0.0.1:{port}/api/v1/query?{urlencode({'query': 'kube_deployment_spec_replicas{workload=\"ranking-api\"}'})}"
        body = None
        for _ in range(50):
            try:
                with urlopen(url) as r:
                    body = json.load(r)
                break
            except OSError:
                time.sleep(0.2)
        self.assertIsNotNone(body)
        self.assertEqual(float(body["data"]["result"][0]["value"][1]), 3.0)


if __name__ == "__main__":
    unittest.main()
