import unittest
from datetime import date, timedelta
from fractions import Fraction as F

from finops_agent import verify_savings as v

KEY_G = "gp2_to_gp3|vol-x"


def period(label, start, end, items, findings=(), diff=None):
    """items: {(resource_id, usage_type): (daily_unblended, first_offset, last_offset_exclusive or None, daily_amortized or None)}"""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    lines = {}
    for (rid, ut), spec in items.items():
        daily, a, b, am = (tuple(spec) + (0, None, None))[:4] if len(spec) < 4 else spec
        b = (e - s).days if b is None else b
        am = daily if am is None else am
        lines[(rid, ut)] = {s + timedelta(days=k): (F(daily), F(am)) for k in range(a, b)}
    return v.Period(label, s, e, list(findings), diff or {"new": [], "persisting": [], "resolved": [], "regressed": []}, lines)


def fnd(id_, type_, res, usd, basis="list_price", spend=None):
    return {"id": id_, "type": type_, "resources": res, "monthly_savings_usd": usd, "basis": basis, "resource_spend_usd": spend}


CTRL = {("vol-c", "EBS:VolumeUsage.gp2"): (F(1), 0, None)}      # untouched control volume, $1/day
CONV = fnd("F1", "gp2_to_gp3", ["vol-x"], 20, "list_price", 100)


class GP3(unittest.TestCase):
    """gp2 -> gp3 in period 2, reverted by an unrelated apply in period 3 (the month_after story)."""

    def setUp(self):
        gp2, gp3 = F(100, 30), F(80, 30)
        self.p = [
            period("A", "2026-09-01", "2026-10-01", {("vol-x", "EBS:VolumeUsage.gp2"): (gp2, 0, None), **CTRL}, [CONV]),
            period("B", "2026-10-01", "2026-11-01", {("vol-x", "EBS:VolumeUsage.gp3"): (gp3, 0, None), **CTRL}, [],
                   {"new": [], "persisting": [], "resolved": [{"key": KEY_G}], "regressed": []}),
            period("C", "2026-11-01", "2026-12-01", {("vol-x", "EBS:VolumeUsage.gp2"): (gp2, 0, None), **CTRL}, [CONV],
                   {"new": [], "persisting": [], "resolved": [], "regressed": [{"key": KEY_G, "finding": "F1", "resolved_in": "r2"}]}),
        ]
        self.res = v.analyze(self.p)

    def test_realized_then_lost(self):
        (x,) = self.res["verifications"]
        self.assertEqual(x["now"]["realized_std"], 20)
        self.assertEqual(x["verdict"], "ok")
        self.assertEqual(x["retained_std"], 0)
        self.assertEqual(x["lost_std"], 20)
        self.assertTrue(x["regressed_later"])
        self.assertEqual(x["final"], "兑现后丢失")

    def test_regression_is_priced_in_the_bill(self):
        d = self.res["decompositions"][1]
        self.assertEqual(d["parts"]["regression"], 20)
        self.assertEqual(d["parts"]["verified"], 0)


