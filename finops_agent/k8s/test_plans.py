import json
import re
import unittest

from finops_agent.k8s import plans as P
from finops_agent.k8s.test_policy import POLICY, make_cluster, make_facts, one

CONTRACT_KEYS = ["plan_id", "decision_id", "context", "before", "patch", "apply_cmd", "rollback_cmd", "canary",
                 "observe_seconds", "stop_conditions", "approval_file"]


def dec(**kw):
    slo = kw.pop("slo_def", {"defined": True, "objectives": {"availability": 0.999, "p99_seconds": 0.3}})
    f = make_facts(**kw)
    f["container"], f["slo_def"] = "app", slo
    return one(f)


class QuantityTest(unittest.TestCase):
    def test_kubectl_forms(self):
        self.assertEqual([P.quantity("cpu", v) for v in (2.0, 0.25, 0.65, 1.0)], ["2", "250m", "650m", "1"])
        self.assertEqual([P.quantity("memory", v) for v in (2.0, 1.125, 0.375)], ["2Gi", "1152Mi", "384Mi"])


class PlanTest(unittest.TestCase):
    def test_contract_shape_for_allow(self):
        d = dec()
        self.assertEqual(d["verdict"], "ALLOW")
        p = P.build_plan(d, POLICY)
        self.assertEqual(list(p), CONTRACT_KEYS)
        self.assertEqual(p["plan_id"], "P-K-shop-api-cpu-0.65")
        self.assertEqual((p["decision_id"], p["context"]), ("K-shop-api-cpu", "k3d-finops-test"))
        self.assertEqual(p["before"], {"requests": {"cpu": "2"}, "limits": {"cpu": "4"}, "replicas": 2})
        self.assertEqual(p["patch"]["spec"]["template"]["spec"]["containers"],
                         [{"name": "app", "resources": {"requests": {"cpu": "650m"}}}])
        self.assertTrue(p["apply_cmd"].startswith("kubectl --context k3d-finops-test -n shop patch deploy api --type strategic -p '"))
        self.assertEqual(json.loads(re.search(r"-p '(.*)'$", p["apply_cmd"]).group(1)), p["patch"])
        back = json.loads(re.search(r"-p '(.*)'$", p["rollback_cmd"]).group(1))
        self.assertEqual(back["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"], {"cpu": "2"})
        self.assertEqual((p["canary"], p["observe_seconds"]), ({"scope": "one workload"}, 120))
        self.assertEqual(p["approval_file"], "runs/approvals/P-K-shop-api-cpu-0.65.approved")

    def test_stop_conditions_follow_resource_and_slo(self):
        cpu = P.build_plan(dec(), POLICY)["stop_conditions"]
        self.assertEqual(cpu, [{"signal": "throttle_ratio", "op": ">", "value": POLICY["max_throttle"]},
                               {"signal": "p99_seconds", "op": ">", "value": 0.3},
                               {"signal": "restarts", "op": ">", "value": 0}])
        mem = P.build_plan(dec(resource="memory"), POLICY)["stop_conditions"]
        self.assertEqual([c["signal"] for c in mem], ["p99_seconds", "oom_killed", "restarts"])
        no_obj = P.build_plan(dec(slo_def={"defined": False, "objectives": {}}), POLICY)   # REQUIRE_APPROVAL, no SLO
        self.assertNotIn("p99_seconds", [c["signal"] for c in no_obj["stop_conditions"]])

    def test_require_approval_gets_a_plan(self):
        d = dec(slo_def={"defined": False, "objectives": {}})
        self.assertEqual(d["verdict"], "REQUIRE_APPROVAL")
        self.assertIsNotNone(P.build_plan(d, POLICY))

    def test_no_plan_for_deny_unknown_noop(self):
        for kw in (dict(slo={"burn_rate_1h": 5, "budget_remaining": 0.01}), dict(usage={"valid_days": 3}), dict(request=0.6)):
            d = dec(**kw)
            self.assertIn(d["verdict"], ("DENY", "UNKNOWN", "NOOP"))
            self.assertIsNone(P.build_plan(d, POLICY))
        plans, skipped = P.build_plans([dec(request=0.6)], POLICY)
        self.assertEqual((plans, skipped), ([], []))

    def test_no_limit_means_empty_limits(self):
        d = dec()
        d["current"]["limit"] = None
        self.assertEqual(P.build_plan(d, POLICY)["before"]["limits"], {})

    def test_missing_container_is_skipped_not_guessed(self):
        d = dec()
        d["container"] = None
        plans, skipped = P.build_plans([d], POLICY)
        self.assertEqual(plans, [])
        self.assertEqual(skipped[0]["decision_id"], "K-shop-api-cpu")

    def test_only_filters_to_active_proposals(self):
        d = dec()
        self.assertEqual(P.build_plans([d], POLICY, only=set())[0], [])
        self.assertEqual(len(P.build_plans([d], POLICY, only={d["id"]})[0]), 1)


if __name__ == "__main__":
    unittest.main()
