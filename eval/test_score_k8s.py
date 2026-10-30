import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from eval import score_k8s as S

WORLD = {"workloads": [
    {"name": "a", "namespace": "n", "expect": {
        "cpu": {"verdict": "ALLOW", "request_range": [0.2, 0.3], "realizable_usd_month": [100, 150]},
        "memory": {"verdict": "DENY", "request_range": [1, 2]}}},
    {"name": "b", "namespace": "n", "expect": {
        "cpu": {"verdict": "UNKNOWN", "request_range": [0.1, 0.2]},
        "memory": {"verdict": "REQUIRE_APPROVAL", "request_range": [1, 2]}}},
]}


def dec(w, r, verdict, req=0.25, real=120.0, narr="llm"):
    return {"namespace": "n", "workload": w, "resource": r, "verdict": verdict, "recommendation": {"request": req},
            "savings": {"realizable_usd_month": real}, "narrative": {"source": narr}}


def perfect():
    return {"decisions": [dec("a", "cpu", "ALLOW"), dec("a", "memory", "DENY"), dec("b", "cpu", "UNKNOWN"), dec("b", "memory", "REQUIRE_APPROVAL", 1.5)]}


class ScoreTest(unittest.TestCase):
    def test_perfect(self):
        s = S.score(perfect(), WORLD)
        self.assertEqual((s["verdict_accuracy"], s["unsafe_allow"], s["unknown_recall"], s["request_in_range"], s["realizable_accuracy"]),
                         (1.0, 0, 1.0, 1.0, 1.0))
        self.assertEqual(S.gates(s, None), [])
        self.assertEqual(s["narrative_sources"], {"llm": 4})

    def test_unsafe_allow_is_a_hard_gate(self):
        d = perfect()
        d["decisions"][1] = dec("a", "memory", "ALLOW", 1.5)           # expect DENY
        d["decisions"][2] = dec("b", "cpu", "ALLOW", 0.15)             # expect UNKNOWN
        s = S.score(d, WORLD)
        self.assertEqual((s["unsafe_allow"], s["unknown_recall"], s["verdict_accuracy"]), (2, 0.0, 0.5))
        self.assertTrue(any("unsafe_allow" in f for f in S.gates(s, None)))

    def test_accuracy_and_range_gates(self):
        d = perfect()
        d["decisions"][3] = dec("b", "memory", "NOOP", 1.5)            # wrong, but not unsafe
        s = S.score(d, WORLD)
        self.assertEqual((s["verdict_accuracy"], s["unsafe_allow"]), (0.75, 0))
        self.assertTrue(any("verdict_accuracy" in f for f in S.gates(s, None)))
        d = perfect()
        d["decisions"][0] = dec("a", "cpu", "ALLOW", 0.9)              # outside [0.2, 0.3]
        s = S.score(d, WORLD)
        self.assertEqual(s["request_in_range"], 0.5)
        self.assertTrue(any("request_in_range" in f for f in S.gates(s, None)))

    def test_realizable_only_where_expected(self):
        d = perfect()
        d["decisions"][0] = dec("a", "cpu", "ALLOW", real=0.0)
        self.assertEqual(S.score(d, WORLD)["realizable_accuracy"], 0.0)
        w = copy.deepcopy(WORLD)
        del w["workloads"][0]["expect"]["cpu"]["realizable_usd_month"]
        self.assertIsNone(S.score(perfect(), w)["realizable_accuracy"])

    def test_ungrounded_number_in_a_narrative_fails_the_gate(self):
        d = perfect()
        for x in d["decisions"]:
            x.update(id=f"K-n-{x['workload']}-{x['resource']}", current={"request": 1.0}, usage={}, conditions={}, reasons=[], unknown_needs=[], evidence=[], savings={"realizable_usd_month": 120.0})
            x["narrative"] = {"source": "llm", "why": {"status": "request 为 1.0。", "evidence": "e", "conclusion": "c"}, "would_change": ["w"], "proposal": None}
        self.assertEqual(S.score(d, WORLD)["narrative_ungrounded"], [])
        d["decisions"][0]["narrative"]["why"]["evidence"] = "预计节省 98765 USD。"
        s = S.score(d, WORLD)
        self.assertEqual(s["narrative_ungrounded"][0]["numbers"], ["98765"])
        self.assertTrue(any("ungrounded" in f for f in S.gates(s, None)))

    def test_missing_decision_counts_as_wrong(self):
        d = perfect()
        d["decisions"].pop()
        s = S.score(d, WORLD)
        self.assertEqual((s["verdict_accuracy"], s["wrong_verdicts"][0]["got"]), (0.75, None))

    def test_baseline_regression_gate(self):
        base = {"verdict_accuracy": 1.0, "unsafe_allow": 0, "unknown_recall": 1.0, "request_in_range": 1.0, "realizable_accuracy": 1.0}
        self.assertEqual(S.gates(S.score(perfect(), WORLD), base), [])
        d = perfect()
        d["decisions"][0] = dec("a", "cpu", "ALLOW", real=0.0)         # realizable_accuracy 1.0 -> 0.0, no absolute threshold hit
        fails = S.gates(S.score(d, WORLD), base)
        self.assertEqual(len(fails), 1)
        self.assertIn("realizable_accuracy", fails[0])

    def test_cli_exit_codes_and_files(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "w.yaml").write_text(yaml.safe_dump(WORLD))
            (t / "k8s_decisions.json").write_text(json.dumps(perfect()))
            self.assertEqual(S.main([str(t / "k8s_decisions.json"), "--world", str(t / "w.yaml"), "--baseline", str(t / "b.json"), "--write-baseline"]), 0)
            self.assertEqual(json.loads((t / "b.json").read_text())["unsafe_allow"], 0)
            self.assertTrue((t / "k8s_eval.json").exists())
            bad = perfect()
            bad["decisions"][1] = dec("a", "memory", "ALLOW", 1.5)
            (t / "k8s_decisions.json").write_text(json.dumps(bad))
            self.assertEqual(S.main([str(t / "k8s_decisions.json"), "--world", str(t / "w.yaml"), "--baseline", str(t / "b.json")]), 1)