class Decomposition(unittest.TestCase):
    def test_sums_exactly_to_the_bill_delta(self):
        n, m = F(1, 3), F(7, 11)       # awkward rates: float arithmetic would not close
        a = period("A", "2026-09-01", "2026-10-01", {
            ("vol-o", "EBS:VolumeUsage.gp2"): (n, 0, None), ("vol-x", "EBS:VolumeUsage.gp2"): (m, 0, None),
            ("vol-c", "EBS:VolumeUsage.gp2"): (m, 0, None), (None, "NAT-Bytes"): (n, 0, None),
            ("vol-gone", "EBS:VolumeUsage.gp2"): (n, 0, None)},
            [fnd("F1", "unattached_volume", ["vol-o"], 10, "billed_14d_extrapolated"), CONV])
        b = period("B", "2026-10-01", "2026-11-01", {
            ("vol-x", "EBS:VolumeUsage.gp3"): (m * F(4, 5), 0, None), ("vol-c", "EBS:VolumeUsage.gp2"): (m * F(6, 5), 0, None),
            (None, "NAT-Bytes"): (n * 3, 0, None), ("vol-new", "EBS:VolumeUsage.gp3"): (n, 10, None),
            ("eip-surprise", "PublicIPv4:IdleAddress"): (n, 0, None)},
            [fnd("F9", "unattached_volume", ["vol-new"], 5, "billed_14d_extrapolated")],
            {"new": [{"key": "unattached_volume|vol-new", "finding": "F9"}], "persisting": [],
             "resolved": [{"key": "unattached_volume|vol-o"}, {"key": KEY_G}], "regressed": []})
        res = v.analyze([a, b])
        d = res["decompositions"][0]
        self.assertEqual(sum(p.cost_u for p in a.resources().values()), a.bill_u)
        self.assertEqual(d["sum"], d["delta"])           # Fraction equality, not assertAlmostEqual
        self.assertTrue(d["exact"])
        self.assertEqual(d["delta"], b.bill_u - a.bill_u)
        self.assertEqual(d["parts"]["new_waste"], b.resources()["vol-new"].cost_u)
        self.assertEqual(d["parts"]["other_removed"], -a.resources()["vol-gone"].cost_u)
        self.assertEqual(d["parts"]["other_new"], b.resources()["eip-surprise"].cost_u)


class Confounders(unittest.TestCase):
    def pair(self, growth):
        """Resource x is converted (cost x0.8 on its own). All control volumes' usage moves by `growth`."""
        a = period("A", "2026-09-01", "2026-10-01", {("snap-x", "EBS:SnapshotUsage"): (F(10), 0, None),
                                                      ("snap-c1", "EBS:SnapshotUsage"): (F(10), 0, None),
                                                      ("snap-c2", "EBS:SnapshotUsage"): (F(30), 0, None)},
                   [fnd("F1", "orphan_snapshots", ["snap-x"], 60, "billed")])
        b = period("B", "2026-10-01", "2026-11-01", {("snap-x", "EBS:SnapshotUsage"): (F(10) * F(1) * (1 + growth) * F(1, 2), 0, None),
                                                      ("snap-c1", "EBS:SnapshotUsage"): (F(10) * (1 + growth), 0, None),
                                                      ("snap-c2", "EBS:SnapshotUsage"): (F(30) * (1 + growth), 0, None)},
                   [], {"new": [], "persisting": [], "resolved": [{"key": "orphan_snapshots|snap-x"}], "regressed": []})
        return v.analyze([a, b])["verifications"][0]["now"]

    def test_usage_growth_is_separated_from_the_saving(self):
        # x halves its cost in a stratum whose usage also grows 50%. Two invoices show 10 -> 7.5 = 2.5 saved; against the
        # do-nothing counterfactual (15) the change alone saved 7.5. The adjusted number is the effect of the change only.
        g = F(1, 2)
        now = self.pair(g)
        self.assertEqual(now["drift"]["ebs_snapshot"]["g"], 1 + g)
        self.assertEqual(now["realized_std"], now["cf_std"] - now["act_std"])
        self.assertEqual(now["realized_std"] / now["baseline_std"], F(1, 2) * (1 + g))   # half of what x would have cost
        self.assertNotEqual(now["naive_std"], now["realized_std"])

    def test_usage_decline_does_not_inflate_the_saving(self):
        now = self.pair(F(-1, 2))                  # everything falls 50% on its own; x falls to a quarter
        self.assertEqual(now["naive_std"], now["baseline_std"] * F(3, 4))       # naive: 75% "saved"
        self.assertEqual(now["realized_std"], now["baseline_std"] * F(1, 4))    # adjusted: only the extra 25% is ours
        self.assertLess(now["realized_std"], now["naive_std"])

    def test_no_drift_leaves_naive_and_adjusted_equal(self):
        now = self.pair(F(0))
        self.assertEqual(now["naive_std"], now["realized_std"])


