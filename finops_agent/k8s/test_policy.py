import copy
import json
import tempfile
import unittest
from pathlib import Path

from finops_agent.k8s.policy import decide, load_policy, verdict_of, write_decisions

POLICY = load_policy()
EV_KEYS = ["request", "limit", "replicas", "p50", "p95", "p99", "max", "valid_days", "coverage", "throttle", "oom",
           "hpa", "burn", "budget", "job", "deadline", "alloc_cpu", "alloc_mem", "node_count", "pods", "pending"]


def make_facts(resource="cpu", request=None, limit=None, replicas=2, usage=None, hpa=None, slo="ok", ns="shop", wl="api"):
    cpu = resource == "cpu"
    cur = {"request": request if request is not None else (2.0 if cpu else 4.0),
           "limit": limit if limit is not None else (4.0 if cpu else 6.0),
           "replicas": replicas, "unit": "core" if cpu else "GiB"}
    u = {"p50": 0.2, "p95": 0.4, "p99": 0.5, "max": 0.9, "valid_days": 14, "coverage": 1.0, "peak_hours_utc": [9, 10],
         "throttle_peak": 0.01, "oom_events": 0}
    if not cpu:
        u.update(p50=1.0, p95=1.4, p99=1.5, max=2.0, throttle_peak=None)
    u.update(usage or {})
    s = {"burn_rate_1h": 0.5, "budget_remaining": 0.8, "job_max_duration": None, "deadline_seconds": None} if slo == "ok" else (slo or {})
    return {"id": f"K-{ns}-{wl}-{resource}", "workload": wl, "namespace": ns, "team": "pay", "resource": resource,
            "current": cur, "usage": u, "hpa": hpa, "slo": s,
            "evidence": [{"id": f"E{i + 1}", "query": f"q_{k}", "window": "14d", "value": 1.0, "source": "prometheus"}
                         for i, k in enumerate(EV_KEYS)],
            "ev": {k: f"E{i + 1}" for i, k in enumerate(EV_KEYS)}}


def pods(wl, n, cpu, mem, ns="shop"):
    return [{"namespace": ns, "workload": wl, "pod": f"{wl}-{i}", "cpu": cpu, "mem": mem} for i in range(n)]


def make_cluster(extra_pods=None, pending=0, **kw):
    c = {"allocatable": {"cpu": 4.0, "memory_gib": 16.0}, "min_nodes": 2, "node_count": 2, "pending": pending,
         "pricing": POLICY["pricing"], "pods": pods("api", 2, 2.0, 4.0) + (extra_pods or [])}
    c.update(kw)
    return c


def one(facts, cluster=None):
    return decide([facts], cluster or make_cluster(), POLICY)[0]


