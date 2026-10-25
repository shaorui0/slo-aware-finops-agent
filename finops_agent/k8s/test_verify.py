"""c0033: K8s savings verification. The pipeline runs once on the base world (template narration, no LLM); the shipped
after-scenarios are then verified against its decisions and plans."""
import copy
import io
import itertools
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

from finops_agent.__main__ import main as agent_main
from finops_agent.k8s import packing, verify
from testenv import metrics_server

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "testenv" / "k8s_world.yaml"
SLOS = ROOT / "testenv" / "k8s_slos.yaml"
APPLY = ROOT / "testenv" / "k8s_scenarios" / "after_apply.yaml"
CLEAN = ROOT / "testenv" / "k8s_scenarios" / "after_clean.yaml"
STEADY, TRAP = "P-K-shop-api-steady-cpu-0.25", "P-K-shop-packing-trap-cpu-0.2"
HPA, NOSLO = "P-K-shop-hpa-managed-cpu-0.45", "P-K-tools-no-slo-cpu-0.2"
CONTRACT_KEYS = ["plan_id", "decision_id", "predicted_usd_month", "realized_usd_month", "ratio", "nodes_before", "nodes_after",
                 "deviations", "slo_ok", "window"]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        metrics_server.use_k8s(True)
        metrics_server.build()
        cls.addClassCleanup(metrics_server.use_k8s, False)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), metrics_server.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.addClassCleanup(srv.server_close)
        cls.addClassCleanup(srv.shutdown)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        argv = ["k8s", "run", "--prom", f"http://127.0.0.1:{srv.server_address[1]}", "--slos", str(SLOS), "--nodes-from", str(WORLD),
                "--out", cls.tmp.name, "--state", str(Path(cls.tmp.name) / "st.json"), "--run-id", "t", "--narrate", "template"]
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            agent_main(argv)
        cls.decisions = json.loads((Path(cls.tmp.name) / "t" / "k8s_decisions.json").read_text())["decisions"]
        cls.plans = json.loads((Path(cls.tmp.name) / "t" / "k8s_plans.json").read_text())["plans"]
        cls.detail = verify.run_verify(cls.decisions, cls.plans, verify.from_scenario(APPLY))
        cls.control = verify.run_verify(cls.decisions, cls.plans, verify.from_scenario(CLEAN))

    def plan(self, pid, detail=None):
        return next(p for p in (detail or self.detail)["plans"] if p["plan_id"] == pid)


class ClosureTest(Base):
    def test_causes_sum_to_the_bill_change_exactly(self):
        for d in (self.detail, self.control):
            total = sum(c["usd_month"] for c in d["causes"])
            self.assertAlmostEqual(total, d["bill"]["delta_usd_month"], places=9)
            self.assertAlmostEqual(d["closure"]["diff"], 0.0, places=9)
            self.assertAlmostEqual(d["bill"]["after_usd_month"] - d["bill"]["before_usd_month"], d["bill"]["delta_usd_month"], places=9)

    def test_bill_change_is_unrelated_growth_minus_realized(self):
        d = self.detail
        b = sum(c["usd_month"] for c in d["causes"] if c["cause"] == "b")
        self.assertAlmostEqual(d["bill"]["delta_usd_month"], b - d["realized_total_usd_month"], places=9)

    def test_each_plan_gap_is_the_sum_of_its_deviations(self):
        for d in (self.detail, self.control):
            for p in d["plans"]:
                self.assertAlmostEqual(p["realized_usd_month"] - p["predicted_usd_month"], sum(p["terms_usd_month"].values()), places=9)
                self.assertAlmostEqual(sum(x["usd"] for x in p["deviations"]), sum(p["terms_usd_month"].values()), places=9)

    def test_closes_for_every_application_order(self):
        for perm in itertools.permutations([STEADY, TRAP, HPA, NOSLO]):
            tl = verify.from_scenario(APPLY)
            by = {c.plan_id: c for c in tl.changes}
            for k, pid in enumerate(perm):
                by[pid].at = verify._ts("2026-10-26T00:00:00Z") + k * 3600   # all before the NOSLO rollback at 10-29
            d = verify.run_verify(self.decisions, self.plans, tl)
            self.assertAlmostEqual(d["closure"]["diff"], 0.0, places=9)
            for p in d["plans"]:
                self.assertAlmostEqual(p["realized_usd_month"] - p["predicted_usd_month"], sum(p["terms_usd_month"].values()), places=9)

    def test_contract_rounding_stays_within_a_cent_per_entry(self):
        for p in verify.to_contract(self.detail):
            self.assertLess(abs(p["realized_usd_month"] - p["predicted_usd_month"] - sum(x["usd"] for x in p["deviations"])), 0.03)


