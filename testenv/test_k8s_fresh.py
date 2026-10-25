"""c0035: the pre-registered fresh K8s world: self-consistency, catalog agreement, the new profile kinds, and that
the generator extension leaves the base world's series untouched. Does not import finops_agent."""
import hashlib
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import yaml

from testenv import k8s_world as kw
from testenv import metrics_server

HERE = Path(__file__).parent
WORLD_PATH = HERE / "heldout" / "k8s_fresh_world.yaml"
CATALOG_PATH = HERE / "heldout" / "k8s_fresh_slos.yaml"
WORLD = yaml.safe_load(WORLD_PATH.read_text())
CATALOG = yaml.safe_load(CATALOG_PATH.read_text())
BASE = yaml.safe_load((HERE / "k8s_world.yaml").read_text())
VERDICTS = {"ALLOW", "REQUIRE_APPROVAL", "DENY", "UNKNOWN", "NOOP"}
# sha256 of the base world's series, recorded before the c0035 generator extension
BASE_SERIES_SHA = "c13c2fa6b443cac8e6d095e36c7f04b0c26ffc4108d191fda5ed316141a12822"


class FreshWorldTest(unittest.TestCase):
    def test_workloads_are_new(self):
        base = {w["name"] for w in BASE["workloads"]}
        names = [w["name"] for w in WORLD["workloads"]]
        self.assertFalse(base & set(names))
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(8 <= len(names) <= 10)

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
                if "realizable_usd_month" in e:
                    a, b = e["realizable_usd_month"]
                    self.assertLessEqual(a, b)

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

    def test_at_least_three_new_situations(self):
        kinds = set()
        for w in WORLD["workloads"]:
            p = w["profile"]
            if p["cpu"]["kind"] in ("weekly", "spiky"):
                kinds.add(p["cpu"]["kind"])
            if p["mem"]["kind"] == "creep":
                kinds.add("creep")
            if "sidecar" in w:
                kinds.add("sidecar")
            if p.get("gaps"):
                kinds.add("gap")
            if w.get("slo") and p.get("slo_metrics") is False:
                kinds.add("slo_metrics_missing")
        self.assertGreaterEqual(len(kinds), 3)

    def test_traps_point_at_workloads(self):
        names = {w["name"] for w in WORLD["workloads"]}
        for t in WORLD["traps"]:
            self.assertIn(t["workload"], names)


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

    def test_weekly_peak_only_on_wednesdays(self):
        (_, v), = self.pick("container_cpu_usage_cores", "rate-engine", pod="rate-engine-6f7c9d-a")
        hi = [t for t, x in zip(self.world.grid, v) if x and x > 1.0]
        self.assertEqual(len(hi), 3 * 6)
        self.assertEqual({int(t // 86400 + 3) % 7 for t in hi}, {2})

    def test_spiky_has_flat_p99_and_high_tail(self):
        (_, v), = self.pick("container_cpu_usage_cores", "route-quote", pod="route-quote-6f7c9d-a")
        s = sorted(v)
        self.assertLess(s[int(len(s) * 0.99)], 0.3)
        self.assertGreater(s[-1], 1.0)

    def test_creep_is_monotone_in_trend(self):
        (_, v), = self.pick("container_memory_working_set_bytes", "session-store", pod="session-store-6f7c9d-a")
        n = len(v) // 7
        means = [sum(v[i * n:(i + 1) * n]) / n for i in range(7)]
        self.assertEqual(means, sorted(means))
        self.assertGreater(means[-1], 2 * means[0])

    def test_gap_in_the_middle(self):
        (_, v), = self.pick("container_cpu_usage_cores", "catalog-sync", pod="catalog-sync-6f7c9d-a")
        none = [t for t, x in zip(self.world.grid, v) if x is None]
        self.assertEqual(len(none), 8 * 288)
        self.assertTrue(v[0] is not None and v[-1] is not None)

    def test_slo_metrics_absent_for_fraud_score_only(self):
        for metric in ("slo_burn_rate_1h", "slo_error_budget_remaining_ratio"):
            have = {l["workload"] for l, k, v in self.series if l["__name__"] == metric}
            self.assertNotIn("fraud-score", have)
            self.assertIn("ledger-api", have)

    def test_sidecar_shares_the_workload_label(self):
        side = self.pick("container_memory_working_set_bytes", "edge-gateway", container="mesh-proxy")
        main = self.pick("container_memory_working_set_bytes", "edge-gateway", container="gateway")
        self.assertEqual(len(side), 3)
        self.assertEqual(len(main), 3)
        self.assertGreater(max(max(v) for _, v in side), 2 * max(max(v) for _, v in main))

    def test_batch_runs_at_21_utc(self):
        (_, v), = self.pick("container_cpu_usage_cores", "eod-reconcile")
        hours = {int(t % 86400 // 3600) for t, x in zip(self.world.grid, v) if x > 1.0}
        self.assertEqual(hours, {21, 22})


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

    def test_serves_fresh_world(self):
        r = self.q('kube_deployment_spec_replicas{workload="ledger-api"}')
        self.assertEqual(float(r[0]["value"][1]), 6.0)
        self.assertEqual(len(self.q("kube_node_status_allocatable")), 14)
        self.assertEqual(self.q('slo_burn_rate_1h{workload="fraud-score"}'), [])
        self.assertTrue(self.q('slo_burn_rate_1h{workload="ledger-api"}'))


if __name__ == "__main__":
    unittest.main()