class Proration(unittest.TestCase):
    def test_created_mid_period_then_deleted(self):
        # created on day 8 of a 31-day month at $1/day; deleted before the next 30-day month starts
        a = period("A", "2026-10-01", "2026-11-01", {("vol-n", "EBS:VolumeUsage.gp3"): (F(1), 7, None)},
                   [fnd("F1", "unattached_volume", ["vol-n"], 31, "billed_14d_extrapolated")])
        b = period("B", "2026-11-01", "2026-12-01", {("vol-z", "EBS:VolumeUsage.gp3"): (F(1), 0, None)}, [],
                   {"new": [], "persisting": [], "resolved": [{"key": "unattached_volume|vol-n"}], "regressed": []})
        (x,) = v.analyze([a, b])["verifications"]
        n = x["now"]
        self.assertEqual(n["baseline_std"], 30)            # run-rate, not the 24 days actually billed
        self.assertEqual(n["naive_cal"], 24)               # what the two invoices show
        self.assertEqual(x["predicted"], 30)               # 31-day extrapolation scaled to a standard month
        self.assertEqual(x["verdict"], "ok")
        self.assertIn("proration", [d[0] for d in x["devs"]])

    def test_deleted_mid_period_counts_the_steady_state(self):
        a = period("A", "2026-09-01", "2026-10-01", {("vol-d", "EBS:VolumeUsage.gp2"): (F(2), 0, None)},
                   [fnd("F1", "unattached_volume", ["vol-d"], 60, "billed")])
        b = period("B", "2026-10-01", "2026-11-01", {("vol-d", "EBS:VolumeUsage.gp2"): (F(2), 0, 10)}, [],     # deleted on day 11
                   {"new": [], "persisting": [], "resolved": [{"key": "unattached_volume|vol-d"}], "regressed": []})
        (x,) = v.analyze([a, b])["verifications"]
        self.assertEqual(x["now"]["realized_std"], 60)                  # steady state: fully gone
        self.assertEqual(x["now"]["realized_cal"], 2 * 31 - 20)         # first month cash: 21 days saved
        self.assertIn("proration", [d[0] for d in x["devs"]])


class Commitment(unittest.TestCase):
    def test_amortized_saving_absorbed_by_unused_commitment(self):
        ut = "BoxUsage:m5.large"
        a = period("A", "2026-09-01", "2026-10-01", {("i-1", ut): (F(0), 0, None, F(3)), ("sp-1", "SavingsPlanRecurringFee"): (F(10), 0, None, F(0))},
                   [fnd("F1", "stopped_instance", ["i-1"], 90, "list_price")])
        b = period("B", "2026-10-01", "2026-11-01", {("sp-1", "SavingsPlanRecurringFee"): (F(10), 0, None, F(3))}, [],
                   {"new": [], "persisting": [], "resolved": [{"key": "stopped_instance|i-1"}], "regressed": []})
        (x,) = v.analyze([a, b])["verifications"]
        self.assertEqual(x["now"]["realized_std"], 0)                   # the bill did not move
        self.assertEqual(x["now"]["realized_amort_std"], 90)           # the amortized view says $90
        self.assertEqual(x["verdict"], "none")
        self.assertIn("commitment", [d[0] for d in x["devs"]])


