"""The demo cluster must equal the synthetic world's shop/api-steady, so a generated plan's `before` needs no adaptation
(scripts/k8s_demo.sh, ops/k3d/adapt_plan.py). No cluster is touched."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from finops_agent.k8s import act

ROOT = Path(__file__).resolve().parents[2]
FX = Path(__file__).resolve().parent / "fixtures"


def world_api_steady():
    w = yaml.safe_load((ROOT / "testenv/k8s_world.yaml").read_text())
    return next(x for x in w["workloads"] if x["name"] == "api-steady" and x["namespace"] == "shop")


def demo_deployment():
    docs = list(yaml.safe_load_all((ROOT / "ops/k3d/shop.yaml").read_text()))
    return next(d for d in docs if d and d["kind"] == "Deployment" and d["metadata"]["name"] == "api-steady")


class DemoManifestTest(unittest.TestCase):
    def test_manifest_equals_the_worlds_api_steady(self):
        w, dep = world_api_steady(), demo_deployment()
        c = dep["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(dep["metadata"]["namespace"], w["namespace"])
        self.assertEqual(dep["spec"]["replicas"], w["replicas"])
        self.assertEqual(c["name"], w["container"]["name"])
        self.assertEqual(act.norm_res(c["resources"]["requests"]), act.norm_res(w["container"]["requests"]))
        self.assertEqual(act.norm_res(c["resources"]["limits"]), act.norm_res(w["container"]["limits"]))

    def test_generated_plans_match_the_manifest_without_adaptation(self):
        plans = json.loads((FX / "day2/k8s_plans.json").read_text())["plans"]
        dep = demo_deployment()
        c = dep["spec"]["template"]["spec"]["containers"][0]
        live = {"requests": c["resources"]["requests"], "limits": c["resources"]["limits"], "replicas": dep["spec"]["replicas"]}
        mine = [p for p in plans if p["decision_id"] in ("K-shop-api-steady-cpu", "K-shop-api-steady-memory")]
        self.assertEqual(len(mine), 2)
        for p in mine:
            self.assertEqual(act.container_name(p), c["name"])
            self.assertTrue(act.same_resources(live, p["before"], act.touched(p)), p["plan_id"])

    def _run(self, plans_doc, dep):
        with tempfile.TemporaryDirectory() as t:
            pf, df = Path(t) / "k8s_plans.json", Path(t) / "dep.json"
            pf.write_text(json.dumps(plans_doc))
            df.write_text(json.dumps(dep))
            ids = [p["plan_id"] for p in plans_doc["plans"]]
            out = subprocess.run([sys.executable, str(ROOT / "ops/k3d/adapt_plan.py"), str(pf), str(df), *ids],
                                 capture_output=True, text=True, cwd=ROOT)
            return out, json.loads(pf.read_text()), (Path(t) / "k8s_plans.generated.json").exists()

    def _live_dep(self, cpu_req="1", replicas=3):
        d = demo_deployment()
        d["spec"]["replicas"] = replicas
        d["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]["cpu"] = cpu_req
        return d

    def test_adapt_plan_is_silent_when_equal(self):
        doc = json.loads((FX / "day2/k8s_plans.json").read_text())
        doc["plans"] = [p for p in doc["plans"] if p["decision_id"] == "K-shop-api-steady-cpu"]
        out, after, kept = self._run(doc, self._live_dep())
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("no adaptation needed", out.stdout)
        self.assertNotIn("WARNING", out.stdout)
        self.assertEqual(after, doc)
        self.assertFalse(kept)

    def test_adapt_plan_is_loud_and_restores_the_demos_own_value(self):
        doc = json.loads((FX / "day2/k8s_plans.json").read_text())
        doc["plans"] = [p for p in doc["plans"] if p["decision_id"] == "K-shop-api-steady-cpu"]
        out, after, kept = self._run(doc, self._live_dep(cpu_req="500m", replicas=1))
        self.assertIn("THE SYNTHETIC WORLD AND THE DEMO CLUSTER DIFFER", out.stdout)
        self.assertIn("before.requests.cpu: world 1 -> demo 500m", out.stdout)
        self.assertIn("before.replicas: world 3 -> demo 1", out.stdout)
        p = after["plans"][0]
        self.assertEqual((p["before"]["requests"], p["before"]["replicas"]), ({"cpu": "500m"}, 1))
        self.assertIn('"cpu":"500m"', p["rollback_cmd"])
        self.assertEqual(p["patch"], doc["plans"][0]["patch"])      # the change under test is untouched
        self.assertTrue(kept)                                        # the generated plan is kept next to it


if __name__ == "__main__":
    unittest.main()
