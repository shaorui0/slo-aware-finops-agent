"""c0033: the after-scenario overlays. They extend the base k8s world with timed events; the base file stays untouched,
and no node count is typed into them (finops_agent.k8s.verify derives it by FFD from the requests)."""
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import json

import yaml

from testenv import k8s_world as kw
from testenv import metrics_server

DIR = Path(kw.__file__).parent / "k8s_scenarios"
AFTER = sorted(DIR.glob("after_*.yaml"))
VERIFY_EVENTS = {"set_request", "rollback", "scale", "bump_request"}


class OverlayTest(unittest.TestCase):
    def test_there_are_overlays(self):
        self.assertEqual([p.name for p in AFTER], ["after_apply.yaml", "after_clean.yaml"])

    def test_overlays_are_scenarios_of_the_base_world_with_14_days_after(self):
        base = kw.read_world()
        for p in AFTER:
            raw = yaml.safe_load(p.read_text())
            self.assertEqual(raw["scenario"]["base"], "testenv/k8s_world.yaml")
            w = kw.read_world(p)
            self.assertEqual(w["cluster"]["period"]["end"], "2026-11-09T00:00:00Z")      # 14 days after the base period end
            self.assertEqual(w["cluster"]["period"]["start"], base["cluster"]["period"]["start"])
            self.assertEqual(w["pricing"], base["pricing"])
            self.assertEqual(w["nodes"], base["nodes"])
            for a, b in zip(w["workloads"], base["workloads"]):
                self.assertEqual({k: v for k, v in a.items() if k != "events"}, {k: v for k, v in b.items() if k != "events"})

    def test_no_node_count_is_typed_into_an_overlay(self):
        for p in AFTER:
            raw = yaml.safe_load(p.read_text())
            self.assertNotIn("nodes", raw)
            self.assertNotIn("nodes", raw.get("verify", {}))
            self.assertNotIn("node_count", p.read_text())

    def test_new_events_are_known_types_and_the_base_file_has_none(self):
        base = kw.read_world()
        for w in base["workloads"]:
            self.assertFalse([e for e in w.get("events", []) if e["type"] in VERIFY_EVENTS])
        for p in AFTER:
            for name, evs in (yaml.safe_load(p.read_text()).get("add_events") or {}).items():
                for e in evs:
                    self.assertIn(e["type"], VERIFY_EVENTS | {"load", "oom", "slo_burn"})
                    if e["type"] == "set_request":
                        self.assertTrue(e["plan_id"].startswith("P-K-"))
                        self.assertIn(e["resource"], ("cpu", "memory"))

    def test_windows_fit_inside_the_period_and_have_equal_length(self):
        for p in AFTER:
            raw = yaml.safe_load(p.read_text())
            v = raw["verify"]
            t = lambda s: kw._ts(s)
            self.assertEqual(t(v["before"]["end"]) - t(v["before"]["start"]), t(v["after"]["end"]) - t(v["after"]["start"]))
            self.assertGreaterEqual(t(v["before"]["start"]), t(raw["cluster"]["period"].get("start", "2026-10-05T00:00:00Z")))
            self.assertLessEqual(t(v["after"]["end"]), t(raw["cluster"]["period"]["end"]))
            self.assertLess(t(v["before"]["end"]), t(v["after"]["start"]))

    def test_rollbacks_follow_a_set_request_of_the_same_plan(self):
        raw = yaml.safe_load((DIR / "after_apply.yaml").read_text())
        evs = [e for v in raw["add_events"].values() for e in v]
        applied = {e["plan_id"]: e["at"] for e in evs if e["type"] == "set_request"}
        rolled = {e["plan_id"]: e["at"] for e in evs if e["type"] == "rollback"}
        self.assertTrue(rolled)
        for pid, at in rolled.items():
            self.assertIn(pid, applied)
            self.assertGreater(kw._ts(at), kw._ts(applied[pid]))

    def test_the_five_phenomena_are_all_present_in_after_apply(self):
        raw = yaml.safe_load((DIR / "after_apply.yaml").read_text())
        types = {e["type"] for v in raw["add_events"].values() for e in v}
        self.assertTrue({"set_request", "rollback", "scale", "load"} <= types)
        self.assertIn("hpa", next(w for w in kw.read_world(DIR / "after_apply.yaml")["workloads"] if w["name"] == "hpa-managed"))


class ServedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(metrics_server.use_k8s, False)

    def serve(self, path):
        metrics_server.use_k8s(True, str(path))
        metrics_server.build()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), metrics_server.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_overlay_is_served_and_hpa_load_shows_up_in_usage(self):
        url = self.serve(DIR / "after_apply.yaml")
        q = lambda expr, t: json.load(urlopen(f"{url}/api/v1/query?{urlencode({'query': expr, 'time': t})}"))["data"]["result"]
        sel = 'container_cpu_usage_cores{workload="hpa-managed"}'
        before = sum(float(r["value"][1]) for r in q(sel, kw._ts("2026-10-29T00:00:00Z")))
        after = sum(float(r["value"][1]) for r in q(sel, kw._ts("2026-11-03T00:00:00Z")))
        self.assertGreater(after / before, 1.3)                  # the 1.5x load event from 10-30
        self.assertEqual(len(q('kube_deployment_spec_replicas{workload="hpa-managed"}', kw._ts("2026-11-03T00:00:00Z"))), 1)

    def test_base_world_series_are_unchanged_by_the_overlay_machinery(self):
        base = kw.build_series(kw.load())
        again = kw.build_series(kw.load(as_of=kw.load().t1))
        self.assertEqual(base, again)


if __name__ == "__main__":
    unittest.main()
