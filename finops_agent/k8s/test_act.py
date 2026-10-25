"""Unit tests for finops_agent.k8s.act. subprocess is mocked; no cluster is touched."""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finops_agent.k8s import act

CTX = "k3d-finops-test"
BEFORE = {"requests": {"cpu": "500m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "256Mi"}, "replicas": 1}
PATCH = {"spec": {"template": {"spec": {"containers": [
    {"name": "api", "resources": {"requests": {"cpu": "100m"}, "limits": {"cpu": "200m"}}}]}}}}
PLAN = {
    "plan_id": "P-K-shop-api-steady-cpu-canary", "decision_id": "K-shop-api-steady-cpu", "context": CTX,
    "before": BEFORE, "patch": PATCH,
    "apply_cmd": f"kubectl --context {CTX} -n shop patch deploy api-steady --type strategic -p '{json.dumps(PATCH)}'",
    "rollback_cmd": f"kubectl --context {CTX} -n shop patch deploy api-steady --type strategic -p "
                    "'{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"api\",\"resources\":"
                    "{\"requests\":{\"cpu\":\"500m\"},\"limits\":{\"cpu\":\"1\"}}}]}}}}'",
    "canary": {"scope": "one workload"}, "observe_seconds": 0,
    "stop_conditions": [{"signal": "throttle_ratio", "op": ">", "value": 0.3},
                        {"signal": "p99_seconds", "op": ">", "value": 0.5},
                        {"signal": "oom_killed", "op": ">", "value": 0},
                        {"signal": "restarts", "op": ">", "value": 0}],
    "approval_file": "runs/approvals/P-K-shop-api-steady-cpu-canary.approved",
}


def dep_json(res):
    return json.dumps({"spec": {"replicas": res["replicas"], "selector": {"matchLabels": {"app": "api-steady"}},
                                "template": {"spec": {"containers": [{"name": "api", "resources": {
                                    "requests": res["requests"], "limits": res["limits"]}}]}}}})


AFTER = {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "200m", "memory": "256Mi"}, "replicas": 1}
PODS = json.dumps({"items": [{"metadata": {}, "status": {"containerStatuses": [{"restartCount": 0}]}}]})
OOM_PODS = json.dumps({"items": [{"metadata": {}, "status": {"containerStatuses": [
    {"restartCount": 1, "lastState": {"terminated": {"reason": "OOMKilled"}}}]}}]})


class FakeKube:
    """Stateful stand-in for subprocess.run; records every command string."""

    def __init__(self, live=BEFORE, metrics="cpu_throttle_ratio 0.0\nhttp_request_duration_p99_seconds 0.02\n",
                 pods=PODS, rollout_rc=0):
        self.live, self.metrics, self.pods, self.rollout_rc = copy.deepcopy(live), metrics, pods, rollout_rc
        self.cmds = []

    def __call__(self, argv, **kw):
        cmd = argv[2]
        self.cmds.append(cmd)
        run = lambda out="", rc=0: subprocess.CompletedProcess(argv, rc, out, "")
        if " get deploy " in cmd:
            return run(dep_json(self.live))
        if " get --raw " in cmd:
            return run(self.metrics) if self.metrics is not None else run("", 1)
        if " get pods " in cmd:
            return run(self.pods)
        if " rollout status " in cmd:
            return run("rolled out", self.rollout_rc)
        if " patch " in cmd:
            body = json.loads(cmd.split(" -p ", 1)[1].strip().strip("'") if "'" in cmd else "{}")
            c = body["spec"]["template"]["spec"]["containers"][0]["resources"]
            self.live["requests"].update(c.get("requests", {}))
            self.live["limits"].update(c.get("limits", {}))
            return run("patched")
        return run()

    def writes(self):
        return [c for c in self.cmds if " patch " in c]


class ActTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "runs/approvals").mkdir(parents=True)
        self.actions = self.root / "runs/r1/k8s_actions.jsonl"
        self.plan = copy.deepcopy(PLAN)
        self.sleep = mock.patch.object(act.time, "sleep", lambda s: None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.tmp.cleanup()

    def approve(self):
        (self.root / self.plan["approval_file"]).write_text("ok\n")

    def run_apply(self, kube, plan=None):
        with mock.patch.object(act.subprocess, "run", kube):
            return act.apply_plan(plan or self.plan, self.actions, root=self.root, poll_seconds=0)

    def last_record(self):
        return json.loads(self.actions.read_text().splitlines()[-1])

    # ---- refusal paths: no write may reach the cluster
    def test_wrong_context_refused(self):
        self.approve()
        self.plan["context"] = "k3d-cre6630"
        kube = FakeKube()
        rec = self.run_apply(kube)
        self.assertEqual(rec["outcome"], "refused")
        self.assertIn("context", rec["reason"])
        self.assertEqual(kube.cmds, [])  # not even a read

    def test_missing_approval_refused(self):
        kube = FakeKube()
        rec = self.run_apply(kube)
        self.assertEqual((rec["outcome"], kube.cmds), ("refused", []))
        self.assertIn("approval file missing", rec["reason"])

    def test_approval_name_must_match_plan_id(self):
        (self.root / "runs/approvals/other.approved").write_text("ok")
        self.plan["approval_file"] = "runs/approvals/other.approved"
        rec = self.run_apply(FakeKube())
        self.assertEqual(rec["outcome"], "refused")

    def test_drift_refused(self):
        self.approve()
        drifted = copy.deepcopy(BEFORE)
        drifted["requests"]["cpu"] = "250m"
        kube = FakeKube(live=drifted)
        rec = self.run_apply(kube)
        self.assertEqual(rec["outcome"], "refused")
        self.assertIn("drift", rec["reason"])
        self.assertEqual(kube.writes(), [])

    def test_foreign_context_in_command_refused(self):
        self.approve()
        self.plan["apply_cmd"] = self.plan["apply_cmd"].replace(CTX, "k3d-cre6630")
        kube = FakeKube()
        rec = self.run_apply(kube)
        self.assertEqual(rec["outcome"], "refused")
        self.assertEqual(kube.cmds, [])

    def test_non_kubectl_or_bad_verb_refused(self):
        self.approve()
        for cmd in ("rm -rf /", f"kubectl --context {CTX} delete ns shop"):
            plan = copy.deepcopy(self.plan)
            plan["apply_cmd"] = cmd
            kube = FakeKube()
            self.assertEqual(self.run_apply(kube, plan)["outcome"], "refused", cmd)
            self.assertEqual(kube.cmds, [])

    # ---- happy path
    def test_verified(self):
        self.approve()
        kube = FakeKube()
        rec = self.run_apply(kube)
        self.assertEqual(rec["outcome"], "verified", rec["reason"])
        self.assertEqual(len(kube.writes()), 1)
        self.assertTrue(kube.writes()[0].startswith("# INTENT: "))
        self.assertTrue(rec["executed"].startswith("# INTENT: "))
        outcomes = [json.loads(l)["outcome"] for l in self.actions.read_text().splitlines()]
        self.assertEqual(outcomes, ["applied", "verified"])

    def test_every_kubectl_call_carries_context(self):
        self.approve()
        kube = FakeKube()
        self.run_apply(kube)
        for c in kube.cmds:
            self.assertIn(f"kubectl --context {CTX} ", c)
            self.assertEqual(c.count("--context"), 1)

    # ---- rollback paths
    def _rollback_case(self, **kw):
        self.approve()
        self.plan["observe_seconds"] = 10
        kube = FakeKube(**kw)
        return kube, self.run_apply(kube)

    def test_throttle_triggers_rollback_and_verifies(self):
        kube, rec = self._rollback_case(metrics="cpu_throttle_ratio 0.8\nhttp_request_duration_p99_seconds 0.02\n")
        self.assertEqual(rec["outcome"], "rolled_back", rec["reason"])
        self.assertEqual(rec["stop_condition"]["signal"], "throttle_ratio")
        self.assertEqual(len(kube.writes()), 2)
        self.assertTrue(all(w.startswith("# INTENT: ") for w in kube.writes()))
        self.assertTrue(act.same_resources(kube.live, BEFORE))
        self.assertIn("rollback_executed", rec)

    def test_p99_triggers_rollback(self):
        _, rec = self._rollback_case(metrics="cpu_throttle_ratio 0\nhttp_request_duration_p99_seconds 2.5\n")
        self.assertEqual((rec["outcome"], rec["stop_condition"]["signal"]), ("rolled_back", "p99_seconds"))

    def test_oom_triggers_rollback(self):
        _, rec = self._rollback_case(pods=OOM_PODS)
        self.assertEqual(rec["outcome"], "rolled_back")
        self.assertIn(rec["stop_condition"]["signal"], ("oom_killed", "restarts"))

    def test_metrics_unavailable_fails_safe(self):
        _, rec = self._rollback_case(metrics=None)
        self.assertEqual((rec["outcome"], rec["stop_condition"]["signal"]), ("rolled_back", "metrics_unavailable"))

    def test_rollout_failure_rolls_back(self):
        self.approve()
        kube = FakeKube(rollout_rc=1)
        rec = self.run_apply(kube)
        self.assertIn(rec["outcome"], ("rolled_back", "failed"))
        self.assertEqual(len(kube.writes()), 2)

    def test_rollback_that_does_not_restore_is_failed(self):
        self.approve()
        self.plan["observe_seconds"] = 10
        kube = FakeKube(metrics="cpu_throttle_ratio 0.9\n")
        orig = kube.__call__

        def stuck(argv, **kw):  # rollback patch is a no-op
            if argv[2].count(" patch ") and '"500m"' in argv[2]:
                kube.cmds.append(argv[2])
                return subprocess.CompletedProcess(argv, 0, "", "")
            return orig(argv, **kw)

        rec = self.run_apply(stuck)
        self.assertEqual(rec["outcome"], "failed")

    # ---- helpers
    def test_quantities_compare_semantically(self):
        self.assertEqual(act.norm_quantity("500m"), act.norm_quantity("0.5"))
        self.assertEqual(act.norm_quantity("1Gi"), act.norm_quantity("1024Mi"))
        self.assertNotEqual(act.norm_quantity("1"), act.norm_quantity("1500m"))

    def test_kubectl_refuses_other_context(self):
        with mock.patch.object(act.subprocess, "run") as run:
            with self.assertRaises(act.Refused):
                act.kubectl({"context": "k3d-cre6630"}, ["get", "pods"])
            run.assert_not_called()

    def test_status_and_cli(self):
        self.approve()
        plans = self.root / "runs/r1/k8s_plans.json"
        plans.write_text(json.dumps([self.plan]))
        self.run_apply(FakeKube())
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            act.main(["status", "--plans", str(plans)])
        self.assertIn("verified", buf.getvalue())
        self.assertEqual(act.main(["apply", "--plans", str(plans), "--plan-id", "nope"]), 2)


if __name__ == "__main__":
    unittest.main()
