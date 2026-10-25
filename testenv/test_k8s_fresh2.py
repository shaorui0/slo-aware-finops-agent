"""c0036: the second pre-registered K8s world: self-consistency, catalog agreement, shape of the hard situations,
node count, that the metrics server serves it, and that the base world is untouched. Does not import finops_agent."""
import hashlib
import json
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import yaml

from testenv import k8s_world as kw
from testenv import metrics_server

HERE = Path(__file__).parent
ROOT = HERE.parent
WORLD_PATH = HERE / "heldout" / "k8s_fresh2_world.yaml"
CATALOG_PATH = HERE / "heldout" / "k8s_fresh2_slos.yaml"
WORLD = yaml.safe_load(WORLD_PATH.read_text())
CATALOG = yaml.safe_load(CATALOG_PATH.read_text())
BASE = yaml.safe_load((HERE / "k8s_world.yaml").read_text())
VERDICTS = {"ALLOW", "REQUIRE_APPROVAL", "DENY", "UNKNOWN", "NOOP"}
# same digest as test_k8s_fresh: the base world's series before any generator extension
BASE_SERIES_SHA = "c13c2fa6b443cac8e6d095e36c7f04b0c26ffc4108d191fda5ed316141a12822"


class Fresh2WorldTest(unittest.TestCase):
    def test_workloads_are_new(self):
        names = [w["name"] for w in WORLD["workloads"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertFalse({w["name"] for w in BASE["workloads"]} & set(names))
        self.assertTrue(8 <= len(names) <= 12)

    def test_pricing_and_nodes_differ_from_base(self):
        self.assertNotEqual(WORLD["pricing"]["node_type"], BASE["pricing"]["node_type"])
        self.assertNotEqual(WORLD["pricing"]["node_hour_usd"], BASE["pricing"]["node_hour_usd"])
        self.assertNotEqual(WORLD["nodes"]["allocatable"], BASE["nodes"]["allocatable"])
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
                cur = kw.parse_qty(w["container"]["requests"][res], "core" if res == "cpu" else "GiB")
                if e["verdict"] == "NOOP" or e.get("range_note"):
                    self.assertGreaterEqual(hi, cur * 0.95, f"{w['name']}/{res}")
                else:
                    self.assertLess(lo, cur, f"{w['name']}/{res}")
                if e.get("judgment_conflict"):
                    self.assertIn("judgment", e["why"].lower(), f"{w['name']}/{res}")

    def test_judgment_conflicts_are_explained(self):
        flagged = [(w["name"], r) for w in WORLD["workloads"] for r, e in w["expect"].items() if e.get("judgment_conflict")]
        self.assertGreaterEqual(len(flagged), 2)

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

    def test_hard_situations_present(self):
        hard = set()
        for w in WORLD["workloads"]:
            p = w["profile"]
            if p["cpu"]["kind"] == "weekly":
                hard.add("weekly_peak_below_p99")
            if p["cpu"]["kind"] == "trend":
                hard.add("cpu_trend")
            if p["mem"]["kind"] == "creep":
                hard.add("mem_trend")
            if "sidecar" in w:
                hard.add("sidecar")
            if p.get("gaps"):
                hard.add("gap")
            if w.get("hpa", {}).get("metric") == "memory":
                hard.add("hpa_on_memory")
            if w.get("slo") and p.get("slo_metrics") is False:
                hard.add("slo_metrics_missing")
            if not w.get("slo"):
                hard.add("no_slo")
        self.assertGreaterEqual(len(hard), 6, hard)

    def test_traps_point_at_workloads(self):
        names = {w["name"] for w in WORLD["workloads"]}
        for t in WORLD["traps"]:
            self.assertIn(t["workload"], names)

    def test_node_count_is_first_fit_decreasing(self):
        """Decreasing by memory then cpu, as pods (sidecar requests included)."""
        w = kw.load(WORLD_PATH)
        al = WORLD["nodes"]["allocatable"]
        pods = []
        for x in w.workloads:
            c, m = w.request(x, "cpu"), w.request(x, "memory")
            if "sidecar" in x:
                c += kw.parse_qty(x["sidecar"]["requests"]["cpu"], "core")
                m += kw.parse_qty(x["sidecar"]["requests"]["memory"], "GiB")
            pods += [(c, m)] * x["replicas"]
        nodes = []
        for c, m in sorted(pods, key=lambda p: (p[1], p[0]), reverse=True):
            for n in nodes:
                if n[0] + c <= al["cpu"] + 1e-9 and n[1] + m <= al["memory_gib"] + 1e-9:
                    n[0] += c
                    n[1] += m
                    break
            else:
                nodes.append([c, m])
        self.assertEqual(len(nodes), WORLD["nodes"]["count"])


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

    def test_trend_rises_roughly_linearly(self):
        (_, v), = self.pick("container_cpu_usage_cores", "search-indexer", pod="search-indexer-6f7c9d-a")
        n = len(v) // 4
        means = [sum(v[i * n:(i + 1) * n]) / n for i in range(4)]
        self.assertEqual(means, sorted(means))
        self.assertGreater(means[-1], 2.5 * means[0])

    def test_shrinking_memory_trend(self):
        (_, v), = self.pick("container_memory_working_set_bytes", "session-cache", pod="session-cache-6f7c9d-a")
        n = len(v) // 4
        means = [sum(v[i * n:(i + 1) * n]) / n for i in range(4)]
        self.assertEqual(means, sorted(means, reverse=True))
        self.assertLess(means[-1], 0.4 * means[0])

    def test_weekly_peak_is_under_one_percent_of_samples_and_throttles(self):
        (_, v), = self.pick("container_cpu_usage_cores", "report-builder", pod="report-builder-6f7c9d-a")
        hi = [t for t, x in zip(self.world.grid, v) if x > 1.0]
        self.assertEqual(len(hi), 4 * 12)
        self.assertLess(len(hi) / len(v), 0.01)
        self.assertEqual({int(t // 86400 + 3) % 7 for t in hi}, {0})
        (_, th), = self.pick("container_cpu_cfs_throttled_periods_total", "report-builder", pod="report-builder-6f7c9d-a")
        peak = max(th[i + 1] - th[i] for i in range(len(th) - 1)) / kw.PERIODS_PER_STEP
        self.assertGreater(peak, 0.2)

    def test_tail_gap_leaves_a_stale_week(self):
        (_, v), = self.pick("container_cpu_usage_cores", "edge-collector", pod="edge-collector-6f7c9d-a")
        none = [x is None for x in v]
        self.assertEqual(sum(none), 7 * 288)
        self.assertTrue(all(none[-7 * 288:]))
        self.assertFalse(any(none[:-7 * 288]))

    def test_sidecar_is_a_separate_container(self):
        side = self.pick("container_cpu_usage_cores", "ingest-worker", container="log-shipper")
        main = self.pick("container_cpu_usage_cores", "ingest-worker", container="worker")
        self.assertEqual((len(side), len(main)), (3, 3))
        self.assertGreater(min(sum(v) / len(v) for _, v in side), 2 * max(sum(v) / len(v) for _, v in main))

    def test_slo_metric_presence(self):
        for metric in ("slo_burn_rate_1h", "slo_error_budget_remaining_ratio"):
            have = {l["workload"] for l, k, v in self.series if l["__name__"] == metric}
            self.assertNotIn("invoice-api", have)
            self.assertNotIn("admin-console", have)
            self.assertIn("notify-svc", have)

    def test_low_budget_with_burn_below_one(self):
        (_, burn), = self.pick("slo_burn_rate_1h", "notify-svc")
        (_, bud), = self.pick("slo_error_budget_remaining_ratio", "notify-svc")
        self.assertLess(max(burn[-12:]), 1.0)
        self.assertLess(bud[-1], 0.15)

    def test_hpa_targets_memory(self):
        self.assertTrue(self.pick("kube_horizontalpodautoscaler_spec_target_metric", "queue-consumer", metric_name="memory"))
        self.assertEqual(self.pick("kube_horizontalpodautoscaler_spec_target_metric", "checkout-web"), [])

    def test_batch_inside_deadline(self):
        (_, v), = self.pick("job_last_duration_seconds", "nightly-etl")
        self.assertLess(max(x for x in v if x is not None), 0.6 * 7200)


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

    def test_serves_fresh2_world(self):
        r = self.q('kube_deployment_spec_replicas{workload="checkout-web"}')
        self.assertEqual(float(r[0]["value"][1]), 4.0)
        self.assertEqual(len(self.q("kube_node_status_allocatable")), 2 * WORLD["nodes"]["count"])
        self.assertEqual(self.q('slo_burn_rate_1h{workload="invoice-api"}'), [])
        self.assertTrue(self.q('slo_burn_rate_1h{workload="notify-svc"}'))


class SubprocessServerTest(unittest.TestCase):
    def test_cli_serves_the_world_file(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen([sys.executable, "-m", "testenv.metrics_server", "--world", str(WORLD_PATH.relative_to(ROOT)), "--port", str(port)],
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        url = f"http://127.0.0.1:{port}/api/v1/query?{urlencode({'query': 'kube_deployment_spec_replicas{workload=\"notify-svc\"}'})}"
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
