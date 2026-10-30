"""c0043: a workload whose metrics stop before as_of must not vanish. Discovery is over the window, and condition A
turns UNKNOWN with an explicit 'no data in the last N days' need (spec k8s-mainline, c0043)."""
import re
import unittest
from urllib.parse import parse_qs, urlparse

from finops_agent.collect import Trace
from finops_agent.k8s import collect as C
from finops_agent.k8s import ledger as L
from finops_agent.k8s import report as R
from finops_agent.k8s.collect import Prom, collect_facts
from finops_agent.k8s.narrate import template_narrative
from finops_agent.k8s.policy import decide, load_policy
from finops_agent.k8s.test_collect import AS_OF, WORLD, fake, hourly_series, vec

POLICY = load_policy()
STALE_DAYS = POLICY.get("stale_days", 3)
LAST_AGE_DAYS = 5      # the late workload's last sample is this many days before as_of (> stale_days)


def late_fetch(url):
    """test_collect's canned world plus workload `late`: samples only up to LAST_AGE_DAYS before as_of.
    Like Prometheus: an instant query at as_of finds nothing for it (lookback is minutes); a window query does."""
    u = urlparse(url)
    qs = parse_qs(u.query)
    q = qs.get("query", [""])[0]
    if u.path == "/api/v1/query" and q.startswith("max by (namespace, workload, team)"):
        out = fake(u.path, qs)
        if "max_over_time(" in q:
            out["data"]["result"].append(vec({"namespace": "shop", "workload": "late", "team": "ops"}, 2))
        return out
    if 'workload="late"' in q:
        if u.path == "/api/v1/query_range" and (C.CPU_USAGE in q or C.MEM_WS in q):
            big = 1024 ** 3 if C.MEM_WS in q else 1
            pts = hourly_series(9, 0.2 * big, 0.5 * big, as_of=AS_OF - LAST_AGE_DAYS * 86400)
            return {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {}, "values": pts}]}}
        return {"status": "success", "data": {"resultType": "vector" if u.path == "/api/v1/query" else "matrix", "result": []}}
    return fake(u.path, qs)


def run():
    out = collect_facts(Prom("http://prom", Trace(), late_fetch), POLICY, as_of=AS_OF)
    return out, decide(out["facts"], out["cluster"], POLICY)


def by(decs, wl, res):
    return next(d for d in decs if d["workload"] == wl and d["resource"] == res)


class StaleDiscoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out, cls.decs = run()

    def test_stale_workload_is_discovered_for_both_resources(self):
        self.assertEqual([d["id"] for d in self.decs],
                         ["K-shop-api-cpu", "K-shop-api-memory", "K-shop-late-cpu", "K-shop-late-memory", "K-shop-web-cpu", "K-shop-web-memory"])

    def test_stale_workload_is_unknown_with_an_explicit_no_data_need(self):
        for res in ("cpu", "memory"):
            d = by(self.decs, "late", res)
            self.assertEqual(d["verdict"], "UNKNOWN")
            self.assertEqual(d["conditions"]["A"]["status"], "UNKNOWN")
            self.assertIn(f"最近 {STALE_DAYS} 天无数据", d["conditions"]["A"]["why"])
            self.assertTrue(any(f"最近 {STALE_DAYS} 天无数据" in n for n in d["unknown_needs"]), d["unknown_needs"])
            self.assertIn(f"{LAST_AGE_DAYS} 天前", d["conditions"]["A"]["why"])

    def test_never_allow_or_require_approval(self):
        for f in self.out["facts"]:
            if f["workload"] != "late":
                continue
            # even if every number were perfect, a stale workload must not be actionable
            f["usage"].update(p99=0.5, max=2.0, valid_days=14, coverage=1.0, throttle_peak=0.0 if f["resource"] == "cpu" else None, oom_events=0.0)
            f["current"].update(request=2.0, limit=4.0, replicas=2)
        decs = decide(self.out["facts"], self.out["cluster"], POLICY)
        for d in decs:
            if d["workload"] == "late":
                self.assertEqual(d["verdict"], "UNKNOWN")

    def test_fresh_workloads_are_untouched(self):
        d = by(self.decs, "api", "cpu")
        self.assertEqual(d["conditions"]["A"]["status"], "PASS")
        self.assertEqual(d["conditions"]["A"]["why"], f"有效天数 14 ≥ {POLICY['min_days']}，样本覆盖率 1.00 ≥ 0.90")
        for d in self.decs:
            if d["workload"] != "late":
                self.assertNotIn("无数据", d["conditions"]["A"]["why"])
                self.assertFalse([n for n in d["unknown_needs"] if "无数据" in n])

    def test_template_narrative_renders_without_none(self):
        d = by(self.decs, "late", "cpu")
        nar = template_narrative(d)
        blob = repr([nar["why"], nar["would_change"]])   # the text fields (the dict repr of a null proposal is not text)
        self.assertNotIn("None", blob)
        self.assertIn("无数据", blob)

    def test_daily_report_lists_it_in_the_unknown_table(self):
        page = R.render("daily", {"decisions": {"as_of": self.out["as_of"], "world": "t", "decisions": self.decs}})
        self.assertNotIn("None", re.sub(r"<[^>]+>", " ", page))
        table = page.split("被拒与数据不足", 1)[1]
        self.assertIn("shop/late", table)
        self.assertIn(f"最近 {STALE_DAYS} 天无数据", table)


class StaleLedgerTest(unittest.TestCase):
    def test_open_proposal_expires_when_the_workload_goes_stale(self):
        out, decs = run()
        api = by(decs, "api", "cpu")
        self.assertIn(api["verdict"], L.ACTIONABLE)          # the canned api workload is actionable
        st, diff = L.update(L.empty_state(), decs, "r1", "d1", POLICY["same_target_tolerance"])
        self.assertIn("shop/api/cpu", diff["new"])
        stale = dict(api, verdict="UNKNOWN", recommendation={"request": None, "method": None, "headroom": 0.3})
        st, diff = L.update(st, [stale], "r2", "d2", POLICY["same_target_tolerance"])
        p = next(p for p in st["proposals"] if p["key"] == "shop/api/cpu")
        self.assertEqual(p["status"], "expired")
        self.assertIn("不再是可提案", p["history"][-1]["note"])
        self.assertNotIn("不在指标", p["history"][-1]["note"])


class StaleScenarioTest(unittest.TestCase):
    """End to end through the synthetic metrics server: testenv/k8s_scenarios/stale_workload.yaml (base world + edge-sync, silent 5 days)."""
    SC = "testenv/k8s_scenarios/stale_workload.yaml"

    def test_edge_sync_is_unknown_and_nothing_else_is_lost(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from testenv import metrics_server
        metrics_server.use_k8s(True, self.SC)
        self.addCleanup(metrics_server.use_k8s, False)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), metrics_server.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        prom = Prom(f"http://127.0.0.1:{srv.server_address[1]}", Trace())
        out = collect_facts(prom, POLICY)
        decs = decide(out["facts"], out["cluster"], POLICY)
        mine = [d for d in decs if d["workload"] == "edge-sync"]
        self.assertEqual(sorted(d["resource"] for d in mine), ["cpu", "memory"])
        for d in mine:
            self.assertEqual(d["verdict"], "UNKNOWN")
            self.assertTrue(any("无数据" in n for n in d["unknown_needs"]), d["unknown_needs"])
        self.assertTrue(all(d["verdict"] not in L.ACTIONABLE for d in mine))
        self.assertEqual(len(decs), 20)      # 9 base workloads x 2 + edge-sync x 2
        json.dumps(decs)


if __name__ == "__main__":
    unittest.main()