class CauseTest(Base):
    def test_clean_change_realizes_the_prediction(self):
        p = self.plan(STEADY)
        self.assertAlmostEqual(p["predicted_usd_month"], 140.16, places=2)
        self.assertAlmostEqual(p["realized_usd_month"], 140.16, places=2)
        self.assertAlmostEqual(p["ratio"], 1.0, places=6)
        self.assertEqual(p["deviations"], [])
        self.assertEqual((p["nodes_before"], p["nodes_after"]), (6, 5))

    def test_packing_trap_realizes_zero_with_cause_e_in_plain_chinese(self):
        for d in (self.detail, self.control):
            p = self.plan(TRAP, d)
            self.assertEqual(p["realized_usd_month"], 0.0)
            self.assertGreater(p["book_usd_month"], 30)          # the paper release is real money on paper
            self.assertEqual([x["cause"] for x in p["deviations"]], [verify.CAUSE_LABEL["e"]])
            ex = p["deviations"][0]["explanation"]
            self.assertIn("节点数没变", ex)
            self.assertIn("账单不变", ex)
            self.assertIn(f"{p['book_usd_month']:.2f}", ex)

    def test_rollback_is_cause_d_and_ratio_is_zero(self):
        p = self.plan(NOSLO)
        self.assertEqual(p["realized_usd_month"], 0.0)
        self.assertEqual(p["ratio"], 0.0)
        self.assertAlmostEqual(p["predicted_usd_month"], 140.16, places=2)
        self.assertIn(verify.CAUSE_LABEL["d"], [x["cause"] for x in p["deviations"]])
        self.assertTrue(any(c["cause"] == "d" and c["scope"] == NOSLO and c["usd_month"] > 0 for c in self.detail["causes"]))
        self.assertIsNone(p["slo_ok"])    # no SLO defined: UNKNOWN is never a pass

    def test_hpa_scale_out_is_cause_c(self):
        c = [x for x in self.detail["causes"] if x["cause"] == "c"]
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["scope"], HPA)
        self.assertGreater(c[0]["usd_month"], 0)                 # the scale-out costs a node for part of the window
        self.assertIn("HPA", c[0]["explanation"])
        causes = [x["cause"] for x in self.plan(HPA)["deviations"]]
        self.assertIn(verify.CAUSE_LABEL["c"], causes)

    def test_unrelated_growth_is_cause_b_and_eats_part_of_the_bill_saving(self):
        b = [x for x in self.detail["causes"] if x["cause"] == "b"]
        self.assertEqual(len(b), 1)
        self.assertGreater(b[0]["usd_month"], 0)
        naive_saving = -self.detail["bill"]["delta_usd_month"]
        self.assertLess(naive_saving, self.detail["realized_total_usd_month"])
        self.assertAlmostEqual(self.detail["realized_total_usd_month"] - naive_saving, b[0]["usd_month"], places=9)

    def test_ratio_not_100_percent_with_a_classified_cause(self):
        off = [p for p in self.detail["plans"] if p["ratio"] is not None and abs(p["ratio"] - 1) > 0.01]
        self.assertTrue(off)
        for p in off:
            self.assertTrue(p["deviations"])

    def test_control_scenario_invents_no_deviation(self):
        self.assertEqual({p["plan_id"] for p in self.control["plans"]}, {STEADY, TRAP})
        self.assertEqual(self.plan(STEADY, self.control)["deviations"], [])
        self.assertAlmostEqual(self.plan(STEADY, self.control)["ratio"], 1.0, places=9)
        self.assertEqual([c["cause"] for c in self.control["causes"] if abs(c["usd_month"]) > 1e-9 and c["cause"] != "a"], [])

    def test_prediction_is_the_decisions_realizable(self):
        dec = {d["id"]: d for d in self.decisions}
        for p in self.detail["plans"]:
            self.assertEqual(p["predicted_usd_month"], dec[p["decision_id"]]["savings"]["realizable_usd_month"])
            self.assertEqual(p["book_usd_month"], dec[p["decision_id"]]["savings"]["request_value_usd_month"])


