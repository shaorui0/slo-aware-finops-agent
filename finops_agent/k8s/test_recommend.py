import unittest

from finops_agent.k8s.recommend import ceil_to, is_noop, parse_bytes, recommend

POLICY = {"cpu_headroom": 0.3, "cpu_step": 0.05, "mem_headroom": 0.2, "mem_step": "64Mi"}


def facts(resource, **usage):
    return {"resource": resource, "usage": usage}


class RecommendTest(unittest.TestCase):
    def test_cpu_formula_ceil_to_step(self):
        # 0.5 * 1.3 = 0.65 exactly on a step despite float noise
        self.assertEqual(recommend(facts("cpu", p99=0.5), POLICY)["request"], 0.65)
        # 0.52 * 1.3 = 0.676 -> 0.70
        self.assertEqual(recommend(facts("cpu", p99=0.52), POLICY)["request"], 0.7)

    def test_cpu_method_string(self):
        self.assertEqual(recommend(facts("cpu", p99=1.0), POLICY)["method"], "p99*(1+0.3) ceil 0.05")

    def test_memory_formula_gib_and_step(self):
        # max 1.0 GiB * 1.2 = 1.2 GiB -> next 64Mi multiple = 1.25 GiB (20 * 0.0625)
        r = recommend(facts("memory", max=1.0), POLICY)
        self.assertEqual(r["request"], 1.25)
        self.assertEqual(r["headroom"], 0.2)
        # exactly on a step stays: 1.25 / 1.2 -> max 1.0416..; use 2.0 * 1.2 = 2.4 -> 2.4375
        self.assertEqual(recommend(facts("memory", max=2.0), POLICY)["request"], 2.4375)

    def test_missing_usage_gives_none(self):
        self.assertIsNone(recommend(facts("cpu", p99=None), POLICY))
        self.assertIsNone(recommend(facts("memory", max=None), POLICY))

    def test_noop_when_not_below_current(self):
        rec = {"request": 1.0}
        self.assertTrue(is_noop(rec, 1.0))
        self.assertTrue(is_noop(rec, 0.8))
        self.assertFalse(is_noop(rec, 1.05))
        self.assertFalse(is_noop(None, 1.0))

    def test_helpers(self):
        self.assertEqual(parse_bytes("64Mi"), 64 * 1024 ** 2)
        self.assertEqual(ceil_to(0.65000000001, 0.05), 0.65)


if __name__ == "__main__":
    unittest.main()