class VerdictOrderTest(unittest.TestCase):
    def test_baseline_is_allow(self):
        d = one(make_facts())
        self.assertEqual(d["verdict"], "ALLOW", d["reasons"])
        self.assertEqual({k: v["status"] for k, v in d["conditions"].items()}, dict(A="PASS", B="PASS", C="PASS", D="PASS"))
        self.assertEqual(d["recommendation"]["request"], 0.65)
        self.assertFalse(d["hpa_coupled"])
        self.assertEqual(d["unknown_needs"], [])

    def test_1_noop_when_recommendation_not_lower(self):
        d = one(make_facts(request=0.6))
        self.assertEqual(d["verdict"], "NOOP")
        self.assertEqual(d["savings"]["request_value_usd_month"], 0)
        # C FAIL (rule 1) and A UNKNOWN (rule 2) come before NOOP
        d = one(make_facts(request=0.6, slo={"burn_rate_1h": 5, "budget_remaining": 0.01}))
        self.assertEqual(d["verdict"], "DENY")
        d = one(make_facts(request=0.6, usage={"valid_days": 3}))
        self.assertEqual(d["verdict"], "UNKNOWN")

    def test_3_noop_but_current_state_unsafe_is_deny(self):
        # recommendation (2.4375) >= current (2.0) but OOMKilled in window: DENY, under-provisioned, raise for a human
        d = one(make_facts("memory", request=2.0, usage={"oom_events": 1}))
        self.assertEqual((d["conditions"]["B"]["status"], d["verdict"]), ("FAIL", "DENY"))
        self.assertTrue(any("under-provisioned" in r for r in d["reasons"]))
        self.assertTrue(any("2.4375" in r for r in d["reasons"]))
        # same for CPU throttling at the current request
        d = one(make_facts(request=0.6, usage={"throttle_peak": 0.1}))
        self.assertEqual(d["verdict"], "DENY")
        # peak working set near the limit, no reduction proposed
        d = one(make_facts("memory", request=2.0, limit=2.1, usage={"max": 2.0}))
        self.assertEqual(d["verdict"], "DENY")

    def test_2_c_fail_beats_a_unknown(self):
        f = make_facts(usage={"valid_days": 5, "coverage": 0.3}, slo={"burn_rate_1h": 3.0, "budget_remaining": 0.8})
        d = one(f)
        self.assertEqual(d["conditions"]["A"]["status"], "UNKNOWN")
        self.assertEqual(d["conditions"]["C"]["status"], "FAIL")
        self.assertEqual(d["verdict"], "DENY")

    def test_c_fail_low_budget_and_batch_deadline(self):
        self.assertEqual(one(make_facts(slo={"burn_rate_1h": 0.5, "budget_remaining": 0.1}))["verdict"], "DENY")
        batch = {"burn_rate_1h": None, "budget_remaining": None, "job_max_duration": 7000, "deadline_seconds": 7200}
        d = one(make_facts(slo=batch))
        self.assertEqual((d["conditions"]["C"]["status"], d["verdict"]), ("FAIL", "DENY"))   # 7000 > 0.8 * 7200
        batch["job_max_duration"] = 5000
        self.assertEqual(one(make_facts(slo=batch))["conditions"]["C"]["status"], "PASS")

    def test_3_a_unknown_gives_unknown_with_needs(self):
        d = one(make_facts(usage={"valid_days": 5, "coverage": 0.36}))
        self.assertEqual(d["verdict"], "UNKNOWN")
        self.assertIn("需要至少 14 天的 CPU 使用数据（当前 5 天）", d["unknown_needs"])
        self.assertTrue(any("覆盖率" in n for n in d["unknown_needs"]))
        d = one(make_facts("memory", usage={"valid_days": 3}))
        self.assertIn("需要至少 14 天的 内存 使用数据（当前 3 天）", d["unknown_needs"])

    def test_3_a_unknown_beats_b_fail(self):
        d = one(make_facts(usage={"valid_days": 5, "throttle_peak": 0.5}))
        self.assertEqual(d["conditions"]["B"]["status"], "FAIL")
        self.assertEqual(d["verdict"], "UNKNOWN")

    def test_4_b_fail_throttling(self):
        d = one(make_facts(usage={"throttle_peak": 0.08}))
        self.assertEqual(d["conditions"]["B"]["status"], "FAIL")
        self.assertIn("throttling", d["conditions"]["B"]["why"])
        self.assertEqual(d["verdict"], "DENY")

    def test_4_b_fail_pending_pods(self):
        d = one(make_facts(), make_cluster(pending=2))
        self.assertEqual((d["conditions"]["B"]["status"], d["verdict"]), ("FAIL", "DENY"))

    def test_4_memory_oom_denied_while_cpu_allowed_on_same_workload(self):
        cpu = make_facts("cpu")
        mem = make_facts("memory", usage={"oom_events": 1})
        out = {d["resource"]: d for d in decide([cpu, mem], make_cluster(), POLICY)}
        self.assertEqual(out["cpu"]["verdict"], "ALLOW")
        self.assertEqual(out["memory"]["verdict"], "DENY")
        self.assertIn("OOMKilled", out["memory"]["conditions"]["B"]["why"])

    def test_4_memory_peak_near_limit_guard(self):
        # request above limit is unusual, but it is the only way the peak/limit guard can trip while
        # the recommendation is still below the current request
        mem = make_facts("memory", request=6.0, limit=5.0, usage={"max": 4.6})
        d = one(mem)
        self.assertEqual(d["conditions"]["B"]["status"], "FAIL")
        self.assertIn("OOM", d["conditions"]["B"]["why"])

    def test_5_unknown_never_allow(self):
        mutations = {
            "no_slo": dict(slo=None),
            "no_throttle_data": dict(usage={"throttle_peak": None}),
            "no_oom_data_mem": None,
            "short_history": dict(usage={"valid_days": 2}),
        }
        for name, kw in mutations.items():
            if kw is None:
                d = one(make_facts("memory", usage={"oom_events": None}))
            else:
                d = one(make_facts(**kw))
            self.assertNotEqual(d["verdict"], "ALLOW", name)
        d = one(make_facts(slo=None))
        self.assertEqual((d["conditions"]["C"]["status"], d["verdict"]), ("UNKNOWN", "REQUIRE_APPROVAL"))
        self.assertTrue(d["unknown_needs"])
        self.assertTrue(any("SLO" in n for n in d["unknown_needs"]))

    def test_5_unknown_pending_and_missing_pods(self):
        self.assertEqual(one(make_facts(), make_cluster(pending=None))["verdict"], "REQUIRE_APPROVAL")
        c = make_cluster()
        c["pods"] = pods("other", 2, 1.0, 1.0)
        d = one(make_facts(), c)
        self.assertEqual(d["conditions"]["D"]["status"], "UNKNOWN")
        self.assertEqual(d["verdict"], "REQUIRE_APPROVAL")

    def test_5_hpa_coupling_requires_approval(self):
        hpa = {"metric": "cpu", "target": 70, "current_replicas": 2, "max_replicas": 10}
        d = one(make_facts(hpa=hpa))
        self.assertEqual(d["verdict"], "REQUIRE_APPROVAL")
        self.assertTrue(d["hpa_coupled"])
        self.assertTrue(all(c["status"] == "PASS" for c in d["conditions"].values()))
        # an HPA on cpu does not couple the memory recommendation
        dm = one(make_facts("memory", hpa=hpa))
        self.assertFalse(dm["hpa_coupled"])
        self.assertEqual(dm["verdict"], "ALLOW")

    def test_6_allow_packing_trap_realizable_zero(self):
        # trap: three 3.5-core pods fill three nodes to 0.5 free; api pods cannot move in, nodes stay at 4
        c = make_cluster(extra_pods=pods("big", 3, 3.5, 1.0), min_nodes=1)
        d = one(make_facts(), c)
        self.assertEqual(d["verdict"], "ALLOW", d["reasons"])
        s = d["savings"]
        self.assertEqual(s["nodes_before"], s["nodes_after"])
        self.assertEqual(s["realizable_usd_month"], 0)
        self.assertGreater(s["request_value_usd_month"], 0)

    def test_6_realizable_positive_needs_rollback_capacity(self):
        # api 2 x 2 core -> 0.5 core lets everything share one node with the 3-core pod: 2 nodes -> 1
        facts = make_facts(usage={"p99": 0.38})
        c = make_cluster(extra_pods=pods("big", 1, 3.0, 1.0), min_nodes=1)
        d = one(facts, c)
        self.assertEqual((d["savings"]["nodes_before"], d["savings"]["nodes_after"]), (2, 1))
        self.assertAlmostEqual(d["savings"]["realizable_usd_month"], 0.192 * 730, places=4)
        # contract-literal D: the post-change cluster (1 node) has no room to roll back 3 cores
        self.assertEqual((d["conditions"]["D"]["status"], d["verdict"]), ("FAIL", "DENY"))
        # a caller that knows the autoscaler ceiling passes max_nodes
        c["max_nodes"] = 3
        d = one(facts, c)
        self.assertEqual((d["conditions"]["D"]["status"], d["verdict"]), ("PASS", "ALLOW"))

    def test_verdict_of_table(self):
        P, F, U = "PASS", "FAIL", "UNKNOWN"
        cases = [
            (True, dict(A=P, B=P, C=P, D=P), False, "NOOP"),
            (True, dict(A=P, B=F, C=P, D=P), False, "DENY"),
            (True, dict(A=U, B=P, C=P, D=P), False, "UNKNOWN"),
            (True, dict(A=P, B=P, C=F, D=P), False, "DENY"),
            (True, dict(A=P, B=U, C=P, D=P), False, "NOOP"),
            (False, dict(A=P, B=P, C=F, D=P), False, "DENY"),
            (False, dict(A=U, B=P, C=P, D=P), False, "UNKNOWN"),
            (False, dict(A=U, B=F, C=P, D=P), False, "UNKNOWN"),
            (False, dict(A=P, B=F, C=P, D=P), False, "DENY"),
            (False, dict(A=P, B=P, C=P, D=F), False, "DENY"),
            (False, dict(A=P, B=U, C=P, D=P), False, "REQUIRE_APPROVAL"),
            (False, dict(A=P, B=P, C=U, D=P), False, "REQUIRE_APPROVAL"),
            (False, dict(A=P, B=P, C=P, D=P), True, "REQUIRE_APPROVAL"),
            (False, dict(A=P, B=P, C=P, D=P), False, "ALLOW"),
        ]
        for noop, st, hpa, want in cases:
            conds = {k: {"status": v} for k, v in st.items()}
            self.assertEqual(verdict_of(noop, conds, hpa), want, (st, hpa))