class NodesFromFfdTest(Base):
    def test_node_count_is_ffd_over_the_after_state_requests(self):
        w = yaml.safe_load(WORLD.read_text())
        pods = []
        for x in w["workloads"]:
            cpu, mem = float(x["container"]["requests"]["cpu"]), float(x["container"]["requests"]["memory"][:-2])
            if x["name"] == "api-steady":
                cpu = 0.25
            if x["name"] == "packing-trap":
                cpu = 0.2
            pods += [{"namespace": x["namespace"], "pod": f"{x['name']}-{i}", "cpu": cpu, "mem": mem} for i in range(x["replicas"])]
        n = packing.pack(pods, w["nodes"]["allocatable"]["cpu"], w["nodes"]["allocatable"]["memory_gib"], w["nodes"]["min"])
        self.assertEqual(n, self.control["nodes"]["after_modal"])
        self.assertEqual(self.control["bill"]["after_node_hours"], n * 168)
        base_pods = verify.Engine(verify.from_scenario(CLEAN)).pods(verify._ts("2026-10-20T00:00:00Z"), {}, True, frozenset())
        self.assertEqual(packing.pack(base_pods, 3.8, 14.5, 3), self.control["nodes"]["before_modal"])

    def test_node_hours_change_with_the_requests_not_by_hand(self):
        tl = verify.from_scenario(CLEAN)
        eng = verify.Engine(tl)
        hb = verify.hours(tl.after)
        none = eng.node_hours(hb, {}, False)
        steady = eng.node_hours(hb, {STEADY: "intended"}, False)
        self.assertEqual(none - steady, 168)                     # exactly one node fewer for every hour
        self.assertEqual(eng.node_hours(hb, {STEADY: "intended", TRAP: "intended"}, False), steady)   # the trap frees nothing

    def test_hpa_formula(self):
        tl = verify.from_scenario(APPLY)
        eng = verify.Engine(tl)
        h = verify._ts("2026-11-03T00:00:00Z")        # after the 1.5x load, after the cpu change
        d = tl.demand("hpa-managed", h, True)
        self.assertAlmostEqual(d, 0.9, delta=0.1)
        old = eng.replicas("hpa-managed", h, {HPA: "actual"}, True, frozenset())
        new = eng.replicas("hpa-managed", h, {HPA: "actual"}, True, frozenset({"hpa-managed"}))
        self.assertEqual(old, 2)                                  # ceil(0.9 / (0.7 x 1.0)) = 2
        self.assertEqual(new, 3)                                  # ceil(0.9 / (0.7 x 0.45)) = 3


class ContractTest(Base):
    def test_exact_contract_shape(self):
        rows = verify.to_contract(self.detail)
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertEqual(list(r), CONTRACT_KEYS)
            self.assertEqual(r["window"], "7d")
            self.assertIsInstance(r["nodes_before"], int)
            self.assertIn(r["slo_ok"], (True, False, None))
            for x in r["deviations"]:
                self.assertEqual(list(x), ["cause", "usd", "explanation"])
        trap = next(r for r in rows if r["plan_id"] == TRAP)
        self.assertIsNone(trap["ratio"])

    def test_fails_loud_on_inconsistent_inputs(self):
        tl = verify.from_scenario(APPLY)
        with self.assertRaises(ValueError):
            verify.run_verify(self.decisions, [p for p in self.plans if p["plan_id"] != STEADY], tl)
        tl = verify.from_scenario(APPLY)
        next(c for c in tl.changes if c.plan_id == STEADY).request = 0.3
        with self.assertRaises(ValueError):
            verify.run_verify(self.decisions, self.plans, tl)
        tl = verify.from_scenario(APPLY)
        tl.after = (tl.after[0], tl.after[1] - 3600)
        with self.assertRaises(ValueError):
            verify.run_verify(self.decisions, self.plans, tl)

    def test_document_is_chinese_generated_and_has_no_local_paths(self):
        doc = verify.render_doc(self.detail, self.control)
        for needle in ("## 结论先行", "## 方法", "## 账单分解", "## 对照", "## 局限", "(e) 装箱没兑现", "闭合", "实现率", "预测", "实际"):
            self.assertIn(needle, doc)
        self.assertNotIn("/Users/", doc)
        self.assertNotIn("-$0.00", doc)
        self.assertIn("本文由", doc)

    def test_cli_writes_contract_detail_and_doc(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "o"
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                rc = verify.main(["--decisions", str(Path(self.tmp.name) / "t" / "k8s_decisions.json"),
                                  "--plans", str(Path(self.tmp.name) / "t" / "k8s_plans.json"),
                                  "--scenario", str(APPLY), "--control", str(CLEAN), "--out-dir", str(out), "--doc", str(Path(t) / "d.md")])
            self.assertEqual(rc, 0)
            rows = json.loads((out / "k8s_verify.json").read_text())
            self.assertEqual(rows, verify.to_contract(self.detail))
            self.assertTrue((out / "k8s_verify_detail.json").exists())
            self.assertTrue((out / "control" / "k8s_verify.json").exists())
            self.assertEqual((Path(t) / "d.md").read_text(), verify.render_doc(self.detail, self.control))


class SequentialTest(unittest.TestCase):
    def test_marginals_telescope(self):
        val = {frozenset(): 10, frozenset("a"): 9, frozenset("ab"): 9, frozenset("abc"): 7}
        out = verify.sequential(["a", "b", "c"], lambda S: val[S])
        self.assertEqual(out, {"a": -1, "b": 0, "c": -2})
        self.assertEqual(sum(out.values()), val[frozenset("abc")] - val[frozenset()])


if __name__ == "__main__":
    unittest.main()
