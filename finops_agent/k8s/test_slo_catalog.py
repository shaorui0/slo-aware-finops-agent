"""SLO catalog as the third input (c0031): objectives and deadlines from the catalog, burn and budget from metrics."""
import unittest

from finops_agent.k8s.policy import decide
from finops_agent.k8s.test_collect import AS_OF, POLICY, collect, pick


def run(slos, **kw):
    out = collect(as_of=AS_OF, slos=slos, **kw)
    return out, {(d["workload"], d["resource"]): d for d in decide(out["facts"], out["cluster"], POLICY)}


API = {"objectives": {"availability": 0.999, "p99_seconds": 0.3}, "deadline_seconds": None}


class CatalogTest(unittest.TestCase):
    def test_workload_missing_from_catalog_is_unknown_even_with_slo_metrics(self):
        # the canned world has slo_* metrics for `api`; the catalog does not list it
        _, d = run({("shop", "web"): {"objectives": {}, "deadline_seconds": 7200}})
        c = d[("api", "cpu")]["conditions"]["C"]
        self.assertEqual(c["status"], "UNKNOWN")
        self.assertIn("SLO 目录", c["why"])
        # c0046: the canned world answers any max_over_time with the window max, so B's recent-usage need can come first; the SLO need must be listed
        self.assertTrue(any("需要在 SLO 目录里为该 workload 定义 SLO" in n for n in d[("api", "cpu")]["unknown_needs"]), d[("api", "cpu")]["unknown_needs"])
        self.assertNotEqual(d[("api", "cpu")]["verdict"], "ALLOW")

    def test_catalog_entry_with_metrics_passes(self):
        _, d = run({("shop", "api"): API, ("shop", "web"): API})
        self.assertEqual(d[("api", "cpu")]["conditions"]["C"]["status"], "PASS")
        self.assertEqual(d[("api", "cpu")]["slo"]["objectives"], {"availability": 0.999, "p99_seconds": 0.3})
        self.assertTrue(d[("api", "cpu")]["slo"]["defined"])

    def test_deadline_comes_from_the_catalog_not_from_the_metric_label(self):
        # web: last run 5000s; the metric label says deadline 7200 (5000 <= 0.8 x 7200, would pass), the catalog says 5500 (5000 > 0.8 x 5500)
        _, d = run({("shop", "api"): API, ("shop", "web"): {"objectives": {}, "deadline_seconds": 5500}})
        c = d[("web", "cpu")]["conditions"]["C"]
        self.assertEqual(c["status"], "FAIL")
        self.assertEqual(d[("web", "cpu")]["verdict"], "DENY")
        self.assertEqual(d[("web", "cpu")]["slo"]["deadline_seconds"], 5500.0)

    def test_batch_without_catalog_deadline_has_no_deadline_even_if_label_exists(self):
        _, d = run({("shop", "api"): API, ("shop", "web"): {"objectives": {"availability": 0.999}, "deadline_seconds": None}})
        # catalog says: an online objective, no deadline; no burn metric for web -> the SLO signal is incomplete
        self.assertEqual(d[("web", "cpu")]["conditions"]["C"]["status"], "UNKNOWN")

    def test_catalog_numbers_are_evidence_with_their_own_source(self):
        out, _ = run({("shop", "api"): API, ("shop", "web"): {"objectives": {}, "deadline_seconds": 7200}})
        ev = {e["query"]: e for e in pick(out, "api", "cpu")["evidence"]}
        self.assertEqual(ev["slo_catalog:shop/api.p99_seconds"]["value"], 0.3)
        self.assertEqual(ev["slo_catalog:shop/api.availability"]["source"], "slo_catalog")
        web = {e["query"]: e for e in pick(out, "web", "cpu")["evidence"]}
        self.assertEqual(web["slo_catalog:shop/web.deadline_seconds"]["value"], 7200.0)

    def test_no_catalog_keeps_the_old_behaviour(self):
        out = collect(as_of=AS_OF)
        d = {(x["workload"], x["resource"]): x for x in decide(out["facts"], out["cluster"], POLICY)}
        self.assertIsNone(d[("api", "cpu")]["slo"]["defined"])
        self.assertEqual(d[("api", "cpu")]["conditions"]["C"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