class EndToEnd(unittest.TestCase):
    """Control chain (c0018): real servers and real agent runs on OS-picked ports."""

    @classmethod
    def setUpClass(cls):
        cls.periods = v.collect(0, chain="control")
        cls.res = v.analyze(cls.periods)

    def test_resolved_sets_match_scenario_expectations(self):
        for p in self.periods[1:]:
            self.assertEqual(sorted(e["key"] for e in p.diff["resolved"]), sorted(p.expect["resolved"]))

    def test_gp3_realized_in_october_lost_in_november(self):
        x = next(x for x in self.res["verifications"] if x["type"] == "gp2_to_gp3")
        self.assertEqual(x["verdict"], "ok")
        self.assertAlmostEqual(float(x["now"]["realized_std"]), 20, places=3)
        self.assertAlmostEqual(float(x["retained_std"]), 0, places=3)
        self.assertEqual(x["final"], "兑现后丢失")

    def test_bills_decompose_exactly(self):
        for d in self.res["decompositions"]:
            self.assertTrue(d["exact"])
            self.assertEqual(d["sum"], d["delta"])

    def test_document_is_deterministic(self):
        first, _ = v.build(self.res)
        again = v.analyze(v.collect(0, chain="control"))
        second, _ = v.build(again)
        self.assertEqual(v.to_md(first), v.to_md(second))

    def test_deletions_realize_the_predicted_amount(self):
        for x in self.res["verifications"]:
            if x["type"] in ("unattached_volume", "idle_public_ip"):
                self.assertEqual(x["verdict"], "ok", x["key"])


class Strata(unittest.TestCase):
    """c0021: an unused volume's cost is flat, a data volume's grows; they must not share a control group."""

    def pair(self):
        a = period("A", "2026-09-01", "2026-10-01", {("vol-w", "EBS:VolumeUsage.gp2"): (F(2), 0, None),     # unattached, will be deleted
                                                      ("vol-w2", "EBS:VolumeUsage.gp2"): (F(2), 0, None),    # unattached, stays (waste control)
                                                      ("vol-d", "EBS:VolumeUsage.gp3"): (F(3), 0, None)},    # data volume (active control)
                   [fnd("F1", "unattached_volume", ["vol-w"], 60, "billed"), fnd("F2", "unattached_volume", ["vol-w2"], 60, "billed")])
        b = period("B", "2026-10-01", "2026-11-01", {("vol-w2", "EBS:VolumeUsage.gp2"): (F(2), 0, None),
                                                      ("vol-d", "EBS:VolumeUsage.gp3"): (F(3) * F(13, 10), 0, None)}, [],
                   {"new": [], "persisting": [], "resolved": [{"key": "unattached_volume|vol-w"}], "regressed": []})
        return v.analyze([a, b])["verifications"][0]

    def test_deleted_idle_volume_is_not_scaled_by_data_growth(self):
        x = self.pair()
        self.assertEqual(x["now"]["strata"][0][1], "ebs_volume:waste")
        self.assertEqual(x["now"]["realized_std"], 60)        # not 60 x 1.3
        self.assertEqual(x["primary"], "none")

    def test_thin_stratum_falls_back_to_the_coarse_class(self):
        a = period("A", "2026-09-01", "2026-10-01", {("vol-x", "EBS:VolumeUsage.gp2"): (F(1), 0, None), ("vol-c", "EBS:VolumeUsage.gp3"): (F(1), 0, None)},
                   [fnd("F1", "unattached_volume", ["vol-x"], 30, "billed")])
        b = period("B", "2026-10-01", "2026-11-01", {("vol-c", "EBS:VolumeUsage.gp3"): (F(1), 0, None)}, [],
                   {"new": [], "persisting": [], "resolved": [{"key": "unattached_volume|vol-x"}], "regressed": []})
        (x,) = v.analyze([a, b])["verifications"]
        self.assertEqual(x["now"]["strata"][0][1], "ebs_volume")


