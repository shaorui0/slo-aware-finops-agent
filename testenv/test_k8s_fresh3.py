"""c0042: the third pre-registered K8s world: self-consistency, catalog agreement, shape of the hard situations,
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
WORLD_PATH = HERE / "heldout" / "k8s_fresh3_world.yaml"
CATALOG_PATH = HERE / "heldout" / "k8s_fresh3_slos.yaml"
WORLD = yaml.safe_load(WORLD_PATH.read_text())
CATALOG = yaml.safe_load(CATALOG_PATH.read_text())
BASE = yaml.safe_load((HERE / "k8s_world.yaml").read_text())
VERDICTS = {"ALLOW", "REQUIRE_APPROVAL", "DENY", "UNKNOWN", "NOOP"}
# same digest as test_k8s_fresh2: the base world's series before any generator extension
BASE_SERIES_SHA = "c13c2fa6b443cac8e6d095e36c7f04b0c26ffc4108d191fda5ed316141a12822"


def ffd_nodes(world, key, override=None):
    al = world.raw["nodes"]["allocatable"]
    pods = []
    for x in world.workloads:
        c, m = world.request(x, "cpu"), world.request(x, "memory")
        if override and x["name"] == override[0]:
            c, m = (override[2], m) if override[1] == "cpu" else (c, override[2])
        if "sidecar" in x:
            c += kw.parse_qty(x["sidecar"]["requests"]["cpu"], "core")
            m += kw.parse_qty(x["sidecar"]["requests"]["memory"], "GiB")
        pods += [(c, m)] * x["replicas"]
    nodes = []
    for c, m in sorted(pods, key=key, reverse=True):
        for n in nodes:
            if n[0] + c <= al["cpu"] + 1e-9 and n[1] + m <= al["memory_gib"] + 1e-9:
                n[0] += c
                n[1] += m
                break
        else:
            nodes.append([c, m])
    return len(nodes)


class Fresh3WorldTest(unittest.TestCase):
    def test_workloads_are_new(self):
        names = [w["name"] for w in WORLD["workloads"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertFalse({w["name"] for w in BASE["workloads"]} & set(names))
        self.assertTrue(10 <= len(names) <= 14)

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

    def test_traps_cover_at_least_five_hard_workloads(self):
        names = {w["name"] for w in WORLD["workloads"]}
        trapped = {t["workload"] for t in WORLD["traps"]}
        self.assertTrue(trapped <= names)
        self.assertGreaterEqual(len(trapped), 5)

    def test_hard_situations_are_distinct_kinds(self):
        kinds = set()
        for w in WORLD["workloads"]:
            p = w["profile"]
            if p.get("per_pod_spread", 0.02) >= 0.3:
                kinds.add("pod_skew")
            if any(e["type"] == "load" for e in w.get("events", [])):
                kinds.add("recent_level_shift")
            if p.get("gaps"):
                kinds.add("mid_gap")
            if p.get("throttle", {}).get("onset", 1) < 0.3:
                kinds.add("hidden_throttle")
            if w.get("hpa") and w["replicas"] >= w["hpa"]["max"]:
                kinds.add("hpa_saturated")
            if p.get("slo_metrics") == "orphan":
                kinds.add("orphan_slo_metrics")
            if p["mem"]["kind"] == "spiky":
                kinds.add("mem_spike")
        self.assertGreaterEqual(len(kinds), 7, kinds)

    def test_node_count_is_first_fit_decreasing(self):
        """Decreasing by memory then cpu, as pods."""
        w = kw.load(WORLD_PATH)
        self.assertEqual(ffd_nodes(w, lambda p: (p[1], p[0])), WORLD["nodes"]["count"])

    def test_realizable_zero_holds_for_both_sort_orders(self):
        w = kw.load(WORLD_PATH)
        for x in WORLD["workloads"]:
            for res, e in x["expect"].items():
                if "realizable_usd_month" in e:
                    self.assertEqual(e["realizable_usd_month"], [0, 0])
                    tgt = e["request_range"][0]
                    mc = lambda p: (p[1], p[0])
                    self.assertEqual(ffd_nodes(w, mc, (x["name"], res, tgt)), ffd_nodes(w, mc))
                    cm = lambda p: (p[0], p[1])     # other sort key: never fewer nodes either
                    self.assertGreaterEqual(ffd_nodes(w, cm, (x["name"], res, tgt)), ffd_nodes(w, cm))
        base = ffd_nodes(w, lambda p: (p[1], p[0]))
        self.assertEqual(base, WORLD["nodes"]["count"])


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
    def p99(v):
        v = sorted(x for x in v if x is not None)
        return v[int(0.99 * (len(v) - 1))]

    def test_pods_are_uneven(self):
        p = [self.p99(v) for _, v in self.pick("container_cpu_usage_cores", "ledger-gateway")]
        self.assertEqual(len(p), 3)
        self.assertGreater(max(p), 2.5 * min(p))
        self.assertGreater(max(p), 1.35 * sum(p) / len(p))

    def test_level_shift_is_under_one_percent_of_samples(self):
        (_, v), = self.pick("container_cpu_usage_cores", "promo-renderer", pod="promo-renderer-6f7c9d-a")
        hi = [x for x in v if x > 0.4]
        self.assertEqual(len(hi), 36)
        self.assertLess(len(hi) / len(v), 0.01)
        self.assertLess(self.p99(v), 0.25)
        self.assertGreater(max(v), 0.55)

    def test_gap_leaves_twelve_valid_days(self):
        (_, v), = self.pick("container_cpu_usage_cores", "audit-trailer", pod="audit-trailer-6f7c9d-a")
        days = {int(t // 86400) for t, x in zip(self.world.grid, v) if x is not None}
        self.assertEqual(len(days), 12)
        self.assertEqual(len(v), 21 * 288)

    def test_short_history_is_thirteen_days(self):
        (_, v), = self.pick("container_cpu_usage_cores", "quote-cache", pod="quote-cache-6f7c9d-a")
        self.assertEqual(len({int(t // 86400) for t, x in zip(self.world.grid, v) if x is not None}), 13)

    def test_hidden_throttle_with_modest_utilization(self):
        (_, v), = self.pick("container_cpu_usage_cores", "thumbnail-farm", pod="thumbnail-farm-6f7c9d-b")
        self.assertLess(max(v), 0.55)
        (_, th), = self.pick("container_cpu_cfs_throttled_periods_total", "thumbnail-farm", pod="thumbnail-farm-6f7c9d-b")
        ratio = (th[-1] - th[0]) / (kw.PERIODS_PER_STEP * (len(th) - 1))
        self.assertGreater(ratio, 0.2)

    def test_hpa_is_at_max(self):
        (_, cur), = self.pick("kube_horizontalpodautoscaler_status_current_replicas", "fanout-worker")
        (_, mx), = self.pick("kube_horizontalpodautoscaler_spec_max_replicas", "fanout-worker")
        self.assertEqual(cur[-1], mx[-1])
        (_, v), = self.pick("container_cpu_usage_cores", "fanout-worker", pod="fanout-worker-6f7c9d-a")
        self.assertGreater(sum(v) / len(v), 0.8)

    def test_orphan_slo_metrics(self):
        for metric in ("slo_burn_rate_1h", "slo_error_budget_remaining_ratio"):
            have = {l["workload"] for l, k, v in self.series if l["__name__"] == metric}
            self.assertIn("loyalty-points", have)
        (_, burn), = self.pick("slo_burn_rate_1h", "loyalty-points")
        self.assertLess(max(burn), 1.0)
        in_catalog = {e["workload"] for e in CATALOG["slos"]}
        self.assertNotIn("loyalty-points", in_catalog)

    def test_memory_near_limit_without_oom(self):
        (_, m), = self.pick("container_memory_working_set_bytes", "geo-resolver", pod="geo-resolver-6f7c9d-a")
        self.assertGreater(max(m) / kw.GIB / 2.0, 0.96)
        for _, v in self.pick("kube_pod_container_status_restarts_total", "geo-resolver"):
            self.assertEqual(max(v), 0.0)

    def test_memory_spikes_hide_from_p99(self):
        for _, m in self.pick("container_memory_working_set_bytes", "pdf-assembler"):
            self.assertLess(self.p99(m) / kw.GIB, 1.2)
            self.assertGreater(max(m) / kw.GIB, 3.1)
            self.assertLess(sum(1 for x in m if x > 2 * kw.GIB) / len(m), 0.01)

    def test_batch_inside_deadline(self):
        (_, v), = self.pick("job_last_duration_seconds", "ledger-reconcile")
        self.assertLess(max(x for x in v if x is not None), 0.6 * 3600)


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

    def test_serves_fresh3_world(self):
        r = self.q('kube_deployment_spec_replicas{workload="ledger-gateway"}')
        self.assertEqual(float(r[0]["value"][1]), 3.0)
        self.assertEqual(len(self.q("kube_node_status_allocatable")), 2 * WORLD["nodes"]["count"])
        self.assertTrue(self.q('slo_burn_rate_1h{workload="loyalty-points"}'))
        self.assertEqual(self.q('slo_burn_rate_1h{workload="no-such-workload"}'), [])


class SubprocessServerTest(unittest.TestCase):
    def test_cli_serves_the_world_file(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen([sys.executable, "-m", "testenv.metrics_server", "--world", str(WORLD_PATH.relative_to(ROOT)), "--port", str(port)],
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        url = f"http://127.0.0.1:{port}/api/v1/query?{urlencode({'query': 'kube_deployment_spec_replicas{workload=\"fanout-worker\"}'})}"
        body = None
        for _ in range(50):
            try:
                with urlopen(url) as r:
                    body = json.load(r)
                break
            except OSError:
                time.sleep(0.2)
        self.assertIsNotNone(body)
        self.assertEqual(float(body["data"]["result"][0]["value"][1]), 4.0)


if __name__ == "__main__":
    unittest.main()
