"""c0046: condition B's "recent usage" sub-check. A recommendation below the recent-window observed max x (1+margin) must not PASS B
(UNKNOWN -> REQUIRE_APPROVAL), for cpu and memory alike (spec k8s-mainline, c0046)."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from finops_agent.collect import Trace
from finops_agent.k8s import report as R
from finops_agent.k8s.collect import Prom, collect_facts
from finops_agent.k8s.policy import decide, load_policy
from finops_agent.k8s.test_policy import make_cluster, make_facts, one

POLICY = load_policy()
H = POLICY.get("recent_hours", 6)


def recent(facts, value, hours=H):
    """Facts as collect.py now writes them: the recent-window max, plus the evidence entry when it is a factor."""
    facts["recent"] = {"hours": hours, "max": value}
    return facts


class RecentSubcheckUnitTest(unittest.TestCase):
    def test_cpu_recommendation_below_recent_max_is_not_allow(self):
        # p99 0.5 -> rec 0.65; recent max 0.9 x 1.1 = 0.99 > 0.65
        d = one(recent(make_facts("cpu"), 0.9))
        self.assertEqual(d["recommendation"]["request"], 0.65)
        self.assertEqual(d["conditions"]["B"]["status"], "UNKNOWN")
        self.assertEqual(d["verdict"], "REQUIRE_APPROVAL")
        self.assertIn("近期", d["conditions"]["B"]["why"])
        self.assertIn("0.9", d["conditions"]["B"]["why"])
        self.assertTrue(any("近期" in n for n in d["unknown_needs"]), d["unknown_needs"])

    def test_cpu_recommendation_above_recent_max_still_allows(self):
        d = one(recent(make_facts("cpu"), 0.55))      # 0.55 x 1.1 = 0.605 <= 0.65
        self.assertEqual(d["conditions"]["B"]["status"], "PASS")
        self.assertEqual(d["verdict"], "ALLOW")

    def test_memory_gets_the_same_subcheck(self):
        # max 2.0 -> rec 2.4; a recent value of 2.4 x 1.1 > 2.4 (only reachable synthetically for memory: the window max bounds it)
        d = one(recent(make_facts("memory"), 2.4))
        self.assertEqual(d["conditions"]["B"]["status"], "UNKNOWN")
        self.assertEqual(d["verdict"], "REQUIRE_APPROVAL")
        self.assertEqual(one(recent(make_facts("memory"), 2.0))["verdict"], "ALLOW")

    def test_boundary_is_not_strict_failure(self):
        # rec 0.65 == recent 0.59090.. x 1.1: equal passes (same epsilon rule as the p99 / window-max checks)
        d = one(recent(make_facts("cpu"), 0.65 / (1 + POLICY["margin"])))
        self.assertEqual(d["conditions"]["B"]["status"], "PASS")

    def test_missing_recent_value_is_unknown_not_pass(self):
        d = one(recent(make_facts("cpu"), None))
        self.assertEqual(d["conditions"]["B"]["status"], "UNKNOWN")
        self.assertEqual(d["verdict"], "REQUIRE_APPROVAL")

    def test_other_failures_still_win(self):
        f = recent(make_facts("cpu", usage={"throttle_peak": 0.3}), 0.9)
        d = one(f)
        self.assertEqual(d["conditions"]["B"]["status"], "FAIL")
        self.assertEqual(d["verdict"], "DENY")

    def test_decision_without_the_recent_fact_is_unchanged(self):
        d = one(make_facts("cpu"))
        self.assertEqual(d["verdict"], "ALLOW")
        self.assertEqual(d["conditions"]["B"]["why"].count("近期"), 0)


class StepUpScenarioTest(unittest.TestCase):
    """End to end on testenv/k8s_scenarios/recent_step.yaml: step-up-svc's cpu tripled 3 hours before as_of."""
    SC = "testenv/k8s_scenarios/recent_step.yaml"

    @classmethod
    def setUpClass(cls):
        from testenv import metrics_server
        metrics_server.use_k8s(True, cls.SC)
        cls.addClassCleanup(metrics_server.use_k8s, False)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), metrics_server.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.addClassCleanup(srv.shutdown)
        cls.out = collect_facts(Prom(f"http://127.0.0.1:{srv.server_address[1]}", Trace()), POLICY)
        cls.decs = decide(cls.out["facts"], cls.out["cluster"], POLICY)

    def get(self, wl, res):
        return next(d for d in self.decs if d["workload"] == wl and d["resource"] == res)

    def test_step_up_cpu_is_not_allow_and_b_cites_the_recent_value(self):
        d = self.get("step-up-svc", "cpu")
        self.assertEqual(d["recommendation"]["request"], 0.3)       # the 14d p99 is the old regime
        self.assertNotEqual(d["verdict"], "ALLOW")
        self.assertEqual(d["verdict"], "REQUIRE_APPROVAL")
        b = d["conditions"]["B"]
        self.assertEqual(b["status"], "UNKNOWN")
        self.assertIn("近期", b["why"])
        ev = {e["id"]: e for e in d["evidence"]}
        recent_ev = [ev[i] for i in b["evidence"] if f"[{H}h]" in str(ev[i]["query"])]
        self.assertEqual(len(recent_ev), 1)
        self.assertEqual(recent_ev[0]["window"], f"{H}h")
        self.assertGreater(recent_ev[0]["value"], 0.6)              # ~0.63: three times the old 0.2

    def test_step_up_memory_is_unaffected(self):
        self.assertEqual(self.get("step-up-svc", "memory")["verdict"], "ALLOW")

    def test_base_workloads_keep_their_verdicts(self):
        base = {("api-steady", "cpu"): "ALLOW", ("api-tidal", "cpu"): "DENY", ("burning-slo", "cpu"): "DENY"}
        for (wl, res), v in base.items():
            self.assertEqual(self.get(wl, res)["verdict"], v, (wl, res))

    def test_unaffected_decisions_carry_no_recent_evidence(self):
        d = self.get("api-steady", "memory")
        self.assertFalse([e for e in d["evidence"] if f"[{H}h]" in str(e["query"])])
        self.assertNotIn("近期", d["conditions"]["B"]["why"])

    def test_report_labels_the_recent_evidence_in_chinese(self):
        d = self.get("step-up-svc", "cpu")
        labels = R.evidence_labels(d["evidence"])
        self.assertTrue(any(v and "近期" in v for v in labels.values()), labels)
        page = R.render("daily", {"decisions": {"as_of": self.out["as_of"], "world": "t", "decisions": self.decs}})
        self.assertIn("近期使用量", page)
        json.dumps(self.decs)


if __name__ == "__main__":
    unittest.main()