class Classification(unittest.TestCase):
    def test_list_price_prediction_carries_the_31_day_month(self):
        a = period("A", "2026-10-01", "2026-11-01", {("vol-x", "EBS:VolumeUsage.gp2"): (F(10), 0, None)}, [fnd("F1", "gp2_to_gp3", ["vol-x"], 62, "list_price")])
        b = period("B", "2026-11-01", "2026-12-01", {("vol-x", "EBS:VolumeUsage.gp3"): (F(8), 0, None)}, [],
                   {"new": [], "persisting": [], "resolved": [{"key": "gp2_to_gp3|vol-x"}], "regressed": []})
        (x,) = v.analyze([a, b])["verifications"]
        self.assertEqual(x["predicted_raw"], 62)
        self.assertEqual(x["predicted"], 60)                      # 62 x 30/31
        self.assertEqual(x["now"]["realized_std"], 60)
        self.assertEqual(x["primary"], "none")

    def test_extra_usage_types_explain_a_list_price_shortfall(self):
        a = period("A", "2026-09-01", "2026-10-01", {("vol-x", "EBS:VolumeUsage.gp2"): (F(10), 0, None)}, [fnd("F1", "gp2_to_gp3", ["vol-x"], 60, "list_price")])
        b = period("B", "2026-10-01", "2026-11-01", {("vol-x", "EBS:VolumeUsage.gp3"): (F(8), 0, None), ("vol-x", "EBS:VolumeP-IOPS.gp3"): (F(1), 0, None),
                                                      ("vol-x", "EBS:VolumeP-Throughput.gp3"): (F(1, 5), 0, None)}, [],
                   {"new": [], "persisting": [], "resolved": [{"key": "gp2_to_gp3|vol-x"}], "regressed": []})
        (x,) = v.analyze([a, b])["verifications"]
        self.assertEqual(x["primary"], "basis")
        self.assertEqual(sorted(x["extras"]), ["EBS:VolumeP-IOPS.gp3", "EBS:VolumeP-Throughput.gp3"])
        self.assertEqual(sum(x["extras"].values()), x["shortfall"])

    def test_mid_period_deletion_has_a_first_month_cash_rate(self):
        a = period("A", "2026-09-01", "2026-10-01", {("vol-d", "EBS:VolumeUsage.gp2"): (F(2), 0, None)}, [fnd("F1", "unattached_volume", ["vol-d"], 60, "billed")])
        b = period("B", "2026-10-01", "2026-11-01", {("vol-d", "EBS:VolumeUsage.gp2"): (F(2), 0, 10)}, [],
                   {"new": [], "persisting": [], "resolved": [{"key": "unattached_volume|vol-d"}], "regressed": []})
        c = period("C", "2026-11-01", "2026-12-01", {("vol-z", "EBS:VolumeUsage.gp2"): (F(1), 0, None)})
        (x,) = v.analyze([a, b, c])["verifications"]
        self.assertEqual(x["std_rate"], 1)
        self.assertEqual(x["cash_rate"], F(21, 31))
        self.assertEqual(x["later_cash_rate"], 1)
        self.assertEqual(x["primary"], "proration")

    def test_earlier_partial_deletion_is_carried_over_in_the_next_bill(self):
        a = period("A", "2026-09-01", "2026-10-01", {("vol-d", "EBS:VolumeUsage.gp2"): (F(2), 0, None), ("vol-k", "EBS:VolumeUsage.gp3"): (F(1), 0, None)},
                   [fnd("F1", "unattached_volume", ["vol-d"], 60, "billed")])
        b = period("B", "2026-10-01", "2026-11-01", {("vol-d", "EBS:VolumeUsage.gp2"): (F(2), 0, 10), ("vol-k", "EBS:VolumeUsage.gp3"): (F(1), 0, None)}, [],
                   {"new": [], "persisting": [], "resolved": [{"key": "unattached_volume|vol-d"}], "regressed": []})
        c = period("C", "2026-11-01", "2026-12-01", {("vol-k", "EBS:VolumeUsage.gp3"): (F(1), 0, None)})
        d = v.analyze([a, b, c])["decompositions"][1]
        self.assertEqual(d["parts"]["carryover"], -20)
        self.assertEqual(d["parts"]["other_removed"], 0)
        self.assertTrue(d["exact"])


