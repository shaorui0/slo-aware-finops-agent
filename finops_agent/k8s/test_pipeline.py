"""End to end against the in-process k8s test world: catalog -> collect -> decide -> narrate -> ledger -> plans."""
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import yaml

from finops_agent.__main__ import main
from finops_agent.k8s import ledger
from finops_agent.llm import CassetteMissing
from testenv import metrics_server

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "testenv" / "k8s_world.yaml"
SLOS = ROOT / "testenv" / "k8s_slos.yaml"


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        metrics_server.use_k8s(True)
        metrics_server.build()
        cls.addClassCleanup(metrics_server.use_k8s, False)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), metrics_server.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.srv.server_close)
        cls.addClassCleanup(cls.srv.shutdown)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.world = yaml.safe_load(WORLD.read_text())

    def go(self, tmp, run_id="t", extra=(), slos=SLOS):
        argv = ["k8s", "run", "--prom", self.url, "--slos", str(slos), "--nodes-from", str(WORLD), "--out", str(tmp),
                "--state", str(Path(tmp) / "k8s_state.json"), "--run-id", run_id, "--narrate", "template", *extra]
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            main(argv)
        return Path(tmp) / run_id

    def test_full_run_matches_every_expectation(self):
        with tempfile.TemporaryDirectory() as t:
            out = self.go(t)
            doc = json.loads((out / "k8s_decisions.json").read_text())
            exp = {(w["namespace"], w["name"]): w["expect"] for w in self.world["workloads"]}
            wrong = [d["id"] for d in doc["decisions"] if d["verdict"] != exp[(d["namespace"], d["workload"])][d["resource"]]["verdict"]]
            self.assertEqual((len(doc["decisions"]), wrong), (18, []))
            self.assertTrue(all(d["narrative"]["source"] == "template" for d in doc["decisions"]))
            plans = json.loads((out / "k8s_plans.json").read_text())
            actionable = {d["id"] for d in doc["decisions"] if d["verdict"] in ("ALLOW", "REQUIRE_APPROVAL")}
            self.assertEqual({p["decision_id"] for p in plans["plans"]}, actionable)
            self.assertFalse(plans["safe_halt"])
            for f in ("k8s_summary.json", "k8s_trace.jsonl"):
                self.assertTrue((out / f).exists())
            state = ledger.load_state(Path(t) / "k8s_state.json")
            self.assertEqual({p["status"] for p in state["proposals"]}, {"proposed"})
            self.assertEqual(len(state["proposals"]), len(actionable))

    def test_second_run_is_unchanged_and_keeps_plan_ids(self):
        with tempfile.TemporaryDirectory() as t:
            a = json.loads((self.go(t, "a") / "k8s_plans.json").read_text())
            out = self.go(t, "b")
            s = json.loads((out / "k8s_summary.json").read_text())
            self.assertEqual((s["ledger"]["new"], s["ledger"]["expired"], s["ledger"]["unchanged"]), (0, 0, len(a["plans"])))
            self.assertEqual([p["plan_id"] for p in a["plans"]], [p["plan_id"] for p in json.loads((out / "k8s_plans.json").read_text())["plans"]])

    def test_halt_stops_plans_end_to_end(self):
        with tempfile.TemporaryDirectory() as t:
            self.go(t, "a")
            sp = Path(t) / "k8s_state.json"
            # a human applied burning-slo's cpu proposal in an earlier world; here the SLO of an applied workload fails
            st = ledger.load_state(sp)
            key = "shop/api-steady/cpu"
            st = ledger.mark(ledger.mark(st, key, "approved"), key, "applied")
            # pretend the applied workload is the burning one: relabel the proposal's workload
            for p in st["proposals"]:
                if p["key"] == key:
                    p.update(key="shop/burning-slo/cpu", workload="burning-slo", decision_id="K-shop-burning-slo-cpu")
            ledger.save_state(sp, st)
            out = self.go(t, "b")
            plans = json.loads((out / "k8s_plans.json").read_text())
            self.assertTrue(plans["safe_halt"])
            self.assertEqual(plans["plans"], [])
            self.assertTrue(ledger.load_state(sp)["safe_halt"])

    def test_workload_missing_from_catalog_is_not_allow(self):
        with tempfile.TemporaryDirectory() as t:
            cat = yaml.safe_load(SLOS.read_text())
            cat["slos"] = [s for s in cat["slos"] if s["workload"] != "api-steady"]
            p = Path(t) / "cat.yaml"
            p.write_text(yaml.safe_dump(cat))
            doc = json.loads((self.go(t, slos=p) / "k8s_decisions.json").read_text())
            d = next(x for x in doc["decisions"] if x["id"] == "K-shop-api-steady-cpu")
            self.assertEqual((d["conditions"]["C"]["status"], d["verdict"]), ("UNKNOWN", "REQUIRE_APPROVAL"))

    def test_replay_without_cassette_fails_loudly(self):
        with tempfile.TemporaryDirectory() as t, mock.patch("finops_agent.llm.CASSETTE_DIR", Path(t) / "none"):
            argv = ["k8s", "run", "--prom", self.url, "--slos", str(SLOS), "--nodes-from", str(WORLD), "--out", t,
                    "--state", str(Path(t) / "s.json"), "--run-id", "x"]
            with mock.patch.dict("os.environ", {"FINOPS_LLM_MODE": "replay"}), self.assertRaises(CassetteMissing):
                main(argv)
            self.assertFalse((Path(t) / "x" / "k8s_decisions.json").exists())

    def test_mark_and_clear_halt_cli(self):
        with tempfile.TemporaryDirectory() as t:
            self.go(t)
            sp = str(Path(t) / "k8s_state.json")
            with redirect_stdout(io.StringIO()):
                main(["k8s", "mark", "--state", sp, "--ref", "shop/api-steady/cpu", "--status", "approved"])
                main(["k8s", "mark", "--state", sp, "--ref", "P-K-shop-api-steady-cpu-0.25", "--status", "applied"])
            st = ledger.load_state(sp)
            self.assertEqual(next(p for p in st["proposals"] if p["key"] == "shop/api-steady/cpu")["status"], "applied")
            st["safe_halt"] = True
            ledger.save_state(sp, st)
            with redirect_stdout(io.StringIO()):
                main(["k8s", "clear-halt", "--state", sp])
            self.assertFalse(ledger.load_state(sp)["safe_halt"])


if __name__ == "__main__":
    unittest.main()
