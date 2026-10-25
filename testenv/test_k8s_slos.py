import unittest
from pathlib import Path

import yaml

from finops_agent.k8s.collect import load_slos

ROOT = Path(__file__).resolve().parent
WORLD = yaml.safe_load((ROOT / "k8s_world.yaml").read_text())
CATALOG = ROOT / "k8s_slos.yaml"


class SloCatalogTest(unittest.TestCase):
    def test_catalog_matches_world_slo_blocks(self):
        cat = load_slos(CATALOG)
        want = {}
        for w in WORLD["workloads"]:
            s = w.get("slo")
            if s:
                obj = {k: s[k] for k in ("availability", "p99_seconds") if k in s}
                want[(w["namespace"], w["name"])] = {"objectives": obj, "deadline_seconds": s.get("deadline_seconds")}
        self.assertEqual(cat, want)

    def test_workload_without_slo_is_absent(self):
        cat = load_slos(CATALOG)
        no_slo = [w for w in WORLD["workloads"] if not w.get("slo")]
        self.assertTrue(no_slo)   # the world keeps at least one (no-slo)
        for w in no_slo:
            self.assertNotIn((w["namespace"], w["name"]), cat)

    def test_shape(self):
        doc = yaml.safe_load(CATALOG.read_text())
        self.assertEqual(doc["kind"], "SLOCatalog")
        for s in doc["slos"]:
            self.assertLessEqual(set(s), {"namespace", "workload", "objectives", "deadline_seconds"})
            self.assertTrue(s.get("objectives") or s.get("deadline_seconds"))

    def test_duplicate_entry_is_an_error(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("slos:\n  - {namespace: a, workload: b, deadline_seconds: 1}\n  - {namespace: a, workload: b, deadline_seconds: 2}\n")
        with self.assertRaises(ValueError):
            load_slos(fh.name)
        Path(fh.name).unlink()


if __name__ == "__main__":
    unittest.main()