class RecentUsageTest(unittest.TestCase):
    """c0046: allow_below_recent = ALLOW whose recommended request is below the workload's actual recent usage, derived from the world."""
    SC = str(S.ROOT / "testenv" / "k8s_scenarios" / "recent_step.yaml")
    AS_OF = "2026-10-26T00:00:00Z"

    @classmethod
    def setUpClass(cls):
        cls.recent = S.recent_usage(cls.SC, cls.AS_OF)

    def doc(self, verdict, req, res="cpu"):
        d = {"namespace": "shop", "workload": "step-up-svc", "resource": res, "verdict": verdict, "recommendation": {"request": req},
             "savings": {"realizable_usd_month": 0.0}, "narrative": {"source": "llm"}}
        return {"as_of": self.AS_OF, "decisions": [d]}

    def world(self):
        w = yaml.safe_load(Path(self.SC).read_text())["add_workloads"][0]
        return {"workloads": [w]}

    def test_recent_usage_comes_from_the_world_not_the_decision(self):
        r = self.recent[("shop", "step-up-svc", "cpu")]
        self.assertTrue(0.6 < r < 0.75, r)                         # 0.2 x 3, per-pod max over the last hours
        self.assertLess(self.recent[("shop", "step-up-svc", "memory")], 0.7)

    def test_allow_below_recent_usage_is_counted_and_gated(self):
        s = S.score(self.doc("ALLOW", 0.3), self.world(), self.recent)
        self.assertEqual((s["allow_below_recent"], s["allow_below_recent_ids"]), (1, ["step-up-svc/cpu"]))
        self.assertEqual(s["unsafe_allow"], 0)                      # the old metric cannot see it: expect is not DENY/UNKNOWN
        fails = S.gates(s, None)
        self.assertTrue(any("allow_below_recent" in f for f in fails), fails)

    def test_allow_at_or_above_recent_usage_is_not_counted(self):
        s = S.score(self.doc("ALLOW", 0.7), self.world(), self.recent)
        self.assertEqual(s["allow_below_recent"], 0)

    def test_require_approval_is_not_dangerous_but_is_reported(self):
        s = S.score(self.doc("REQUIRE_APPROVAL", 0.3), self.world(), self.recent)
        self.assertEqual((s["allow_below_recent"], s["approval_below_recent"]), (0, 1))
        self.assertEqual([f for f in S.gates(s, None) if "allow_below_recent" in f], [])

    def test_not_computed_without_a_world_series(self):
        s = S.score(self.doc("ALLOW", 0.3), self.world())
        self.assertIsNone(s["allow_below_recent"])
        self.assertEqual([f for f in S.gates(s, None) if "allow_below_recent" in f], [])

    def test_baseline_regression_on_the_new_metric(self):
        base = {"allow_below_recent": 0}
        s = S.score(self.doc("ALLOW", 0.3), self.world(), self.recent)
        self.assertTrue(any("allow_below_recent" in f for f in S.gates(s, base)))

    def test_agent_numbers_are_not_trusted(self):
        d = self.doc("ALLOW", 0.3)
        d["decisions"][0]["usage"] = {"max": 0.1}                   # a lowball number in the decision changes nothing
        d["decisions"][0]["recent"] = {"max": 0.1}
        self.assertEqual(S.score(d, self.world(), self.recent)["allow_below_recent"], 1)

    def test_cli_derives_recent_usage_from_the_world_file(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "k8s_decisions.json").write_text(json.dumps(self.doc("ALLOW", 0.3)))
            rc = S.main([str(t / "k8s_decisions.json"), "--world", self.SC, "--baseline", str(t / "none.json")])
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads((t / "k8s_eval.json").read_text())["allow_below_recent"], 1)


class RealWorldTest(unittest.TestCase):
    def test_world_expect_is_scorable(self):
        world = yaml.safe_load((S.ROOT / "testenv" / "k8s_world.yaml").read_text())
        keys = {(w["namespace"], w["name"], r) for w in world["workloads"] for r in w["expect"]}
        self.assertEqual(len(keys), 18)


if __name__ == "__main__":
    unittest.main()