class WorldFeatures(unittest.TestCase):
    """c0021 added optional world features (deleted, uptime, gp3 iops/throughput). Base output must not move."""

    def test_base_and_existing_scenarios_are_byte_identical(self):
        import hashlib
        from testenv import world as W
        want = {None: "7e0e3f0d66e14042ad586c413cb807b17c3e7259f7f9bec7ba613ef1473b0e94",
                "testenv/scenarios/next_month.yaml": "2571d950800c51230bb1a2d7fcb39b76b35cf9078a0a126519b5d99621ffdcd3",
                "testenv/scenarios/month_after.yaml": "ef011dc78c1d9467718053cc77b222aecf2aef5ac21b1b4d9a96acd71a53e92a"}
        for path, h in want.items():
            self.assertEqual(hashlib.sha256(repr(W.load(v.ROOT / path if path else None).lines).encode()).hexdigest(), h, path)

    def test_deleted_uptime_and_gp3_extras(self):
        from testenv import world as W
        w1 = W.load(v.ROOT / "testenv/scenarios/verify_month1.yaml")
        s, e = w1.period
        day = lambda rid, ut=None: {l.day: l.unblended for l in w1.lines if l.resource_id == rid and s <= l.day < e and (ut is None or l.usage_type == ut)}
        a = day("vol-0vfya000000000001")
        self.assertEqual(max(a), date(2026, 10, 10))              # billed up to the day before `deleted`
        self.assertNotIn("vol-0vfya000000000001", {x["id"] for x in w1.all_volumes()})               # gone from the API at period end
        self.assertIn("vol-0vfya000000000001", {x["id"] for x in w1.all_volumes(billing=True)})
        dev = day("i-0a1b2c3d4e5f60601", "BoxUsage:m5.2xlarge")
        self.assertAlmostEqual(dev[date(2026, 10, 5)], 0.384 * 24 * 0.3, places=6)
        self.assertAlmostEqual(W.load(v.ROOT / "testenv/scenarios/verify_base.yaml").lines[0].unblended, W.load().lines[0].unblended)   # before `since`: unchanged
        w2 = W.load(v.ROOT / "testenv/scenarios/verify_month2.yaml")
        s2, e2 = w2.period
        ut = {l.usage_type: l.unblended for l in w2.lines if l.resource_id == "vol-0vfyd000000000001" and l.day == s2}
        self.assertAlmostEqual(ut["EBS:VolumeP-IOPS.gp3"], 0.005 * 6000 / 30, places=6)
        self.assertAlmostEqual(ut["EBS:VolumeP-Throughput.gp3"], 0.04 * 125 / 30, places=6)


def _row(res, prefix):
    return next(x for x in res["verifications"] if x["key"].startswith(prefix))