class OutputTest(unittest.TestCase):
    def test_contract_shape(self):
        d = one(make_facts())
        self.assertEqual(list(d), ["id", "workload", "namespace", "team", "resource", "current", "usage", "recommendation",
                                   "savings", "conditions", "verdict", "reasons", "unknown_needs", "hpa_coupled", "evidence", "narrative",
                                   "container", "slo"])   # the last two are c0031 additions
        self.assertEqual(d["id"], "K-shop-api-cpu")
        self.assertIsNone(d["narrative"])
        self.assertEqual(set(d["savings"]), {"request_value_usd_month", "realizable_usd_month", "nodes_before", "nodes_after"})
        for c in d["conditions"].values():
            self.assertEqual(set(c), {"status", "why", "evidence"})
        ids = {e["id"] for e in d["evidence"]}
        for c in d["conditions"].values():
            self.assertTrue(set(c["evidence"]) <= ids)
        # request value: (2.0 - 0.65) core x 0.0316 x 730 x 2 replicas
        self.assertAlmostEqual(d["savings"]["request_value_usd_month"], 1.35 * 0.0316 * 730 * 2, places=4)

    def test_chinese_why(self):
        d = one(make_facts(usage={"valid_days": 5}))
        self.assertTrue(any("一" <= ch <= "鿿" for ch in d["conditions"]["A"]["why"]))
        self.assertTrue(all(any("一" <= ch <= "鿿" for ch in r) for r in d["reasons"]))

    def test_determinism_same_input_identical_json(self):
        fl = [make_facts("cpu"), make_facts("memory"), make_facts("cpu", wl="web", slo=None)]
        c = make_cluster(extra_pods=pods("web", 2, 1.0, 2.0))
        a = json.dumps(decide(copy.deepcopy(fl), copy.deepcopy(c), POLICY), ensure_ascii=False)
        b = json.dumps(decide(copy.deepcopy(fl), copy.deepcopy(c), POLICY), ensure_ascii=False)
        r = json.dumps(decide(list(reversed(copy.deepcopy(fl))), copy.deepcopy(c), POLICY), ensure_ascii=False)
        self.assertEqual(a, b)
        self.assertEqual(a, r)   # decisions are sorted by id, not by input order

    def test_inputs_not_mutated(self):
        f, c = make_facts(), make_cluster()
        f0, c0 = copy.deepcopy(f), copy.deepcopy(c)
        decide([f], c, POLICY)
        self.assertEqual(f, f0)
        self.assertEqual(c, c0)

    def test_write_decisions(self):
        with tempfile.TemporaryDirectory() as t:
            p = write_decisions(Path(t) / "runs" / "r1" / "k8s_decisions.json", "2026-10-01T00:00:00Z", "k8s", [one(make_facts())])
            doc = json.loads(p.read_text())
        self.assertEqual((doc["as_of"], doc["world"]), ("2026-10-01T00:00:00Z", "k8s"))
        self.assertEqual(doc["decisions"][0]["verdict"], "ALLOW")

    def test_policy_has_all_thresholds(self):
        for k in ["min_days", "min_coverage", "cpu_headroom", "cpu_step", "mem_headroom", "mem_step", "margin",
                  "max_throttle", "oom_guard", "min_budget", "deadline_guard", "same_target_tolerance"]:
            self.assertIn(k, POLICY)
        self.assertEqual((POLICY["min_days"], POLICY["min_coverage"], POLICY["cpu_headroom"], POLICY["mem_headroom"]), (14, 0.9, 0.3, 0.2))


if __name__ == "__main__":
    unittest.main()
