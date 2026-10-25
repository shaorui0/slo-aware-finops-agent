import unittest

from finops_agent.k8s.packing import free_after, pack, savings

CLUSTER = {"allocatable": {"cpu": 4.0, "memory_gib": 16.0}, "min_nodes": 1}
PRICING = {"node_hour_usd": 0.192, "cpu_core_hour_usd": 0.0316, "mem_gib_hour_usd": 0.0041}


def pod(ns, wl, i, cpu, mem):
    return {"namespace": ns, "workload": wl, "pod": f"{wl}-{i}", "cpu": cpu, "mem": mem}


class PackTest(unittest.TestCase):
    def test_both_resources_must_fit(self):
        # cpu would fit in one node (3+1), memory does not (10+10 > 16)
        pods = [pod("a", "x", 0, 3, 10), pod("a", "y", 0, 1, 10)]
        self.assertEqual(pack(pods, 4.0, 16.0), 2)
        self.assertEqual(pack([pod("a", "x", 0, 3, 5), pod("a", "y", 0, 1, 5)], 4.0, 16.0), 1)

    def test_ffd_beats_naive_order(self):
        # sizes 2,2,1,1,1,1 pack into 2 nodes of 4 cpu only if the large ones go first
        pods = [pod("a", "s", i, 1, 1) for i in range(4)] + [pod("a", "b", i, 2, 1) for i in range(2)]
        self.assertEqual(pack(pods, 4.0, 16.0), 2)

    def test_oversize_pod_gets_own_node_and_min_nodes(self):
        self.assertEqual(pack([pod("a", "x", 0, 6, 1)], 4.0, 16.0), 1)
        self.assertEqual(pack([], 4.0, 16.0, min_nodes=2), 2)

    def test_deterministic_regardless_of_input_order(self):
        pods = [pod("a", "w", i, 1.3, 3) for i in range(7)] + [pod("b", "v", i, 0.7, 5) for i in range(5)]
        a = pack(pods, 4.0, 16.0)
        b = pack(list(reversed(pods)), 4.0, 16.0)
        self.assertEqual(a, b)


class SavingsTest(unittest.TestCase):
    def test_node_count_drops_gives_realizable(self):
        pods = [pod("a", "w", 0, 2, 4), pod("a", "w", 1, 2, 4), pod("a", "o", 0, 3, 4)]
        # before: o(3) + nothing of 2 fits (3+2>4) -> nodes: [o], [w0,w1] -> 2
        # after w->0.5 each: o(3) + 0.5 + 0.5 = 4.0 fits one node
        s = savings(pods, CLUSTER, "a", "w", "cpu", {"request": 2, "replicas": 2}, 0.5, PRICING)
        self.assertEqual((s["nodes_before"], s["nodes_after"]), (2, 1))
        self.assertAlmostEqual(s["realizable_usd_month"], 0.192 * 730, places=6)
        self.assertAlmostEqual(s["request_value_usd_month"], 1.5 * 0.0316 * 730 * 2, places=6)

    def test_packing_trap_realizable_zero_but_request_value_positive(self):
        pods = [pod("a", "w", 0, 2, 4), pod("a", "w", 1, 2, 4), pod("a", "o", 0, 3, 4)]
        s = savings(pods, CLUSTER, "a", "w", "cpu", {"request": 2, "replicas": 2}, 1.5, PRICING)
        self.assertEqual(s["nodes_before"], s["nodes_after"])
        self.assertEqual(s["realizable_usd_month"], 0)
        self.assertGreater(s["request_value_usd_month"], 0)

    def test_memory_unit_price(self):
        pods = [pod("a", "w", 0, 1, 8)]
        s = savings(pods, CLUSTER, "a", "w", "memory", {"request": 8, "replicas": 3}, 4, PRICING)
        self.assertAlmostEqual(s["request_value_usd_month"], 4 * 0.0041 * 730 * 3, places=6)

    def test_free_after(self):
        pods = [pod("a", "w", 0, 2, 4), pod("a", "o", 0, 1, 4)]
        self.assertEqual(free_after(pods, CLUSTER, "a", "w", "cpu", 1.0, 1), 2.0)   # 4 - (1 + 1)


if __name__ == "__main__":
    unittest.main()
