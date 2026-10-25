import tempfile
import unittest
from pathlib import Path

from finops_agent.k8s import ledger as L
from finops_agent.k8s import plans as P
from finops_agent.k8s.test_policy import POLICY, make_facts, one

TOL = POLICY["same_target_tolerance"]


def dec(verdict_kw=None, **kw):
    f = make_facts(**kw)
    f["container"] = "app"
    return one(f)


def allow(p99=0.5):
    return dec(usage={"p99": p99})            # cpu 2.0 -> ceil(p99*1.3, 0.05)


def burning(wl="api"):
    return dec(slo={"burn_rate_1h": 5, "budget_remaining": 0.01})


def step(state, decisions, run="r", as_of="2026-10-25T00:00:00Z", **kw):
    return L.update(state, decisions, run, as_of, TOL, **kw)


class LedgerTest(unittest.TestCase):
    def test_new_then_unchanged_within_five_percent(self):
        s, diff = step(L.empty_state(), [allow(0.5)], "r1", "d1")
        self.assertEqual(L.counts(diff)["new"], 1)
        p = s["proposals"][0]
        self.assertEqual((p["status"], p["plan_id"], p["key"], p["target"]), ("proposed", "P-K-shop-api-cpu-0.65", "shop/api/cpu", {"request": 0.65, "unit": "core"}))
        s, diff = step(s, [allow(0.51)], "r2", "d2")           # 0.51*1.3 -> 0.7: more than 5% away from 0.65
        self.assertEqual((diff["expired"], diff["new"]), (["shop/api/cpu"], ["shop/api/cpu"]))
        s, diff = step(s, [allow(0.51)], "r3", "d3")           # identical -> unchanged
        self.assertEqual((diff["unchanged"], diff["new"], diff["expired"]), (["shop/api/cpu"], [], []))
        self.assertEqual(len(s["proposals"]), 2)
        live = [p for p in s["proposals"] if p["status"] == "proposed"][0]
        self.assertEqual((live["first_seen"], live["last_seen"]), ("d2", "d3"))

    def test_target_within_tolerance_only_moves_last_seen(self):
        d1, d2 = allow(0.5), allow(0.5)
        d2["recommendation"] = {**d2["recommendation"], "request": 0.67}        # +3%
        s, _ = step(L.empty_state(), [d1], "r1", "d1")
        s, diff = step(s, [d2], "r2", "d2")
        self.assertEqual((diff["unchanged"], len(s["proposals"])), (["shop/api/cpu"], 1))
        self.assertEqual(s["proposals"][0]["target"]["request"], 0.65)           # stays the target a human may have approved
        self.assertEqual(s["proposals"][0]["latest_recommendation"], 0.67)

    def test_verdict_change_expires(self):
        s, _ = step(L.empty_state(), [allow()], "r1", "d1")
        s, diff = step(s, [burning()], "r2", "d2")
        self.assertEqual((diff["expired"], diff["new"]), (["shop/api/cpu"], []))
        self.assertEqual(s["proposals"][0]["status"], "expired")
        self.assertIn("DENY", s["proposals"][0]["history"][-1]["note"])

    def test_verdict_change_between_actionable_verdicts_renews(self):
        s, _ = step(L.empty_state(), [allow()], "r1", "d1")
        ra = dec(slo=None)
        self.assertEqual(ra["verdict"], "REQUIRE_APPROVAL")
        s, diff = step(s, [ra], "r2", "d2")
        self.assertEqual((diff["expired"], diff["new"]), (["shop/api/cpu"], ["shop/api/cpu"]))

    def test_workload_gone_expires(self):
        s, _ = step(L.empty_state(), [allow()], "r1", "d1")
        s, diff = step(s, [], "r2", "d2")
        self.assertEqual(diff["expired"], ["shop/api/cpu"])

    def test_approval_file_moves_proposed_to_approved(self):
        s, _ = step(L.empty_state(), [allow()], "r1", "d1")
        with tempfile.TemporaryDirectory() as t:
            s2, diff = step(s, [allow()], "r2", "d2", approvals_dir=t)
            self.assertEqual(diff["approved"], [])
            (Path(t) / "P-K-shop-api-cpu-0.65.approved").write_text("ok")
            s2, diff = step(s, [allow()], "r2", "d2", approvals_dir=t)
            self.assertEqual((diff["approved"], s2["proposals"][0]["status"]), (["shop/api/cpu"], "approved"))

    def test_mark_transitions(self):
        s, _ = step(L.empty_state(), [allow()], "r1", "d1")
        with self.assertRaises(ValueError):
            L.mark(s, "shop/api/cpu", "applied")                  # not approved yet
        s = L.mark(s, "P-K-shop-api-cpu-0.65", "approved")
        s = L.mark(s, "shop/api/cpu", "applied")
        s = L.mark(s, "K-shop-api-cpu", "verified")
        self.assertEqual(s["proposals"][0]["status"], "verified")
        with self.assertRaises(ValueError):
            L.mark(s, "shop/api/cpu", "rolled_back")              # verified is terminal: no live proposal

    def test_rolled_back_is_not_reproposed_until_target_moves(self):
        s, _ = step(L.empty_state(), [allow()], "r1", "d1")
        for st in ("approved", "applied", "rolled_back"):
            s = L.mark(s, "shop/api/cpu", st)
        s, diff = step(s, [allow()], "r2", "d2")
        self.assertEqual((diff["suppressed"], diff["new"]), (["shop/api/cpu"], []))
        s, diff = step(s, [allow(0.8)], "r3", "d3")               # target moved
        self.assertEqual(diff["new"], ["shop/api/cpu"])

    def test_applied_then_slo_degrades_halts_and_stops_plans(self):
        s, _ = step(L.empty_state(), [allow()], "r1", "d1")
        s = L.mark(L.mark(s, "shop/api/cpu", "approved"), "shop/api/cpu", "applied")
        self.assertEqual(set(L.plan_targets(s)), set())           # applied: no more plans
        # next run: the workload's SLO is burning (condition C FAIL)
        s, diff = step(s, [burning()], "r2", "d2")
        self.assertEqual(diff["halted"], ["shop/api/cpu"])
        self.assertEqual((s["proposals"][0]["status"], s["safe_halt"]), ("halted", True))
        self.assertEqual(s["halt"]["key"], "shop/api/cpu")
        self.assertEqual(L.plan_targets(s), {})
        plans, _ = P.build_plans([burning()], POLICY, only=set(L.plan_targets(s)))
        self.assertEqual(plans, [])
        # while halted: report only. Another workload's fresh ALLOW is neither proposed nor planned.
        other = dec(wl="web", usage={"p99": 0.5})
        s2, diff = step(s, [burning(), other], "r3", "d3")
        self.assertTrue(diff["frozen"])
        self.assertEqual((diff["new"], diff["expired"]), ([], []))
        self.assertEqual(len(s2["proposals"]), 1)
        self.assertEqual(L.plan_targets(s2), {})
        # human clears the halt: proposals resume
        s3, diff = step(L.clear_halt(s2), [burning(), other], "r4", "d4")
        self.assertEqual(diff["new"], ["shop/web/cpu"])
        self.assertEqual(s3["proposals"][0]["status"], "halted")   # still needs a human decision (rolled_back / verified)

    def test_safe_halt_blocks_plans_for_other_open_proposals(self):
        a, b = allow(), dec(wl="web", usage={"p99": 0.5})
        s, _ = step(L.empty_state(), [a, b], "r1", "d1")
        s = L.mark(L.mark(s, "shop/api/cpu", "approved"), "shop/api/cpu", "applied")
        self.assertEqual(set(L.plan_targets(s)), {"K-shop-web-cpu"})
        s, _ = step(s, [burning(), b], "r2", "d2")
        self.assertEqual(L.plan_targets(s), {})

    def test_save_load_roundtrip(self):
        s, _ = step(L.empty_state(), [allow()], "r1", "d1")
        with tempfile.TemporaryDirectory() as t:
            L.save_state(Path(t) / "x" / "k8s_state.json", s)
            self.assertEqual(L.load_state(Path(t) / "x" / "k8s_state.json"), s)
            self.assertEqual(L.load_state(Path(t) / "none.json"), L.empty_state())


if __name__ == "__main__":
    unittest.main()