class ScenarioChain(unittest.TestCase):
    """c0021: real servers and real agent runs over testenv/scenarios/verify_*.yaml, OS-picked ports."""

    @classmethod
    def setUpClass(cls):
        cls.periods = v.collect(0, chain="scenario")
        cls.res = v.analyze(cls.periods)
        cls.a = _row(cls.res, "unattached_volume|vol-0vfya")
        cls.b = _row(cls.res, "schedule_dev_but_sp_covered|")
        cls.c = _row(cls.res, "gp2_to_gp3|vol-0vfyc")
        cls.d = _row(cls.res, "gp2_to_gp3|vol-0vfyd")
        cls.e = _row(cls.res, "idle_public_ip|")

    def test_agent_diff_matches_the_scenario_expectations(self):
        for p in self.periods[1:]:
            self.assertEqual(sorted(e["key"] for e in p.diff["resolved"]), sorted(p.expect["resolved"]))
            self.assertEqual(sorted(e["key"] for e in p.diff["new"]), sorted(p.expect["new"]))
        self.assertEqual([e["finding"] for e in self.periods[1].claims_resolved], ["C01"])    # a claim is never in the agent's own diff

    def test_a_mid_period_deletion_is_partial_in_the_first_month_and_full_after(self):
        x = self.a
        self.assertEqual(x["primary"], "proration")
        self.assertAlmostEqual(float(x["std_rate"]), 1.0, places=4)
        self.assertAlmostEqual(float(x["cash_rate"]), 21 / 31, places=4)
        self.assertAlmostEqual(float(x["later_cash_rate"]), 1.0, places=4)
        self.assertEqual(x["final"], "兑现，后续保持")

    def test_b_dev_hours_are_absorbed_by_the_savings_plan(self):
        x = self.b
        self.assertGreater(x["predicted"], 500)
        self.assertEqual(x["now"]["realized_std"], 0)
        self.assertEqual(x["verdict"], "none")
        self.assertEqual(x["primary"], "commitment")
        self.assertGreater(x["now"]["realized_amort_std"], 400)
        self.assertEqual(x["absorbed"], x["now"]["realized_amort_std"])
        d = self.res["decompositions"][0]
        self.assertEqual(d["bill1"] - d["bill0"], d["sum"])        # the whole bill still closes with C01 inside

    def test_b_the_unused_commitment_grows_by_the_absorbed_amount(self):
        p0, p1 = self.periods[0], self.periods[1]
        grew = v.STD * (p1.resources()["sp-ec2-m5-01"].rate("a") - p0.resources()["sp-ec2-m5-01"].rate("a"))
        self.assertLess(abs(grew - self.b["absorbed"]), F(1, 100))

    def test_c_growth_hides_the_saving_from_the_naive_comparison(self):
        x = self.c
        self.assertEqual(x["primary"], "drift")
        self.assertLess(x["now"]["naive_std"], 0)
        self.assertGreater(x["now"]["realized_std"], x["predicted"])
        self.assertAlmostEqual(float(x["now"]["strata"][0][2]), 1.3, places=4)
        self.assertAlmostEqual(float(x["now"]["realized_std"]), 52, places=3)

    def test_d_list_price_prediction_misses_iops_and_throughput(self):
        x = self.d
        self.assertEqual(x["primary"], "basis")
        self.assertEqual(x["verdict"], "short")
        self.assertAlmostEqual(float(x["predicted"]), 60, places=3)
        self.assertAlmostEqual(float(x["now"]["realized_std"]), 25, places=3)
        self.assertEqual(sorted(x["extras"]), ["EBS:VolumeP-IOPS.gp3", "EBS:VolumeP-Throughput.gp3"])
        self.assertLess(abs(sum(x["extras"].values()) - x["shortfall"]), F(1, 100))

    def test_e_a_clean_deletion_has_no_deviation(self):
        self.assertEqual(self.e["primary"], "none")
        self.assertEqual(self.e["verdict"], "ok")

    def test_portfolio_realization_is_below_one_and_the_bill_decomposes_exactly(self):
        pred = sum(x["predicted"] for x in self.res["verifications"])
        real = sum(x["now"]["realized_std"] for x in self.res["verifications"])
        self.assertLess(real / pred, F(1, 2))
        for d in self.res["decompositions"]:
            self.assertTrue(d["exact"])
            self.assertEqual(d["sum"], d["delta"])

    def test_the_document_is_deterministic_and_all_checks_pass(self):
        res_c = v.analyze(v.collect(0, chain="control"))
        blocks, ca, cc = v.build_all(self.res, res_c)
        self.assertTrue(all(ok for _c, ok in ca["checks"] + cc["checks"]), [c for c, ok in ca["checks"] + cc["checks"] if not ok])
        again_s = v.analyze(v.collect(0, chain="scenario"))
        again_c = v.analyze(v.collect(0, chain="control"))
        blocks2, _, _ = v.build_all(again_s, again_c)
        self.assertEqual(v.to_md(blocks), v.to_md(blocks2))


if __name__ == "__main__":
    unittest.main()
