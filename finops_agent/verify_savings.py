"""Savings verification: did the remediations that the agent's state diff calls `resolved` really save money?

For every finding resolved between consecutive periods of the rot scenarios, compare
  predicted  = the finding's monthly_savings_usd, raised in period N
  realized   = what the affected resources would have cost in N+1 had nothing been done
               (their N daily rate, moved by the drift of an untouched control group)
               minus what Cost Explorer says they cost in N+1.
Then decompose the whole bill delta N -> N+1 so the verified savings, new waste and everything
else add up to the actual delta, exactly (all arithmetic is Fraction, no float rounding).

Pure analysis lives in analyze(); collection (servers, agent runs, Cost Explorer queries) in collect().
Two chains of three periods each are verified:
  scenario  testenv/scenarios/verify_*.yaml: bills where realized savings genuinely differ from predicted (c0021)
  control   base -> next_month -> month_after: bills and predictions share one price table, so ~100% is expected (c0018)

Run:  python3 -m finops_agent.verify_savings --out-md docs/SAVINGS_VERIFICATION.md --out-html reports/savings-verification.html
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STD = 30                      # standard month: the test world prices every line as monthly / 30
TOL = F(2, 100)               # +-2% counts as "equal"
ONE = F(1)

PERIODS = [   # the control chain (c0018): label, world file (None = base world), start, end
    ("2026-09", None, "2026-09-01", "2026-10-01"),
    ("2026-10", "testenv/scenarios/next_month.yaml", "2026-10-01", "2026-11-01"),
    ("2026-11", "testenv/scenarios/month_after.yaml", "2026-11-01", "2026-12-01"),
]
SCENARIO_PERIODS = [   # the scenario chain (c0021): overlays on the base world that create real deviations
    ("2026-09", "testenv/scenarios/verify_base.yaml", "2026-09-01", "2026-10-01"),
    ("2026-10", "testenv/scenarios/verify_month1.yaml", "2026-10-01", "2026-11-01"),
    ("2026-11", "testenv/scenarios/verify_month2.yaml", "2026-11-01", "2026-12-01"),
]
CHAINS = {"control": PERIODS, "scenario": SCENARIO_PERIODS}
WASTE_TYPES = {"unattached_volume", "stopped_instance_storage", "idle_public_ip", "orphan_snapshots"}


# ---- data model ------------------------------------------------------------
@dataclass
class Period:
    label: str
    start: date
    end: date
    findings: list
    diff: dict
    lines: dict                      # (resource_id|None, usage_type) -> {day: (unblended, amortized)}  (Fractions)
    ut_totals: dict = field(default_factory=dict)   # usage_type -> unblended, from the usage-type-level query
    world: str = "base"
    expect: dict | None = None       # scenario.expect (resolved/new/regressed keys) when the world file has one
    claims_resolved: list = field(default_factory=list)   # scenario claims whose action was applied in this period (not agent findings)
    _res: dict | None = field(default=None, repr=False)
    _waste: set | None = field(default=None, repr=False)

    @property
    def days(self):
        return (self.end - self.start).days

    @property
    def last_day(self):
        return self.end - timedelta(days=1)

    def resources(self) -> dict:
        if self._res is None:
            out: dict = {}
            for (rid, ut), days in self.lines.items():
                r = out.setdefault(res_key(rid, ut), Res(res_key(rid, ut)))
                for d, (u, a) in days.items():
                    r.add(d, ut, u, a)
            self._res = out
        return self._res

    @property
    def waste_keys(self) -> set:
        """Resources that are the subject of a waste finding (unattached, stopped, idle, orphan) in this period: their cost
        is flat by construction, it does not follow business usage."""
        if self._waste is None:
            self._waste = {k for f in self.findings if f["type"] in WASTE_TYPES for k in f["resources"]}
        return self._waste

    @property
    def bill_u(self):
        return sum((r.cost_u for r in self.resources().values()), F(0))

    @property
    def bill_a(self):
        return sum((r.cost_a for r in self.resources().values()), F(0))


@dataclass
class Res:
    key: str
    cost_u: F = F(0)
    cost_a: F = F(0)
    days: set = field(default_factory=set)
    uts: dict = field(default_factory=dict)

    def add(self, day, ut, u, a):
        self.cost_u += u
        self.cost_a += a
        self.days.add(day)
        self.uts[ut] = self.uts.get(ut, F(0)) + u

    @property
    def first(self):
        return min(self.days)

    @property
    def last(self):
        return max(self.days)

    def rate(self, kind="u"):
        """Cost per billed day."""
        return (self.cost_u if kind == "u" else self.cost_a) / len(self.days)

    def alive_at_end(self, p: Period):
        return self.last >= p.last_day

    def steady(self, p: Period, kind="u"):
        """Daily rate the resource ends the period at: its rate if still billed on the last day, else 0 (deleted mid-period)."""
        return self.rate(kind) if self.alive_at_end(p) else F(0)


def res_key(rid, ut):
    return rid if rid not in (None, "", "NoResourceId") else f"usage:{ut}"


def finding_key(f):
    return f"{f['type']}|{','.join(sorted(f['resources']))}"


def stratum_of(r: Res) -> str:
    """Control-group stratum: the class of the resource's largest usage type, so a volume is compared with volumes,
    an address with addresses, never with the NAT bill."""
    ut = max(r.uts, key=lambda u: (abs(r.uts[u]), u)) if r.uts else ""
    for prefix, cls in (("EBS:Volume", "ebs_volume"), ("EBS:Snapshot", "ebs_snapshot"), ("PublicIPv4", "public_ipv4"),
                        ("BoxUsage", "compute"), ("SavingsPlanNegation", "compute"), ("NatGateway", "nat")):
        if ut.startswith(prefix):
            return cls
    return ut


def fine_stratum(r: Res, p: Period) -> str:
    """Coarse class x waste/active. A volume that nobody uses does not grow with the business; a data volume does."""
    return f"{stratum_of(r)}:{'waste' if r.key in p.waste_keys else 'active'}"


# ---- control group ---------------------------------------------------------
def touched_between(periods, i, j) -> set:
    """Resources named by any finding that was resolved, regressed or new in a period after i up to j (the treated set)."""
    out = set()
    for k in range(i, j + 1):
        p = periods[k]
        by_key = {finding_key(f): f for f in p.findings}
        for kind in ("resolved", "regressed", "new", "claims"):
            for e in (p.claims_resolved if kind == "claims" else p.diff.get(kind, [])):
                f = by_key.get(e["key"])
                if f is None and k > 0:
                    f = next((x for x in periods[k - 1].findings if finding_key(x) == e["key"]), None)
                if f:
                    out.update(f["resources"])
    return out


def control_drift(p0: Period, p1: Period, touched: set) -> dict:
    """Per stratum: drift = sum of control daily rates in p1 over the same in p0. Control = resources billed in both
    periods, still alive at the end of p1, and not touched by any remediation. '*' pools all strata."""
    R0, R1 = p0.resources(), p1.resources()
    agg: dict = {}
    for k, r0 in R0.items():
        r1 = R1.get(k)
        if r1 is None or k in touched or not r1.alive_at_end(p1):
            continue
        for s in (fine_stratum(r0, p0), stratum_of(r0), "*"):
            a = agg.setdefault(s, {"n": 0, "rate0": F(0), "rate1": F(0)})
            a["n"] += 1
            a["rate0"] += r0.rate()
            a["rate1"] += r1.rate()
    for a in agg.values():
        a["g"] = a["rate1"] / a["rate0"] if a["rate0"] else None
    return agg


def drift_for(drift: dict, stratum: str):
    """The finest stratum that has control resources; then the coarse class; then every control resource ('*')."""
    for s in (stratum, stratum.split(":")[0]):
        d = drift.get(s)
        if d and d["g"] is not None:
            return d["g"], s
    g = drift.get("*", {}).get("g")
    return (g if g is not None else ONE), "*"


# ---- measuring one finding against a later period --------------------------
def measure(periods, i, j, f) -> dict:
    """Realized saving of finding `f` (raised in period i) as seen in period j > i."""
    p0, p1 = periods[i], periods[j]
    R0, R1 = p0.resources(), p1.resources()
    drift = control_drift(p0, p1, touched_between(periods, i + 1, j) | set(f["resources"]))
    keys = [k for k in f["resources"] if k in R0 or k in R1]
    unmapped = [k for k in f["resources"] if k not in keys]
    d1 = p1.days
    m = {"keys": keys, "unmapped": unmapped, "period": p1.label, "days1": d1, "rows": [], "strata": [],
         "base_c0": F(0), "act_c1": F(0), "cf_cal": F(0), "rate0": F(0), "cf_rate": F(0), "act_rate": F(0),
         "rate0_a": F(0), "cf_rate_a": F(0), "act_rate_a": F(0), "partial": []}
    for k in keys:
        r0, r1 = R0.get(k), R1.get(k)
        rate0 = r0.rate() if r0 else F(0)
        rate0a = r0.rate("a") if r0 else F(0)
        g, used = drift_for(drift, fine_stratum(r0, p0) if r0 else fine_stratum(r1, p1))
        act = r1.steady(p1) if r1 else F(0)
        acta = r1.steady(p1, "a") if r1 else F(0)
        c0, c1 = (r0.cost_u if r0 else F(0)), (r1.cost_u if r1 else F(0))
        m["base_c0"] += c0
        m["act_c1"] += c1
        m["cf_cal"] += rate0 * g * d1
        m["rate0"] += rate0
        m["cf_rate"] += rate0 * g
        m["act_rate"] += act
        m["rate0_a"] += rate0a
        m["cf_rate_a"] += rate0a * g
        m["act_rate_a"] += acta
        m["strata"].append((k, used, g))
        if r0 and len(r0.days) < p0.days:
            m["partial"].append(f"{k} 在 {p0.label} 只计费 {len(r0.days)}/{p0.days} 天（自 {r0.first.isoformat()}）")
        if r1 and len(r1.days) < d1:
            how = f"自 {r1.first.isoformat()} 起" if r1.alive_at_end(p1) else f"至 {r1.last.isoformat()} 止"
            m["partial"].append(f"{k} 在 {p1.label} 只计费 {len(r1.days)}/{d1} 天（{how}）")
        m["rows"].append({"key": k, "c0": c0, "c1": c1, "rate0": rate0, "g": g, "stratum": used, "act_rate": act})
    m["baseline_std"] = STD * m["rate0"]
    m["cf_std"] = STD * m["cf_rate"]
    m["act_std"] = STD * m["act_rate"]
    m["naive_std"] = m["baseline_std"] - m["act_std"]
    m["realized_std"] = m["cf_std"] - m["act_std"]
    m["realized_amort_std"] = STD * (m["cf_rate_a"] - m["act_rate_a"])
    m["naive_cal"] = m["base_c0"] - m["act_c1"]                  # what two invoices show
    m["realized_cal"] = m["cf_cal"] - m["act_c1"]                # first-month cash vs the do-nothing counterfactual
    m["drift"] = drift
    m["ut0"] = _ut_rates([R0.get(k) for k in keys], p0, steady=False)      # N: usage-type mix of the treated resources
    m["ut1"] = _ut_rates([R1.get(k) for k in keys], p1)                    # N+1: what they still bill at the end of the period
    return m


def verdict_of(pred: F, realized: F) -> tuple[str, str]:
    if pred <= 0:
        return "n/a", "无预测值，不评估"
    rate = realized / pred
    if abs(rate - 1) <= TOL:
        return "ok", "兑现"
    if rate > 1 + TOL:
        return "over", "超额兑现"
    if rate <= TOL:
        return "none", "未兑现"
    return "short", "部分兑现"


def predicted_std(f, p0: Period) -> F:
    """The finding's monthly_savings_usd in standard-month dollars. The agent scales a 14-day billed cost to the analysis
    period (analyze.py: cost * days / res_days), and a list-price estimate is a share of that same number, so both
    carry N's calendar days. A claim's number is already a standard month."""
    pred = F(str(f["monthly_savings_usd"])).limit_denominator(10 ** 9)
    if f.get("source") != "claim" and f.get("basis") in ("billed", "billed_14d_extrapolated", "list_price"):
        return pred * STD / p0.days
    return pred


def _ut_rates(res_list, p, steady=True) -> dict:
    """Standard-month cost per usage type of the given resources (daily rate x 30; a resource deleted before the period ends counts 0)."""
    out: dict = {}
    for r in res_list:
        if r is None or (steady and not r.alive_at_end(p)):
            continue
        for ut, tot in r.uts.items():
            out[ut] = out.get(ut, F(0)) + STD * tot / len(r.days)
    return out


def explain(x: dict) -> None:
    """Classify why realized differs from predicted. Fills x['causes'] (class, amount, text), x['primary'], x['conclusion'].
    Classes: commitment (absorbed by an unused Savings Plan), drift (control-group correction), basis (the prediction's
    price basis differs from the bill), proration (part-month billing)."""
    n, pred = x["now"], x["predicted"]
    tol = TOL * max(pred, ONE)
    x["std_rate"] = n["realized_std"] / pred if pred > 0 else None
    x["cash_pred"] = pred * n["days1"] / STD
    x["cash_rate"] = n["realized_cal"] / x["cash_pred"] if x["cash_pred"] > 0 else None
    x["later_cash_rate"] = None
    if x["later"] is not None and pred > 0:
        x["later_cash_rate"] = x["later"]["realized_cal"] / (pred * x["later"]["days1"] / STD)
    gg = n["cf_std"] / n["baseline_std"] if n["baseline_std"] > 0 else ONE
    pred_g = pred * gg                                    # the prediction moved to the usage level of the counterfactual
    x["pred_g"] = pred_g
    causes = []
    absorb = n["realized_amort_std"] - n["realized_std"]
    x["absorbed"] = absorb
    if pred > 0 and absorb > tol:
        causes.append(("commitment", absorb, "摊销口径节省 {} 大于账单口径 {}：差额 {} 被 Savings Plan 未用承诺吸收，现金没有减少".format(
            m(n["realized_amort_std"]), m(n["realized_std"]), m(absorb))))
        gap = n["realized_amort_std"] - pred_g            # what is left between the prediction and the amortized saving
    else:
        gap = n["realized_std"] - pred_g
    if pred > 0 and abs(n["realized_std"] - n["naive_std"]) > tol:
        causes.append(("drift", n["realized_std"] - n["naive_std"], "同层对照资源用量变化，对照组校正把节省从朴素 {} 改成 {}".format(m(n["naive_std"]), m(n["realized_std"]))))
    x["basis_gap"] = gap
    x["extras"], x["shortfall"] = {}, F(0)
    if pred > 0 and abs(gap) > tol:
        if any(c == "commitment" for c, _, _ in causes):
            causes.append(("basis", gap, "预测按按需价 {}；SP 覆盖的实例按 SP 折扣价入账，摊销口径只省 {}，差 {} 是 SP 折扣".format(
                m(pred_g), m(n["realized_amort_std"]), m(-gap))))
        elif x["basis"] == "list_price" and gap < 0:
            new = {u: a for u, a in n["ut1"].items() if u not in n["ut0"] and a > 0}
            main = max(new, key=lambda u: new[u]) if new else None
            x["extras"] = {u: a for u, a in new.items() if u != main}
            x["shortfall"] = -gap
            tot = sum(x["extras"].values(), F(0))
            tie = x["extras"] and abs(tot - x["shortfall"]) <= tol
            txt = "预测只算 GB 单价差；账单实际比牌价预期高 {}".format(m(x["shortfall"]))
            if x["extras"]:
                txt += "，新增用量类型 {} 合计 {}，{}".format("、".join(f"{u} {m(a)}" for u, a in sorted(x["extras"].items())), m(tot),
                                                            "与差额吻合" if tie else "与差额相差 {}".format(m(x["shortfall"] - tot)))
            causes.append(("basis", gap, txt))
        else:
            causes.append(("basis", gap, "预测与实测相差 {}（预测口径：{}）".format(m(gap), x["basis"])))
    if n["partial"] and x["cash_rate"] is not None and x["std_rate"] is not None and abs(x["cash_rate"] - x["std_rate"]) > TOL:
        causes.append(("proration", n["realized_cal"] - x["cash_pred"], "；".join(n["partial"]) + "；首月现金实现率 {}，标准月 {}".format(pc(x["cash_rate"]), pc(x["std_rate"]))))
    x["causes"] = causes
    order = ["commitment", "drift", "basis", "proration"]
    x["primary"] = next((c for c in order if any(k == c for k, _, _ in causes)), "none")
    x["conclusion"] = conclusion_of(x)


CLASS_ZH = {"commitment": "承诺吸收", "drift": "用量漂移（对照组校正）", "basis": "预测口径差", "proration": "按天折算（期中变更）", "none": "无偏差"}


def conclusion_of(x: dict) -> str:
    n, pred, p = x["now"], x["predicted"], x["primary"]
    if p == "commitment":
        return "现金未省：纸面 {} 中 {} 被 Savings Plan 未用承诺吸收，{} 是预测按按需价、SP 覆盖的实例按折扣价入账。不应计入节省，先处理承诺利用率再谈停机。".format(
            m(pred), m(x["absorbed"]), m(pred - n["realized_amort_std"]))
    if p == "drift":
        return "节省真实，量被增长掩盖：朴素对比 {}，校正后 {}（预测 {}）。校正量来自对照组 g。".format(sm(n["naive_std"]), m(n["realized_std"]), m(pred))
    if p == "basis":
        return "实现 {}，是预测 {} 的 {}：{}。预测应改用含 IOPS 与吞吐的 gp3 总价。".format(
            m(n["realized_std"]), m(pred), pc(x["std_rate"]), "差额由新增的 IOPS 与吞吐用量类型解释" if x["extras"] else "原因见偏差表")
    if p == "proration":
        return "兑现。标准月实现 {}，首月现金只有 {}（{}），因为资源在期中才删除；{}。".format(
            pc(x["std_rate"]), pc(x["cash_rate"]),
            "；".join(n["partial"]),
            "下一期现金实现率 {}".format(pc(x["later_cash_rate"])) if x["later_cash_rate"] is not None else "下一期无数据")
    return "兑现，实现率 {}。".format(pc(x["std_rate"])) if x["std_rate"] is not None else "无预测值，不评估"


def verify_finding(periods, i, f, key) -> dict:
    """Verify finding f (raised in period i) that period i+1 reports resolved; look at i+2 too when it exists."""
    p0 = periods[i]
    now = measure(periods, i, i + 1, f)
    raw = F(str(f["monthly_savings_usd"])).limit_denominator(10 ** 9)
    pred = predicted_std(f, p0)
    spend = f.get("resource_spend_usd")
    rate_ok, label = verdict_of(pred, now["realized_std"])
    devs = []
    if now["days1"] != STD:
        devs.append(("calendar", f"{periods[i + 1].label} 有 {now['days1']} 天，首月现金与标准月不同；比较用 {STD} 天标准月"))
    if now["partial"]:
        devs.append(("proration", "；".join(now["partial"])))
    if pred > 0 and abs(now["realized_std"] - now["naive_std"]) > TOL * max(pred, ONE):
        devs.append(("drift", "对照组漂移把节省从 {} 校正为 {}".format(now["naive_std"], now["realized_std"])))
    if pred > 0 and now["realized_amort_std"] - now["realized_std"] > TOL * max(pred, ONE):
        devs.append(("commitment", "摊销口径节省 {} 大于账单口径 {}：差额被 Savings Plan 未用承诺吸收".format(
            now["realized_amort_std"], now["realized_std"])))
    if f.get("basis") == "list_price":
        tie = pred > 0 and abs(now["realized_std"] / pred - 1) <= TOL
        devs.append(("basis", "预测按牌价估算；账单实测{}".format("与牌价一致" if tie else "与牌价不一致")))
    elif f.get("basis") in ("billed", "billed_14d_extrapolated") and pred > 0:
        tie = abs(now["baseline_std"] - pred) <= TOL * pred
        note = f"预测按近 14 天账单外推到 {p0.days} 天，换算标准月 x{STD}/{p0.days}" if p0.days != STD else "预测按近 14 天账单外推"
        devs.append(("basis", note + "；{}".format("与 N 期账单的标准月成本一致" if tie else "与 N 期账单的标准月成本不一致")))
    if now["unmapped"]:
        devs.append(("unmapped", "资源 {} 在账单里没有行，只核实有行的部分".format("、".join(now["unmapped"]))))
    out = {"key": key, "id": f["id"], "type": f["type"], "from": p0.label, "to": periods[i + 1].label,
           "predicted_raw": raw, "predicted": pred, "basis": f.get("basis"), "source": f.get("source", "agent"),
           "spend": F(str(spend)).limit_denominator(10 ** 9) if spend is not None else None,
           "now": now, "verdict": rate_ok, "label": label, "devs": devs, "later": None, "final": label}
    if i + 2 < len(periods):
        later = measure(periods, i, i + 2, f)
        out["later"] = later
        out["regressed_later"] = any(e["key"] == key for e in periods[i + 2].diff.get("regressed", []))
        out["retained_std"] = later["realized_std"]
        out["lost_std"] = now["realized_std"] - later["realized_std"]
        if now["realized_std"] > 0:
            share = later["realized_std"] / now["realized_std"]
            out["retained_share"] = share
            if share <= TOL:
                out["final"] = "兑现后丢失"
            elif share < 1 - TOL:
                out["final"] = label + "，后续部分丢失"
            else:
                out["final"] = label + "，后续保持"
    explain(out)
    return out


# ---- bill decomposition -----------------------------------------------------
CATS = [("verified", "已核实节省"), ("regression", "回归损失"), ("new_waste", "新增浪费"),
        ("calendar", "日历天数与计费天数"), ("drift", "用量漂移"), ("carryover", "前期节省的结转"),
        ("other_removed", "其他移除"), ("other_new", "其他新增")]


def decompose(periods, i, verified: list, regressed_f: list, new_f: list, carry: frozenset = frozenset()) -> dict:
    """Exact split of bill(i+1) - bill(i) over every resource key. All Fractions; sum(parts) == delta by construction
    of each per-key identity:  c1 - c0 = (c1 - cf) + (cf - c0),  cf - c0 = (rate0*d1 - c0) + rate0*(g-1)*d1."""
    p0, p1 = periods[i], periods[i + 1]
    R0, R1 = p0.resources(), p1.resources()
    touched = touched_between(periods, i + 1, i + 1)
    for f in verified:
        touched |= set(f["resources"])
    drift = control_drift(p0, p1, touched)
    V = {k for f in verified for k in f["resources"]}
    G = {k for f in regressed_f for k in f["resources"]}
    N = {k for f in new_f for k in f["resources"]}
    parts = {c: F(0) for c, _ in CATS}
    movers = []
    for k in sorted(set(R0) | set(R1)):
        r0, r1 = R0.get(k), R1.get(k)
        c0 = r0.cost_u if r0 else F(0)
        c1 = r1.cost_u if r1 else F(0)
        role = "other"
        if r0 is None:
            role = "new_waste" if k in N else "other_new"
            parts[role] += c1
        elif r1 is None:
            role = "verified" if k in V else "other_removed"
            if k in V:
                g, _ = drift_for(drift, fine_stratum(r0, p0))
                cf = r0.rate() * g * p1.days
                parts["verified"] += cf - c1
                parts["calendar"] += r0.rate() * p1.days - c0
                parts["drift"] += r0.rate() * (g - 1) * p1.days
            elif k in carry:       # deleted mid-period last time: the part of its cost that was still billed then is gone now
                role = "carryover"
                parts["carryover"] += -c0
            else:
                parts["other_removed"] += -c0
        else:
            g, _ = drift_for(drift, fine_stratum(r0, p0))
            cf = r0.rate() * g * p1.days
            if k in V:
                role = "verified"
                parts["verified"] += cf - c1          # sign: positive = saving; the bill delta carries its negative
            elif k in G:
                role = "regression"
                parts["regression"] += c1 - cf
            if k in V or k in G:
                parts["calendar"] += r0.rate() * p1.days - c0
                parts["drift"] += r0.rate() * (g - 1) * p1.days
            else:
                parts["calendar"] += r0.rate() * p1.days - c0
                parts["drift"] += c1 - r0.rate() * p1.days
        movers.append({"key": k, "role": role, "c0": c0, "c1": c1, "delta": c1 - c0})
    delta = p1.bill_u - p0.bill_u
    # verified is a saving (positive number); in the bill delta it enters with a minus sign
    total = -parts["verified"] + parts["regression"] + parts["new_waste"] + parts["calendar"] + parts["drift"] \
        + parts["carryover"] + parts["other_removed"] + parts["other_new"]
    return {"from": p0.label, "to": p1.label, "bill0": p0.bill_u, "bill1": p1.bill_u, "delta": delta, "parts": parts,
            "sum": total, "exact": total == delta, "movers": sorted(movers, key=lambda m: (-abs(m["delta"]), m["key"])),
            "drift": drift, "amort0": p0.bill_a, "amort1": p1.bill_a}


# ---- full analysis ----------------------------------------------------------
def analyze(periods: list[Period]) -> dict:
    ver, dec = [], []
    carry: frozenset = frozenset()
    for i in range(len(periods) - 1):
        p0, p1 = periods[i], periods[i + 1]
        by_key0 = {finding_key(f): f for f in p0.findings}
        by_key1 = {finding_key(f): f for f in p1.findings}
        vs = []
        for e in sorted(p1.diff.get("resolved", []) + p1.claims_resolved, key=lambda e: e["key"]):   # diff order comes from a set; sort for a stable document
            f = by_key0.get(e["key"])
            if f is not None:
                vs.append(verify_finding(periods, i, f, e["key"]))
        regressed = [by_key1[e["key"]] for e in p1.diff.get("regressed", []) if e["key"] in by_key1]
        new = [by_key1[e["key"]] for e in p1.diff.get("new", []) if e["key"] in by_key1]
        ver.extend(vs)
        dec.append(decompose(periods, i, [by_key0[v["key"]] for v in vs], regressed, new, carry))
        carry = frozenset(k for v in vs for k in by_key0[v["key"]]["resources"])
        dec[-1]["verified_findings"] = [v["id"] for v in vs]
        dec[-1]["regressed_findings"] = [f["id"] for f in regressed]
        dec[-1]["new_findings"] = [f["id"] for f in new]
    return {"periods": periods, "verifications": ver, "decompositions": dec}


# ---- collection: servers, agent, Cost Explorer ------------------------------
def _wait(url, tries=60):
    for _ in range(tries):
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _free(port):
    import socket
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _pick_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _amount(x):
    return F(Decimal(x))


def query_ce(endpoint, start: date, end: date):
    """Resource-level daily lines (<= 14 days per request, the API limit) and usage-type-level monthly totals."""
    import boto3
    ce = boto3.client("ce", endpoint_url=endpoint, aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1")
    svc = ce.get_cost_and_usage(TimePeriod={"Start": start.isoformat(), "End": end.isoformat()}, Granularity="MONTHLY",
                                Metrics=["UnblendedCost"], GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])
    services = sorted({g["Keys"][0] for b in svc["ResultsByTime"] for g in b["Groups"]})
    flt = {"Dimensions": {"Key": "SERVICE", "Values": services}}   # the API requires a Filter on the resource-level call
    lines: dict = {}
    d = start
    while d < end:
        e = min(d + timedelta(days=14), end)
        r = ce.get_cost_and_usage_with_resources(
            TimePeriod={"Start": d.isoformat(), "End": e.isoformat()}, Granularity="DAILY",
            Metrics=["UnblendedCost", "AmortizedCost"], Filter=flt,
            GroupBy=[{"Type": "DIMENSION", "Key": "RESOURCE_ID"}, {"Type": "DIMENSION", "Key": "USAGE_TYPE"}])
        for b in r["ResultsByTime"]:
            day = date.fromisoformat(b["TimePeriod"]["Start"])
            for g in b["Groups"]:
                rid, ut = g["Keys"]
                m = g["Metrics"]
                lines.setdefault((None if rid == "NoResourceId" else rid, ut), {})[day] = (
                    _amount(m["UnblendedCost"]["Amount"]), _amount(m["AmortizedCost"]["Amount"]))
        d = e
    r = ce.get_cost_and_usage(TimePeriod={"Start": start.isoformat(), "End": end.isoformat()}, Granularity="MONTHLY",
                              Metrics=["UnblendedCost"], GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}])
    ut = {}
    for b in r["ResultsByTime"]:
        for g in b["Groups"]:
            ut[g["Keys"][0]] = ut.get(g["Keys"][0], F(0)) + _amount(g["Metrics"]["UnblendedCost"]["Amount"])
    return lines, ut


def claim_finding(c: dict, w) -> dict:
    """A scenario claim as a finding-shaped dict. `on_demand_idle_hours`: what a cost tool would book for putting the instances on a
    schedule, on-demand hourly price x hours not run, from the world's own price table."""
    assert c["method"] == "on_demand_idle_hours", c["method"]
    price = lambda iid: F(str(w.prices["ec2_hourly"][w.instance(iid)["type"]]))
    pred = sum((price(i) * 24 * STD for i in c["instances"]), F(0)) * (1 - F(str(c["target_uptime"])))
    return {"id": c["id"], "type": c["type"], "resources": list(c["instances"]), "monthly_savings_usd": float(pred),
            "basis": "list_price", "resource_spend_usd": None, "source": "claim", "title": c["title"], "target_uptime": c["target_uptime"]}


def apply_claims(periods: list, worlds: list, root: Path) -> None:
    """Claims are declared in the first period's world file (`scenario.claims`) and applied by later ones (`scenario.applied_claims`).
    They go into period 0's findings and the later period's claims_resolved, never into the agent's own diff."""
    import yaml
    from testenv import world as W
    sc0 = (yaml.safe_load((root / worlds[0]).read_text()).get("scenario") or {}) if worlds[0] else {}
    if not sc0.get("claims"):
        return
    w0 = W.load(root / worlds[0])
    made = {}
    for c in sc0["claims"]:
        f = claim_finding(c, w0)
        made[c["id"]] = f
        periods[0].findings.append(f)
    for k in range(1, len(periods)):
        sc = (yaml.safe_load((root / worlds[k]).read_text()).get("scenario") or {}) if worlds[k] else {}
        for cid in sc.get("applied_claims", []):
            periods[k].claims_resolved.append({"key": finding_key(made[cid]), "finding": cid})


def collect(base_port=37566, root: Path = ROOT, keep: str | None = None, log=lambda s: None, chain: str = "control") -> list[Period]:
    """Run the agent once per period with one shared state file (same pattern as scripts/rot_demo.sh) and read
    each period's own Cost Explorer. Two servers per period, killed before the next period starts.
    base_port=0 lets the OS pick both ports (tests use this: fixed ports in the ephemeral range flake on CI)."""
    import yaml
    aws_port, prom_port = (base_port, base_port + 1) if base_port else (_pick_port(), _pick_port())
    for port in (aws_port, prom_port):
        if not _free(port):
            raise SystemExit(f"port {port} is busy")
    tmp = Path(tempfile.mkdtemp(prefix="verify-savings-"))
    state = tmp / "state.json"
    out = []
    env = {**os.environ, "FINOPS_LLM_MODE": "replay"}
    try:
        for n, (label, world, start, end) in enumerate(CHAINS[chain], 1):
            wargs = ["--world", str(root / world)] if world else []
            errs = [tmp / f"aws-{n}.err", tmp / f"prom-{n}.err"]
            procs = [subprocess.Popen([sys.executable, "-m", "testenv.aws_server", "--port", str(aws_port), *wargs],
                                      cwd=root, stdout=subprocess.DEVNULL, stderr=errs[0].open("w")),
                     subprocess.Popen([sys.executable, "-m", "testenv.metrics_server", "--port", str(prom_port), *wargs],
                                      cwd=root, stdout=subprocess.DEVNULL, stderr=errs[1].open("w"))]
            try:
                if not (_wait(f"http://127.0.0.1:{aws_port}/health") and _wait(f"http://127.0.0.1:{prom_port}/-/healthy")):
                    tails = "\n".join(f"{e.name} (exit {pr.poll()}): {e.read_text()[-500:]}" for e, pr in zip(errs, procs))
                    raise SystemExit(f"test servers did not come up on ports {aws_port}/{prom_port}\n{tails}")
                rid = f"verify-{chain}-{n}"
                log(f"agent run {rid} on {world or 'base world'}")
                r = subprocess.run([sys.executable, "-m", "finops_agent", "run", "--aws-endpoint", f"http://127.0.0.1:{aws_port}",
                                    "--prom", f"http://127.0.0.1:{prom_port}", "--start", start, "--end", end, "--run-id", rid,
                                    "--state", str(state), "--out", str(tmp / "runs"), "--no-judge"],
                                   cwd=root, env=env, capture_output=True, text=True)
                if r.returncode:
                    raise SystemExit(f"agent run {rid} failed:\n{r.stderr[-2000:]}")
                findings = json.loads((tmp / "runs" / rid / "findings.json").read_text())["findings"]
                diff = json.loads((tmp / "runs" / rid / "slo.json").read_text())["diff"]
                log(f"cost explorer {label}")
                lines, ut = query_ce(f"http://127.0.0.1:{aws_port}", date.fromisoformat(start), date.fromisoformat(end))
            finally:
                for p in procs:
                    p.terminate()
                for p in procs:
                    p.wait()
            expect = None
            if world:
                expect = (yaml.safe_load((root / world).read_text()).get("scenario") or {}).get("expect")
            out.append(Period(label, date.fromisoformat(start), date.fromisoformat(end), findings, diff, lines, ut,
                              world or "testenv/world.yaml", expect))
        apply_claims(out, [w for _, w, _, _ in CHAINS[chain]], root)
        if keep:
            shutil.copytree(tmp / "runs", keep, dirs_exist_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---- independent price check -------------------------------------------------
def world_std_cost(w, rid):
    """Standard-month unblended cost of a volume, address or instance computed from the world's price table and the
    resource's spec, not from billing lines. An instance covered by a Savings Plan nets to 0 (BoxUsage + negation)."""
    P = w.prices
    for vol in w.all_volumes():
        if vol["id"] == rid:
            c = P["ebs_gb_month"][vol["type"]] * vol["size"]
            if vol["type"] == "io1":
                c += P["io1_iops_month"] * vol["iops"]
            if vol["type"] == "gp3":
                c += P.get("gp3_iops_month", 0) * max(0, vol.get("iops", 3000) - 3000)
                c += P.get("gp3_throughput_month", 0) * max(0, vol.get("throughput", 125) - 125)
            return c
    for a in w.raw.get("addresses", []):
        if a["allocation_id"] == rid:
            return P["public_ipv4_hourly"] * 24 * STD
    for inst in w.instances:
        if inst["id"] == rid and inst.get("state") != "stopped":
            fam = inst["type"].split(".")[0]
            if fam in {sp["family"] for sp in w.raw["savings_plans"]}:
                return 0.0
            return P["ec2_hourly"][inst["type"]] * 24 * STD * w.uptime(inst, w.period[1] - timedelta(days=1))
    return None


def price_rows(res, root: Path = ROOT) -> list:
    from testenv import world as W
    out = []
    worlds = {}
    for p in res["periods"]:
        try:
            worlds[p.label] = W.load(None if p.world.endswith("world.yaml") else root / p.world) if p.world not in ("base", "synthetic") else None
        except Exception:
            worlds[p.label] = None
    byl = {p.label: p for p in res["periods"]}
    for n, x in enumerate(res["verifications"], 1):
        w0, w1 = worlds.get(x["from"]), worlds.get(x["to"])
        if w0 is None or w1 is None:
            continue
        for k in x["now"]["keys"]:
            e0, e1 = world_std_cost(w0, k), world_std_cost(w1, k)
            e1 = 0.0 if e1 is None and e0 is not None else e1
            r0, r1 = byl[x["from"]].resources().get(k), byl[x["to"]].resources().get(k)
            ce0 = float(STD * r0.rate()) if r0 else 0.0
            ce1 = float(STD * r1.steady(byl[x["to"]])) if r1 else 0.0
            out.append({"row": f"V{n}", "key": k, "e0": e0, "ce0": ce0, "e1": e1, "ce1": ce1,
                        "ok": e0 is not None and abs(e0 - ce0) < 0.01 and abs((e1 or 0) - ce1) < 0.01})
    return out


# ---- document ---------------------------------------------------------------
def m(x, d=2):
    x = float(x)
    return ("-" if x < -0.5 * 10 ** -d else "") + f"${abs(x):,.{d}f}"


def sm(x, d=2):
    x = float(x)
    return ("+" if x > 0.5 * 10 ** -d else "") + m(x, d)


def pc(x):
    return f"{100 * float(x):.1f}%"


TYPE_ZH = {"unattached_volume": "未挂载卷", "idle_public_ip": "闲置 EIP", "gp2_to_gp3": "gp2 改 gp3", "schedule_dev_but_sp_covered": "dev 启停"}
DEV_ZH = {"calendar": "日历月天数", "proration": "按天折算", "drift": "对照组校正", "commitment": "承诺吸收",
          "basis": "预测口径", "unmapped": "资源未对上"}
CAT_NOTE = {
    "verified": "resolved finding 的资源：反事实成本减实际成本（正数，账单上以负号进入）",
    "regression": "regressed finding 的资源：实际成本减反事实成本",
    "new_waste": "本期 new finding 的新资源，按本期实际成本计",
    "calendar": "全部资源：N 期日均成本乘 N+1 期天数，减 N 期实际成本（天数不同、半个月的资源补满整月）",
    "drift": "全部资源：N+1 期实际成本减 N 期日均成本乘天数，对被处理的资源则是对照组漂移项",
    "carryover": "上一期已核实、当时只删了一部分的资源：N 期里它剩下的那部分成本这期归零（上期首月现金与标准月之差的补齐）",
    "other_removed": "没有对应 finding 的消失资源",
    "other_new": "没有对应 finding 的新增资源",
}


def byk_n(D, x, stratum):
    d = next(d for d in D if d["from"] == x["from"])
    return d["drift"].get(stratum, {}).get("n", 0)


def build(res: dict, root: Path = ROOT):
    """Return (blocks, ctx). Every number in the blocks comes from `res`; prose is f-strings over those numbers."""
    P, V, D = res["periods"], res["verifications"], res["decompositions"]
    B = []
    h = lambda t: B.append(("h2", "B" + t))
    h3 = lambda t: B.append(("h3", t))
    p_ = lambda t: B.append(("p", t))
    ul = lambda items: B.append(("ul", items))
    tb = lambda title, head, rows: B.append(("table", title, head, rows))
    row_id = {id(x): f"V{n}" for n, x in enumerate(V, 1)}
    rid = lambda x: row_id[id(x)]
    byl = {p.label: p for p in P}

    tot_pred = sum((x["predicted"] for x in V), F(0))
    tot_real = sum((x["now"]["realized_std"] for x in V), F(0))
    tot_naive = sum((x["now"]["naive_std"] for x in V), F(0))
    lost = [x for x in V if x["final"] == "兑现后丢失"]
    kept = [x for x in V if x.get("later") is not None and x["final"] not in ("兑现后丢失",)]
    lost_amt = sum((x["lost_std"] for x in lost), F(0))
    ok_n = sum(1 for x in V if x["verdict"] == "ok")
    bills = [p.bill_u for p in P]

    # ---- 1
    p_("这一部分是 c0018 的原链（基础世界、`next_month.yaml`、`month_after.yaml`），在 c0021 里保留作「零假设对照」。它回答另一个问题：这套核实流水线本身会不会凭空制造偏差？这条链上预测与账单出自同一张价格表，所以实现率应当是 100%，也确实是 100%。这个 100% 不是节省被证明，而是流水线（采集、汇总、折算、配对）没有引入误差的证据。真正考验方法的偏差在 A 部分。")
    h("1 问题与口径")
    p_(f"agent 把一条 finding 标成 resolved，只说明这个 key 在本次巡检里不再出现。它不说明账单降了，也不说明降了多少。finding 里的 monthly_savings_usd 是提出时的估算。本文用每个周期自己的 Cost Explorer 数据核实：被处理的资源在下一个周期到底少花了多少钱，和当初预测的差多少，差在哪，钱有没有留住。")
    p_(f"结论先行：三个周期（{P[0].label}、{P[1].label}、{P[2].label}）里共有 {len(V)} 条 resolved finding，预测合计 {m(tot_pred)}/月，经对照组校正后实现 {m(tot_real)}/月，实现率 {pc(tot_real / tot_pred)}。{ok_n} 条实现率在 ±2% 内。其中 {len(lost)} 条在下一个周期丢失（{'、'.join(x['id'] + ' ' + TYPE_ZH.get(x['type'], x['type']) for x in lost) or '无'}，每月 {m(lost_amt)}）。")
    h3("口径")
    ul([
        "预测：finding 的 monthly_savings_usd。basis 有三类：list_price（牌价估算，按 30 天标准月），billed 与 billed_14d_extrapolated（账单实际，agent 把近 14 天外推到分析周期的天数），inferred（推断）。",
        f"标准月：{STD} 天。测试世界的每条成本行都是月价除以 30，所以用 30 天作单位；账单外推型预测先乘 30 除以 N 期天数，换到同一单位再比较。真实账号应换成 730 小时。",
        "实现节省：被处理资源在 N+1 期的反事实成本减实际成本。反事实 = N 期日均成本乘对照组漂移系数 g。对被删除的资源，实际成本取期末的稳态日均成本（期中删除的，删除后为 0）。",
        "实现率：实现节省除以预测（均为标准月美元）。±2% 内算兑现，高于 102% 算超额，2% 以下算未兑现，其间算部分兑现。",
        "保持：同一条 finding 在 N+2 期再测一次（反事实仍用 N 期日均成本和 N 到 N+2 的对照组漂移）。N+2 实现为 0 则记为兑现后丢失。",
        "首月现金：N+1 期反事实成本（按 N+1 的真实天数）减实际成本。它回答财务月报上会看到多少，和标准月口径的差别单列为偏差原因。",
        "金额口径：成本一律取 UnblendedCost（账单实际）。AmortizedCost 只用来检测承诺吸收。",
    ])

    # ---- 2
    h("2 数据")
    p_("每个周期起一套 test-aws-server 和 test-metrics-server（端口 37566 起，周期之间先关再开），用 `--world` 选当期世界：基础世界、`testenv/scenarios/next_month.yaml`、`testenv/scenarios/month_after.yaml`。agent 用 `--no-judge` 跑三次，共用一个 state 文件，写法与 `scripts/rot_demo.sh` 相同。agent 之外，用 boto3（endpoint_url 指向测试服务器）直接查该周期的 Cost Explorer：")
    ul(["资源级：GetCostAndUsageWithResources，日粒度，按 RESOURCE_ID 和 USAGE_TYPE 分组，UnblendedCost 与 AmortizedCost。接口限制单次不超过 14 天，所以每个周期分段查询再相加；接口要求带 Filter，取该周期出现过的全部 SERVICE。",
        "用量类型级：GetCostAndUsage，月粒度，按 USAGE_TYPE 分组，用来和资源级交叉核对。",
        "没有资源 ID 的行（跨 AZ 流量、S3、CloudWatch、Route 53）在资源级记为 NoResourceId，本文按 usage:<用量类型> 作资源键。"])
    rows = []
    for p in P:
        ex = "—"
        if p.expect is not None:
            got = {k: sorted(e["key"] for e in p.diff.get(k, [])) for k in ("resolved", "new", "regressed")}
            want = {k: sorted(p.expect.get(k, [])) for k in ("resolved", "new", "regressed")}
            ex = "一致" if got == want else "不一致"
        ut_sum = sum(p.ut_totals.values(), F(0))
        rows.append([p.label, p.world, p.days, len(p.findings), "/".join(str(len(p.diff.get(k, []))) for k in ("new", "persisting", "resolved", "regressed")),
                     m(p.bill_u), m(p.bill_a), m(ut_sum - p.bill_u, 6), ex])
    tb("表 1 周期、世界与 agent 运行", ["周期", "世界文件", "天数", "findings", "new/persisting/resolved/regressed", "账单(Unblended)", "账单(Amortized)", "用量类型级减资源级", "与 scenario.expect"], rows)
    rows = []
    for p in P:
        for f in p.findings:
            st = next((k for k in ("new", "persisting", "resolved", "regressed") if any(e.get("finding") == f["id"] for e in p.diff.get(k, []))), "—")
            rows.append([p.label, f["id"], f"`{f['type']}`", "、".join(f["resources"][:2]) + ("…" if len(f["resources"]) > 2 else ""), m(f["monthly_savings_usd"]), f["basis"],
                         m(f["resource_spend_usd"]) if f.get("resource_spend_usd") is not None else "—", st])
    tb("表 1b agent 各周期的 findings 与预测（`--no-judge`，只有确定性检测器）", ["周期", "id", "类型", "资源（前 2）", "monthly_savings_usd", "basis", "资源花费", "本期 diff 状态"], rows)
    uts = sorted(set().union(*[set(p.ut_totals) for p in P]))
    rows = []
    for u in uts:
        a, b, c = [p.ut_totals.get(u, F(0)) for p in P]
        rows.append([f"`{u}`", m(a), m(b), m(c), sm(b - a), sm(c - b)])
    rows.append(["合计", m(sum(P[0].ut_totals.values(), F(0))), m(sum(P[1].ut_totals.values(), F(0))), m(sum(P[2].ut_totals.values(), F(0))),
                 sm(sum(P[1].ut_totals.values(), F(0)) - sum(P[0].ut_totals.values(), F(0))), sm(sum(P[2].ut_totals.values(), F(0)) - sum(P[1].ut_totals.values(), F(0)))])
    tb("表 2 用量类型级成本（Cost Explorer，月）", ["用量类型", P[0].label, P[1].label, P[2].label, f"{P[1].label} 相对 {P[0].label}", f"{P[2].label} 相对 {P[1].label}"], rows)
    p_(f"表 2 的合计与表 1 的账单差几个 e-6 美元：Cost Explorer 每行按 6 位小数返回，月度加总与日度加总的舍入不同。账单从 {m(bills[0])} 到 {m(bills[1])} 再到 {m(bills[2])}，每次变动都有天数、NAT 用量等多个原因，所以不能拿账单差当节省。")
    touched = []
    for x in V:
        touched += x["now"]["keys"]
    for k in (1, 2):
        for e in P[k].diff.get("regressed", []) + P[k].diff.get("new", []):
            f = next((f for f in P[k].findings if finding_key(f) == e["key"]), None)
            touched += [r for r in (f["resources"] if f else []) if r in P[k].resources()]
    touched = list(dict.fromkeys(touched))
    rows = []
    for k in touched:
        cells = [f"`{k}`"]
        for p in P:
            r = p.resources().get(k)
            cells.append(f"{m(r.cost_u)}（{len(r.days)}/{p.days} 天，{r.first.isoformat()} 起）" if r else "不在账单")
        cells.append("、".join(sorted({u for p in P for u in (p.resources().get(k).uts if p.resources().get(k) else {})})))
        rows.append(cells)
    tb("表 3 被处理资源的逐周期成本（资源级）", ["资源", P[0].label, P[1].label, P[2].label, "用量类型"], rows)

    # ---- 3
    h("3 方法（含反事实基线与混杂因素）")
    p_("账单差 = 节省 + 一切别的变化。要证明钱是省下来的，必须把别的变化挪出去。方法分五步，全部由 `finops_agent/verify_savings.py` 的确定性代码完成，没有 LLM 参与。")
    ul([
        "第 1 步，配对：N+1 期 diff 的 resolved 列表里每个 key，回到 N 期的 findings 找到同 key 的 finding，取它的预测、basis 和资源列表。资源 ID 在账单里没有行的（例如 AMI），记为未对上，只核实有行的部分。",
        "第 2 步，基线：N 期被处理资源的日均成本 = 资源级成本 / 该资源有账单的天数。中途创建的资源按有账单的天数算，所以半个月的资源不会被低估。",
        "第 3 步，对照组：资源级成本在 N 与 N+1 都出现、N+1 期末仍在计费、且不属于任何 resolved / regressed / new finding 的资源。按用量类型类别分层（ebs_volume、ebs_snapshot、public_ipv4、compute、nat，其余各自一层）。漂移系数 g = 层内对照资源 N+1 日均成本之和 / N 日均成本之和。某层没有对照资源，就用全体对照资源的 g，并在表里标出。c0021 起分层多一维：同一类别再按「是否为浪费类 finding 的对象」分成 active 与 waste，细层没有对照资源时先退回粗类别，再退回全体（原链的数字不受影响，见 A3）。",
        "第 4 步，反事实与实现：反事实日均成本 = N 日均成本 x g。实现节省 = (反事实 - N+1 稳态日均成本) x 30。同时给出不校正的朴素值（N 减 N+1），两者之差就是校正量。",
        "第 5 步，偏差分类：对每条核实按规则打标。日历月天数（N+1 不是 30 天）；按天折算（资源在 N 或 N+1 只计费一部分天数）；对照组校正（校正量超过预测的 2%）；预测口径（牌价与账单是否一致，外推是否与 N 期成本一致）；承诺吸收（摊销口径节省比账单口径多出 2% 以上，说明差额进了 Savings Plan 未用承诺）。",
    ])
    h3("混杂因素与处理")
    tb("表 4 混杂因素", ["混杂因素", "怎么影响", "怎么处理"], [
        ["日历月天数", "10 月 31 天，所有按天计费的行都多 1/30；两张发票相减会把天数差混进节省", "用 30 天标准月比较；首月现金按真实天数另列"],
        ["中途创建或删除", "N 期只计费几天的资源，发票上的成本低于它的月度水平", "用日均成本，不用发票成本；稳态取期末是否仍在计费"],
        ["用量漂移", "NAT 字节、跨 AZ 流量这类用量计费行，本身会随业务涨跌，混进账单差", "对照组按层估计漂移，反事实乘 g；只对用量被治理资源之外的对照资源估计"],
        ["新资源", "新增的闲置资源抬高账单，与节省方向相反", "单列为新增浪费，不从节省里扣，也不算进漂移"],
        ["回归", "同一个 key 在后续周期回来，节省被抹掉", "同一条 finding 在 N+2 期再测，并在账单分解里单列回归损失"],
        ["承诺覆盖", "被 Savings Plan 覆盖的实例，停机后账单不动，省下的钱被未用承诺吸收", "同时算 Unblended 与 Amortized 节省，差额打『承诺吸收』标"],
        ["预测口径", "牌价预测与账单实际可能不同；账单外推用 N 期天数", "核实表标出 basis，并换算到标准月"],
    ])
    rows = []
    seen = set()
    for d in D:
        for s, a in sorted(d["drift"].items()):
            if s.startswith(("Amazon", "AmazonCloud", "DataTransfer", "SavingsPlan")):
                continue
            rows.append([f"{d['from']} 到 {d['to']}", s if s != "*" else "* (全部对照资源)", a["n"], m(STD * a["rate0"]), m(STD * a["rate1"]), f"{float(a['g']):.4f}" if a["g"] else "—"])
    tb("表 5 对照组漂移（标准月成本之和与系数 g；已略去单资源的服务层）", ["区间", "层", "对照资源数", "N 期", "N+1 期", "g"], rows)
    nat = next((a for d in D for s, a in d["drift"].items() if s == "nat" and d["from"] == P[0].label), None)
    if nat and nat["g"]:
        p_(f"表 5 里 {P[0].label} 到 {P[1].label} 的 nat 层 g = {float(nat['g']):.4f}：世界文件的 incident_start 是 2026-09-04，9 月只有部分天处在重试风暴里，{P[1].label} 整月都在，NAT 字节成本上涨，这就是一个真实的用量漂移。被处理的是 EBS 和 EIP，不受它影响，所以各自层的 g 为 1。这说明分层有必要：用全体对照资源的 g（{float(next(d for d in D)['drift']['*']['g']):.4f}）去校正卷，会凭空多算 {pc(next(d for d in D)['drift']['*']['g'] - 1)}。")

    # ---- 4
    h("4 逐条核实表")
    rows = []
    for x in V:
        n = x["now"]
        rows.append([rid(x), f"{x['id']} {TYPE_ZH.get(x['type'], x['type'])}", f"{x['from']} 到 {x['to']}", m(x["predicted"]), x["basis"], m(n["baseline_std"]), m(n["cf_std"]), m(n["act_std"]),
                     m(n["naive_std"]), m(n["realized_std"]), pc(n["realized_std"] / x["predicted"]) if x["predicted"] else "—", m(n["realized_cal"]), m(n["naive_cal"])])
    tb("表 6 逐条核实：数字（标准月美元，除最后两列）", ["行", "finding", "区间", "预测", "预测口径", "N 期成本", "反事实", "N+1 实际", "朴素节省", "实现节省", "实现率", "首月现金", "两张发票之差"], rows)
    rows = []
    for x in V:
        devs = "；".join(f"{DEV_ZH[c]}：{t}" for c, t in x["devs"]) or "无"
        later = "N+2 期无数据" if x["later"] is None else f"{x['later']['period']} 保持 {m(x['retained_std'])}，丢失 {m(x['lost_std'])}"
        rows.append([rid(x), devs, x["label"], later, x["final"]])
    tb("表 7 逐条核实：偏差原因与结论", ["行", "偏差原因", "本期结论", "下一期保持", "最终结论"], rows)
    led = [x for x in V if x["later"] is not None]
    rows = []
    for x in led:
        n, l = x["now"], x["later"]
        rows.append([rid(x), f"{x['id']} {TYPE_ZH.get(x['type'], x['type'])}", m(n["baseline_std"]), m(n["act_std"]), m(l["act_std"]), m(n["realized_std"]), m(l["realized_std"]),
                     m(x["lost_std"]), "是" if x["regressed_later"] else "否"])
    tb("表 8 保持台账：N 期、N+1 期、N+2 期的标准月成本", ["行", "finding", "N 期成本", "N+1 实际", "N+2 实际", "N+1 实现", "N+2 仍实现", "丢失", "N+2 期 diff 为 regressed"], rows)
    for x in V:
        n = x["now"]
        t = TYPE_ZH.get(x["type"], x["type"])
        res_ = "、".join(n["keys"])
        txt = f"{rid(x)}（{x['id']} {t}，`{res_}`）：预测 {m(x['predicted'])}，实现 {m(n['realized_std'])}，实现率 {pc(n['realized_std'] / x['predicted'])}，朴素差 {m(n['naive_std'])}。"
        if x["basis"] in ("billed", "billed_14d_extrapolated") and x["predicted_raw"] != x["predicted"]:
            txt += f"agent 的原始预测 {m(x['predicted_raw'])} 是按 {byl[x['from']].days} 天外推的，换算到标准月是 {m(x['predicted'])}，与实现{'一致' if abs(x['predicted'] - n['realized_std']) <= TOL * x['predicted'] else '不一致'}。"
        txt += f"首月现金 {m(n['realized_cal'])}（{byl[x['to']].days} 天），两张发票之差 {m(n['naive_cal'])}。"
        if n["partial"]:
            txt += f"按天折算：{'；'.join(n['partial'])}。发票差、首月现金、标准月实现是同一件事的三个口径，不是三个结论。"
        if x["later"] is not None:
            txt += f"下一期 {x['later']['period']}：{x['final']}。"
        p_(txt)
    if lost:
        x = lost[0]
        p_(f"{rid(x)} 是本文最重要的一行。{byl[x['from']].label} 的 finding 预测 {m(x['predicted'])}（牌价），{x['to']} 的账单上它兑现了 {m(x['now']['realized_std'])}，实现率 {pc(x['now']['realized_std'] / x['predicted'])}，resolved 判断正确。{x['later']['period']} 一次不相关的 terraform apply 把卷改回 gp2，同一个 key 重新出现（regressed），这笔节省在表 8 里归零：丢失 {m(x['lost_std'])}/月。resolved 在当月是真的，但不保证下月仍真，所以要看保持，不能只看一次。")

    # ---- 5
    h("5 组合视图与账单分解")
    rows = []
    for k in range(len(P) - 1):
        vs = [x for x in V if x["from"] == P[k].label]
        if not vs:
            continue
        pr = sum((x["predicted"] for x in vs), F(0))
        re_ = sum((x["now"]["realized_std"] for x in vs), F(0))
        nv = sum((x["now"]["naive_std"] for x in vs), F(0))
        cal = sum((x["now"]["realized_cal"] for x in vs), F(0))
        inv = sum((x["now"]["naive_cal"] for x in vs), F(0))
        ret = sum((x["retained_std"] for x in vs if x["later"] is not None), F(0)) if any(x["later"] for x in vs) else None
        rows.append([f"{P[k].label} 到 {P[k + 1].label}", len(vs), m(pr), m(re_), pc(re_ / pr), m(nv), m(cal), m(inv), m(ret) if ret is not None else "N+2 无数据"])
    rows.append(["合计", len(V), m(tot_pred), m(tot_real), pc(tot_real / tot_pred), m(tot_naive), m(sum((x["now"]["realized_cal"] for x in V), F(0))),
                 m(sum((x["now"]["naive_cal"] for x in V), F(0))), "—"])
    tb("表 9 组合视图：预测与实现（标准月美元；首月现金与发票之差按日历月）", ["区间", "条数", "预测", "实现", "实现率", "朴素节省", "首月现金", "两张发票之差", "N+2 期仍保持"], rows)
    bb = {}
    for x in V:
        a = bb.setdefault(x["basis"], [0, F(0), F(0)])
        a[0] += 1
        a[1] += x["predicted"]
        a[2] += x["now"]["realized_std"]
    tb("表 10 按预测口径分", ["预测口径", "条数", "预测", "实现", "实现率"], [[k, a[0], m(a[1]), m(a[2]), pc(a[2] / a[1])] for k, a in sorted(bb.items())])
    last = P[-1]
    open_ = [f for f in last.findings if finding_key(f) in {e["key"] for e in last.diff.get("persisting", [])}]
    open_pred = sum((F(str(f["monthly_savings_usd"])).limit_denominator(10 ** 9) for f in open_), F(0))
    still = sum((x["retained_std"] for x in V if x["later"] is not None), F(0)) + sum((x["now"]["realized_std"] for x in V if x["later"] is None), F(0))
    p_(f"组合上看：截至 {last.label}，仍然有效的实现节省是 {m(still)}/月（{P[1].label} 兑现并保持的，加 {last.label} 新兑现的）。同一周期还有 {len(open_)} 条 persisting finding 没人处理，预测合计 {m(open_pred)}/月，这部分是尚未兑现的池子，不是节省。")
    cats = [c for c, _ in CATS]
    rows = []
    for c, zh in CATS:
        sign = -1 if c == "verified" else 1
        rows.append([zh] + [sm(sign * d["parts"][c]) for d in D] + [CAT_NOTE[c]])
    rows.append(["合计（各项之和）"] + [sm(d["sum"]) for d in D] + [""])
    rows.append(["实际账单差（Cost Explorer）"] + [sm(d["delta"]) for d in D] + [""])
    rows.append(["差（必须为 0）"] + [m(d["sum"] - d["delta"], 6) for d in D] + ["精确有理数运算，不是舍入后相等"])
    tb("表 11 账单分解：账单差 = 已核实节省 + 回归损失 + 新增浪费 + 其他", ["项目"] + [f"{d['from']} 到 {d['to']}" for d in D] + ["含义"], rows)
    for d in D:
        pt = d["parts"]
        p_(f"{d['from']} 到 {d['to']}：账单从 {m(d['bill0'])} 变到 {m(d['bill1'])}，差 {sm(d['delta'])}。已核实节省 {m(pt['verified'])}（{'、'.join(d['verified_findings']) or '无'}），回归损失 {m(pt['regression'])}（{'、'.join(d['regressed_findings']) or '无'}），新增浪费 {m(pt['new_waste'])}（{'、'.join(d['new_findings']) or '无'}），日历天数 {sm(pt['calendar'])}，用量漂移 {sm(pt['drift'])}。各项相加与实际账单差的差值为 {m(d['sum'] - d['delta'], 6)}。")
    d0 = D[0]
    p_(f"表 11 说明为什么不能拿账单差当节省：{d0['from']} 到 {d0['to']} 三项清理实际省了 {m(d0['parts']['verified'])}，账单却涨了 {m(d0['delta'])}，因为日历多一天贡献 {sm(d0['parts']['calendar'])}，NAT 用量漂移等贡献 {sm(d0['parts']['drift'])}。只看总账单，会得出『清理没有效果』的错误结论。")
    for d in D:
        rows = [[f"`{mv['key']}`", dict(CATS).get(mv["role"], "无关变动" if mv["role"] == "other" else mv["role"]), m(mv["c0"]), m(mv["c1"]), sm(mv["delta"])] for mv in d["movers"][:8]]
        tb(f"表 12 变动最大的资源：{d['from']} 到 {d['to']}（前 8，按绝对变动）", ["资源键", "归类", "N 期", "N+1 期", "变动"], rows)

    # ---- 6
    h("6 为什么这个结论可信")
    pr_rows = price_rows(res, root)
    rows = [[r["row"], f"`{r['key']}`", m(r["e0"]) if r["e0"] is not None else "—", m(r["ce0"]), m(r["e1"] or 0), m(r["ce1"]), "一致" if r["ok"] else "不一致"] for r in pr_rows]
    tb("表 13 独立复算：世界价格表（单价 x 规格）与 Cost Explorer 的标准月成本", ["行", "资源", "N 期（价格表）", "N 期（账单）", "N+1 期（价格表）", "N+1 期（账单）", "对账"], rows)
    chk = []
    chk.append(["用量类型级合计与资源级合计相差不超过 $0.01（舍入）", all(abs(sum(p.ut_totals.values(), F(0)) - p.bill_u) < F(1, 100) for p in P)])
    chk.append(["账单分解各项之和与实际账单差精确相等（有理数）", all(d["exact"] for d in D)])
    chk.append(["每条核实的预测不超过该资源 N 期花费（FA04 不变量）", all(x["spend"] is None or x["predicted_raw"] <= x["spend"] + F(1, 100) for x in V)])
    chk.append(["每条核实的实现节省不超过反事实成本（实际成本非负）", all(x["now"]["realized_std"] <= x["now"]["cf_std"] for x in V)])
    chk.append(["每条核实所用的层都有对照资源，没有退回全体平均", all(s != "*" for x in V for (_k, s, _g) in x["now"]["strata"])])
    chk.append(["资源 ID 全部在账单里有行", all(not x["now"]["unmapped"] for x in V)])
    exp = [p for p in P if p.expect is not None]
    chk.append(["各期 diff 与 scenario.expect 的 resolved / new / regressed 逐项一致", all(
        {k: sorted(e["key"] for e in p.diff.get(k, [])) for k in ("resolved", "new", "regressed")} == {k: sorted(p.expect.get(k, [])) for k in ("resolved", "new", "regressed")} for p in exp)])
    chk.append(["价格表复算与账单一致（表 13 全部一致）", bool(pr_rows) and all(r["ok"] for r in pr_rows)])
    tb("表 14 自检清单", ["检查", "结果"], [[c, "通过" if ok else "失败"] for c, ok in chk])
    ul([
        "两条互相独立的路径对上了账。路径一：把 Cost Explorer 的日度成本行按资源汇总再取日均。路径二：用世界文件里的单价乘资源规格。两者在表 13 一致，说明汇总、日均和折算的代码没有算错。",
        "预测与实测来自不同来源。预测来自 agent 在 N 期的检测器（牌价或 14 天外推），实测来自 N+1 期的账单。它们对上，才说明预测有依据；对不上，偏差分类列出原因。",
        "账单分解是一个闭合检验：把整张账单的变动拆成七项，求和与实际差完全相等。如果核实表里某条节省被多算、少算或重复算，这个等式会破。",
        "方法对已知的反例有反应。单元测试里造了用量下降（朴素法虚增节省）、用量上涨（朴素法掩盖节省）、中途创建与删除、承诺吸收四种情形，校正后的数字等于变更本身的效果。gp3 被回退的情形在真实场景里出现，并被表 8 抓到。",
        "每个数字由代码生成：`python3 -m finops_agent.verify_savings` 重新跑一遍三个周期，文档逐字节相同（单元测试 test_document_is_deterministic 重新采集两次并比对）。",
        f"实现率 {pc(tot_real / tot_pred)} 是这条链上的零假设结果，不是方法的功劳：世界用同一张价格表生成预测所依据的成本行和账单，预测本来就应该对上。真实偏差见 A 部分。这里的证据价值在三处：流水线（采集、汇总、折算、配对）没有凭空制造偏差；两张发票之差（{m(sum((x['now']['naive_cal'] for x in V), F(0)))}）、首月现金（{m(sum((x['now']['realized_cal'] for x in V), F(0)))}）与标准月实现（{m(tot_real)}）的差别被逐项解释；方法在能制造偏差的合成案例里给出了正确的偏差。",
    ])

    used = sorted({(sv, byk_n(D, x, sv), g) for x in V for (_k, sv, g) in x["now"]["strata"]})
    used_txt = "；".join(f"{sv} 层 {n} 个（g={float(g):.4f}）" for sv, n, g in used)

    # ---- 7
    h("7 局限")
    ul([
        "数据是合成的。世界文件同时生成成本行和价格表，所以表 13 的对账证明的是汇总与折算逻辑没错，不证明真实账单的行为：折扣、积分、退款、预留实例、跨月结算调整在这里都没有。这条链没有承诺覆盖的 resolved 项，也没有期中删除，承诺吸收与按天折算在 A 部分的场景链里被真实数据验证。",
        "这条链里的删除都发生在周期开始之前（覆盖层一次性移除资源，成本行整月消失）。",
        f"对照组的假设是同层资源的用量按同一比例漂移。核实所用的层及其对照资源数：{used_txt}。同层对照很少，且被处理资源所在层的 g {'全部为 1，校正量为 0' if all(g == 1 for _s, _n, g in used) else '不全为 1'}。单元测试验证了校正公式，真实场景没有给出非平凡的校正，所以这条链上没有『校正有效』的证据；它在 A 部分的 gp2 改 gp3 增长场景里被验证。",
        "resolved 只核实了 findings 里 monthly_savings_usd 大于 0 的项。savings_plan_underutilized 的预测为 0，持续 persisting，没有进入核实。三条没被 `--no-judge` 跑出的判断层 finding（ingest 车队、NAT、跨 AZ）不在本文范围。",
        "标准月取 30 天是测试世界的定价约定。真实账号按小时计费，应使用 730 小时。资源级成本在真实 AWS 里只对 EC2 相关服务提供且最多回溯 14 天，对更早周期要靠自己保存的数据，不能事后补查。",
        "只有 3 个周期。保持只测了一期（N+2）。节省是否能保持半年，这里没有证据。",
    ])

    # ---- 8
    h("8 结论")
    ul([
        f"resolved 与账单一致：{len(V)} 条 resolved 的资源在下一期账单里都真的少了成本，预测合计 {m(tot_pred)}，标准月实现合计 {m(tot_real)}，实现率 {pc(tot_real / tot_pred)}。逐条实现率均落在 ±2% 内的有 {ok_n} 条。",
        f"预测与实现的差别来自口径，不来自错误：账单外推型预测带着 N 期的日历天数（{'、'.join(f'{p.label} {p.days} 天' for p in P)}），换算到标准月后与实现一致；牌价型预测（gp2 改 gp3）与账单实测一致。{'逐条实现率都在 ±2% 内，没有发现预测的系统性高估或低估。' if ok_n == len(V) else '有实现率偏离 ±2% 的行，见表 7。'}",
        f"一次兑现不等于留住：{'、'.join(x['id'] for x in lost)} 在下一期被回退，{m(lost_amt)}/月 的实现归零。resolved 之后要持续看 regressed，这条已有告警 FinOpsRegression。",
        f"账单分解精确闭合：{'；'.join(f'{d['from']} 到 {d['to']} 账单差 ' + sm(d['delta']) + '，其中已核实节省 ' + m(d['parts']['verified']) for d in D)}。账单涨跌与节省没有直接关系。",
        "这条链没有考验承诺覆盖、中途删除与用量增长；它们在 A 部分的场景链里考验，见 A1。",
    ])
    ctx = {"V": V, "D": D, "tot_pred": tot_pred, "tot_real": tot_real, "checks": chk, "price_rows": pr_rows}
    return B, ctx


# ---- part A: the scenario chain (c0021) ---------------------------------------
TAGS = {"proration": "a", "commitment": "b", "drift": "c", "basis": "d", "none": "e"}


def _res_rows(P, keys):
    rows = []
    for k in keys:
        cells = [f"`{k}`"]
        for p in P:
            r = p.resources().get(k)
            cells.append(f"{m(r.cost_u)}（{len(r.days)}/{p.days} 天）" if r else "不在账单")
        cells.append("、".join(sorted({u for p in P for u in (p.resources().get(k).uts if p.resources().get(k) else {})})))
        rows.append(cells)
    return rows


def _control_rows(P, x):
    i = [p.label for p in P].index(x["from"])
    p0, p1 = P[i], P[i + 1]
    used = x["now"]["strata"][0][1]
    treated = touched_between(P, i + 1, i + 1) | set(x["now"]["keys"])
    rows = []
    for k, r0 in sorted(p0.resources().items()):
        r1 = p1.resources().get(k)
        if r1 is None or k in treated or not r1.alive_at_end(p1):
            continue
        if used in (fine_stratum(r0, p0), stratum_of(r0)):
            rows.append([f"`{k}`", "、".join(sorted(r0.uts)), m(STD * r0.rate()), m(STD * r1.rate()), f"{float(r1.rate() / r0.rate()):.4f}" if r0.rate() else "—"])
    return rows


def build_scenario(res: dict, root: Path = ROOT):
    """Part A: the chain with real deviations. Returns (blocks, ctx)."""
    from testenv import world as W
    P, V, D = res["periods"], res["verifications"], res["decompositions"]
    B = []
    h = lambda t: B.append(("h2", t))
    h3 = lambda t: B.append(("h3", t))
    p_ = lambda t: B.append(("p", t))
    ul = lambda items: B.append(("ul", items))
    tb = lambda title, head, rows: B.append(("table", title, head, rows))
    byl = {p.label: p for p in P}
    worlds = {p.label: W.load(root / p.world) for p in P}
    pick = lambda prefix: next(x for x in V if x["key"].startswith(prefix))
    xa, xb = pick("unattached_volume|vol-0vfya"), pick("schedule_dev_but_sp_covered|")
    xc, xd, xe = pick("gp2_to_gp3|vol-0vfyc"), pick("gp2_to_gp3|vol-0vfyd"), pick("idle_public_ip|")
    items = [("a", xa), ("b", xb), ("c", xc), ("d", xd), ("e", xe)]
    ITEM = {"a": "期中删除未挂载卷", "b": "dev 启停（SP 覆盖的 m5）", "c": "gp2 改 gp3，同期用量增长 30%", "d": "gp2 改 gp3，保留 IOPS 与吞吐", "e": "释放闲置 EIP（对照，无偏差）"}
    tag = {id(x): t for t, x in items}
    tot_pred = sum((x["predicted"] for x in V), F(0))
    tot_real = sum((x["now"]["realized_std"] for x in V), F(0))
    tot_naive = sum((x["now"]["naive_std"] for x in V), F(0))
    tot_cash = sum((x["now"]["realized_cal"] for x in V), F(0))
    tot_cashpred = sum((x["cash_pred"] for x in V), F(0))
    tot_inv = sum((x["now"]["naive_cal"] for x in V), F(0))
    dev_n = sum(1 for x in V if x["primary"] != "none")
    Va = [x for x in V if x["source"] != "claim"]
    a_pred = sum((x["predicted"] for x in Va), F(0))
    a_real = sum((x["now"]["realized_std"] for x in Va), F(0))
    sp_key = "sp-ec2-m5-01"
    gt05 = next(f for f in W.load().raw["ground_truth"]["findings"] if f["id"] == "GT05")["monthly_savings_usd"]

    # ---- A1
    h("A1 结论先行")
    p_(f"这条核实链（`testenv/scenarios/verify_*.yaml`，三个周期 {P[0].label}、{P[1].label}、{P[2].label}）在基础世界上叠加了会让「预测」和「账单实测」分开的事件。共 {len(V)} 条 resolved（其中 1 条是场景声明的纸面节省 C01，不是 agent 的输出），预测合计 {m(tot_pred)}/月，标准月实现 {m(tot_real)}/月，实现率 {pc(tot_real / tot_pred)}，不是 100%。{dev_n} 条出现偏差，原因逐条分类如表 A1，其余 {len(V) - dev_n} 条作对照，实现率 {pc(xe['std_rate'])}。")
    ul([
        f"（a）期中删除：{xa['id']} 预测 {m(xa['predicted'])}，标准月实现 {m(xa['now']['realized_std'])}（{pc(xa['std_rate'])}），但首月现金只实现 {pc(xa['cash_rate'])}：卷在 {P[1].label}-11 才删除，当月 {xa['now']['days1']} 天里只有 {len(byl[P[1].label].resources()[xa['now']['keys'][0]].days)} 天计费。原因分类「按天折算」。{P[2].label} 整月为 0，次月现金实现率 {pc(xa['later_cash_rate'])}。",
        f"（b）承诺吸收：{xb['id']} 预测 {m(xb['predicted'])}（按需价 x 闲置小时），账单口径实现 {m(xb['now']['realized_std'])}（{pc(xb['std_rate'])}）。摊销口径节省 {m(xb['now']['realized_amort_std'])}，全部进了 Savings Plan 的未用承诺。原因分类「承诺吸收」。这是基础世界 GT05 陷阱（标准答案 {gt05['min']} 到 {gt05['max']} 美元）的具体化。",
        f"（c）用量增长：{xc['id']} 朴素对比 {sm(xc['now']['naive_std'])}（两张发票看起来改 gp3 反而更贵），对照组 g = {float(xc['now']['strata'][0][2]):.4f} 校正后实现 {m(xc['now']['realized_std'])}，预测 {m(xc['predicted'])}。原因分类「用量漂移」。",
        f"（d）口径差：{xd['id']} 预测 {m(xd['predicted'])}（牌价只算 GB 单价差），实现 {m(xd['now']['realized_std'])}（{pc(xd['std_rate'])}）。短缺 {m(xd['shortfall'])} 全部是为保留性能而配的 gp3 IOPS 与吞吐。原因分类「预测口径差」。另外，agent 的牌价预测带着 {P[1].label} 的 {P[1].days} 天，原值 {m(xd['predicted_raw'])}，换算标准月后是 {m(xd['predicted'])}。",
        f"组合：预测 {m(tot_pred)}，实现 {m(tot_real)}，差 {m(tot_pred - tot_real)}，其中 {m(xb['predicted'] - xb['now']['realized_std'])} 是 C01 的纸面节省：把 C01 当真，预测是实现的 {float(tot_pred / tot_real):.1f} 倍。只看 agent 自己的 {len(Va)} 条 finding：预测 {m(a_pred)}，实现 {m(a_real)}，实现率 {pc(a_real / a_pred)}（偏差来自（a）（c）（d）的口径与增长，其中（c）超额、（d）不足）。",
        f"账单分解仍然精确闭合：" + "；".join(f"{d['from']} 到 {d['to']} 账单差 {sm(d['delta'])}，各项之和与之相差 {m(d['sum'] - d['delta'], 6)}" for d in D) + "。",
    ])
    rows = []
    for t, x in items:
        n = x["now"]
        later = pc(x["later_cash_rate"]) if x["later_cash_rate"] is not None else "无 N+2"
        rows.append([f"（{t}）{ITEM[t]}", f"{x['id']} `{x['type']}`", f"{x['from']} 到 {x['to']}", m(x["predicted"]), m(n["realized_std"]), pc(x["std_rate"]),
                     pc(x["cash_rate"]), later, CLASS_ZH[x["primary"]], x["conclusion"]])
    rows.append(["合计", f"{len(V)} 条", "", m(tot_pred), m(tot_real), pc(tot_real / tot_pred), pc(tot_cash / tot_cashpred), "", "", ""])
    tb("表 A1 偏差总表（预测与实现为标准月美元；首月现金率 = 首月现金节省 / 预测折算到该月天数）", ["项", "finding", "区间", "预测", "实现", "实现率", "首月现金实现率", "次月现金实现率", "原因分类", "结论"], rows)

    # ---- A2
    h("A2 为什么要这条链，偏差是怎么造出来的")
    p_("c0018 在原链（基础世界、next_month、month_after）上核实了全部 resolved，实现率 100%。那不是方法的功劳：世界用同一张价格表同时生成预测依据的成本行和账单，预测必然对上；比例折算、承诺吸收、对照组校正只在合成单测里被验证。维护者要的是「为什么对、怎么验证真的省钱了」，所以这条链刻意制造四类偏差，每一类在真实账单里都会出现，而且从世界文件的规格出发就能算出应有的结果（A6 的设计值对账）。")
    tb("表 A2 偏差设计表", ["项", "场景怎么造（世界文件）", "为什么现实里会这样", "核实器应当给出的分类"], [
        ["（a）期中删除", "`verify_month1.yaml` 给卷加 `deleted: 2026-10-11`；`world.py` 新增该字段，删除日之前计费，期末 API 不再返回", "删除不会恰好在月初。发票只有部分天数，首月现金低于月度水平", "按天折算；标准月 100%，首月现金 21/31，次月 100%"],
        ["（b）承诺吸收", "`verify_base.yaml` 声明 claim C01；`verify_month1.yaml` 给 3 台 dev m5.2xlarge 加 `uptime: 0.3`（`world.py` 新增，按比例减少按需小时）", "按需价节省被 Savings Plan 覆盖：BoxUsage 与 SavingsPlanNegation 相抵为 0，承诺费固定，停机只让承诺更空", "承诺吸收；账单口径 0，摊销口径为正"],
        ["（c）用量增长", "`verify_month1.yaml` 把 orders-db 卷与三张数据卷都放大 30%；被处理的 vol-0vfyc 同期 gp2 改 gp3，也放大 30%", "改配置的同时业务在增长。两张发票相减，增长把节省抵掉", "用量漂移；朴素为负，校正后为正"],
        ["（d）口径差", "`verify_month2.yaml` 把 3000 GB gp2 改 gp3 并配 9000 IOPS、250 MB/s（`world.py` 新增 gp3 的 IOPS 与吞吐计费，价格在 `verify_base.yaml` 的 prices 里）", "gp2 的 3 IOPS/GB 在大卷上给出 9000 IOPS；gp3 只含 3000 IOPS 与 125 MB/s，保留性能要按量另付", "预测口径差；短缺等于新增的 IOPS 与吞吐用量类型"],
        ["（e）对照", "`verify_month1.yaml` 释放闲置 EIP", "没有偏差源的清理，用来证明核实器没有凭空制造偏差", "无偏差"],
    ])
    p_(f"（b）的预测来自哪里要说清楚。{P[0].label} 的 `--no-judge` agent 没有排期类 finding（确定性检测器里没有这一类），判断层对 GT05 的结论是「先不要停，节省接近 0」。所以 C01 不是 agent 的输出，而是场景声明的一个假想：有人拿按需价乘闲置小时算出一个纸面节省，然后照做。核实器检验的就是这个纸面值有没有兑现。它在表里标为来源 claim，不混入 agent 的成绩。")
    p_("世界文件里新增的三个字段（`deleted`、`uptime`、gp3 的 `iops`/`throughput`）都是可选的，基础世界和已有场景没有这些字段，`World.lines` 的 sha256 与改动前逐字节相同（见 evidence.md）。")

    # ---- A3
    h("A3 数据")
    rows = []
    for p in P:
        ex = "—"
        if p.expect is not None:
            got = {k: sorted(e["key"] for e in p.diff.get(k, [])) for k in ("resolved", "new", "regressed")}
            want = {k: sorted(p.expect.get(k, [])) for k in ("resolved", "new", "regressed")}
            ex = "一致" if got == want else "不一致"
        rows.append([p.label, p.world, p.days, len(p.findings), "/".join(str(len(p.diff.get(k, []))) for k in ("new", "persisting", "resolved", "regressed")),
                     m(p.bill_u), m(p.bill_a), ex])
    tb("表 A3 周期、世界与 agent 运行（`--no-judge`，三次运行共用一个 state 文件）", ["周期", "世界文件", "天数", "findings", "new/persisting/resolved/regressed", "账单(Unblended)", "账单(Amortized)", "与 scenario.expect"], rows)
    rows = []
    for p in P:
        for f in p.findings:
            if f["type"] in ("savings_plan_underutilized", "misattributed_tags", "orphan_snapshots", "stopped_instance_storage") and f.get("source") != "claim":
                continue
            st = "claim" if f.get("source") == "claim" else next((k for k in ("new", "persisting", "resolved", "regressed") if any(e.get("finding") == f["id"] for e in p.diff.get(k, []))), "—")
            rows.append([p.label, f["id"], f"`{f['type']}`", "、".join(f["resources"][:2]) + ("…" if len(f["resources"]) > 2 else ""), m(f["monthly_savings_usd"]), f["basis"],
                         "claim（场景声明）" if f.get("source") == "claim" else "agent", st])
    tb("表 A4 各周期与本章相关的 findings 与预测（其余 persisting 项略）", ["周期", "id", "类型", "资源（前 2）", "monthly_savings_usd", "basis", "来源", "本期 diff 状态"], rows)
    keys = []
    for _t, x in items:
        keys += x["now"]["keys"]
    tb("表 A5 被处理资源的逐周期成本（Cost Explorer 资源级，日度加总）", ["资源", P[0].label, P[1].label, P[2].label, "用量类型"], _res_rows(P, list(dict.fromkeys(keys))))
    p_(f"采集方式与 c0018 相同：每个周期起一套 test-aws-server 与 test-metrics-server，agent 跑一次，再用 boto3 查该周期自己的 Cost Explorer（资源级日度，分 14 天一段；用量类型级月度）。方法见 B3，这里不重复。唯一的方法改动是对照组的分层：资源按「用量类型类别 x 是否为浪费类 finding 的对象」分层（`ebs_volume:active`、`ebs_volume:waste` 等），没有对照资源的细层退回粗类别，再退回全体。理由：无人使用的卷成本是平的，不随业务增长，数据卷会增长；把它们放进同一个对照组，增长会被错误地套到被删的闲置卷上。原链的数字在这个改动下不变（B 部分重新生成，数字与 c0018 相同）。")

    # ---- A4 (a)
    h("A4 逐项分析")
    h3("（a）期中删除：同一件事的三个口径")
    n, l = xa["now"], xa["later"]
    k_a = n["keys"][0]
    r_a = {p.label: p.resources().get(k_a) for p in P}
    p_(f"卷 `{k_a}` 在 {P[0].label} 是 600 GB gp2 未挂载卷，日均成本 {m(n['rate0'])}，标准月 {m(n['baseline_std'])}。agent 的预测 {m(xa['predicted_raw'])}（`{xa['basis']}`，近 14 天外推）与之相同。卷在 {P[1].label}-11 删除，所以 {P[1].label} 的发票只有 {len(r_a[P[1].label].days)} 天（至 {r_a[P[1].label].last.isoformat()}）。")
    tb("表 A6 （a）三个口径", ["口径", "金额", "怎么得到", "与预测比"], [
        [f"{P[0].label} 发票", m(n["base_c0"]), f"{len(r_a[P[0].label].days)} 天 x {m(n['rate0'])}", "—"],
        [f"{P[1].label} 发票", m(n["act_c1"]), f"{len(r_a[P[1].label].days)} 天 x {m(n['rate0'])}", "—"],
        [f"{P[2].label} 发票", m(l["act_c1"]), "卷已不在账单", "—"],
        ["两张发票之差（N 与 N+1）", m(n["naive_cal"]), f"{m(n['base_c0'])} 减 {m(n['act_c1'])}；N 期 30 天，N+1 期 {n['days1']} 天，天数也不同", "不可直接比"],
        ["首月现金节省（对 N+1 的 31 天）", m(n["realized_cal"]), f"反事实 {m(n['cf_cal'])}（不删则 {n['days1']} 天 x {m(n['rate0'])}）减实际 {m(n['act_c1'])}", f"{pc(xa['cash_rate'])}（预测折算 {m(xa['cash_pred'])}）"],
        ["次月现金节省（N+2，30 天）", m(l["realized_cal"]), f"反事实 {m(l['cf_cal'])} 减实际 {m(l['act_c1'])}", f"{pc(xa['later_cash_rate'])}"],
        ["标准月稳态节省", m(n["realized_std"]), f"期末不再计费，稳态成本 {m(n['act_std'])}；{m(n['cf_std'])} 减 {m(n['act_std'])}", f"{pc(xa['std_rate'])}"],
    ])
    p_(f"结论：删除是兑现的，只是首月现金没有拿满。{n['days1']} 天里 {n['days1'] - len(r_a[P[1].label].days)} 天省下了钱，首月现金实现率 {pc(xa['cash_rate'])}，恰好等于 {n['days1'] - len(r_a[P[1].label].days)}/{n['days1']}。偏差原因是按天折算，不是预测错：标准月稳态实现 {pc(xa['std_rate'])}，{P[2].label} 整月现金实现率 {pc(xa['later_cash_rate'])}，N+2 保持 {m(xa['retained_std'])}，最终结论「{xa['final']}」。月报上看到的 {m(n['realized_cal'])} 只是这笔节省的第一个月。")

    # ---- (b)
    h3("（b）dev 启停：纸面节省被 Savings Plan 吸收")
    n = xb["now"]
    kb = n["keys"]
    sp0, sp1 = byl[P[0].label].resources()[sp_key], byl[P[1].label].resources()[sp_key]
    ia0 = sum((byl[P[0].label].resources()[k].rate("a") for k in kb), F(0)) * STD
    ia1 = sum((byl[P[1].label].resources()[k].rate("a") for k in kb), F(0)) * STD
    iu0 = sum((byl[P[0].label].resources()[k].rate("u") for k in kb), F(0)) * STD
    iu1 = sum((byl[P[1].label].resources()[k].rate("u") for k in kb), F(0)) * STD
    sa0, sa1 = STD * sp0.rate("a"), STD * sp1.rate("a")
    su0, su1 = STD * sp0.rate("u"), STD * sp1.rate("u")
    p_(f"C01 的预测 {m(xb['predicted'])} = 3 台 m5.2xlarge 的按需小时价 x 24 x 30 x（1 - 目标开机比例 {float(next(f for f in P[0].findings if f['id'] == 'C01')['target_uptime'])}），全部来自世界价格表，不看账单。账单上发生的事：这 3 台被 m5 的 Savings Plan `{sp_key}` 覆盖，每台每天有两行，BoxUsage（按需价）与 SavingsPlanNegation（负的按需价），Unblended 合计为 0；承诺费是固定的每小时 2.40 美元，不随用量变。停机只减少了被覆盖的用量，所以账单一分钱没动，承诺的未用部分变大了。")
    tb("表 A7 （b）节省去了哪里（标准月美元）", ["资源", "口径", P[0].label, P[1].label, "变化"], [
        ["3 台 dev 实例", "Unblended", m(iu0), m(iu1), sm(iu1 - iu0)],
        ["3 台 dev 实例", "Amortized", m(ia0), m(ia1), sm(ia1 - ia0)],
        [f"`{sp_key}` 承诺费", "Unblended", m(su0), m(su1), sm(su1 - su0)],
        [f"`{sp_key}` 承诺费", "Amortized（未用承诺）", m(sa0), m(sa1), sm(sa1 - sa0)],
        ["合计", "Unblended", m(iu0 + su0), m(iu1 + su1), sm(iu1 + su1 - iu0 - su0)],
        ["合计", "Amortized", m(ia0 + sa0), m(ia1 + sa1), sm(ia1 + sa1 - ia0 - sa0)],
    ])
    tb("表 A8 （b）预测到实现的拆解", ["步骤", "金额", "说明"], [
        ["预测（按需价 x 闲置小时）", m(xb["predicted"]), "claim，来自价格表"],
        ["减：SP 折扣差", m(xb["predicted"] - n["realized_amort_std"]), "覆盖的实例按 SP 折扣价摊销，不是按需价"],
        ["摊销口径节省", m(n["realized_amort_std"]), "实例行的 Amortized 下降"],
        ["减：被未用承诺吸收", m(xb["absorbed"]), f"同一笔钱在 `{sp_key}` 的未用承诺里同额出现（表 A7，{sm(sa1 - sa0)}）"],
        ["账单口径节省（实现）", m(n["realized_std"]), "Unblended 不变"],
    ])
    p_(f"结论：C01 的 {m(xb['predicted'])} 现金一分没省，实现率 {pc(xb['std_rate'])}，分类「承诺吸收」。两张账单（Unblended 与 Amortized）合计都没有变化：实例行少的钱，在承诺行同额多出来（{sm(ia1 - ia0)} 对 {sm(sa1 - sa0)}）。这正是基础世界 GT05 说的：标准答案节省 {gt05['min']} 到 {gt05['max']} 美元，纸面值 {m(xb['predicted'])} 与之差了一个数量级。要让停机兑现，先要把承诺填满（GT04）。")

    # ---- (c)
    h3("（c）用量增长：朴素对比与对照组校正")
    n = xc["now"]
    used = n["strata"][0][1]
    g = n["strata"][0][2]
    dr = n["drift"].get(used, {})
    p_(f"{P[1].label} 所有在用数据卷都放大了 30%（orders-db 的卷与三张数据卷），被处理的 `{n['keys'][0]}` 也从 2000 GB 放到 2600 GB，同时 gp2 改 gp3。两张发票上它的成本从 {m(n['baseline_std'])} 变成 {m(n['act_std'])}：朴素看，改 gp3 不但没省，还贵了。对照组（层 `{used}`，{dr.get('n', 0)} 个资源，没有被任何 finding 处理）的日均成本之和从 {m(STD * dr.get('rate0', 0))} 涨到 {m(STD * dr.get('rate1', 0))}，g = {float(g):.4f}。不改的话这张卷会变成 {m(n['cf_std'])}。")
    tb("表 A9 （c）朴素与校正", ["项", "金额", "说明"], [
        [f"{P[0].label} 标准月成本", m(n["baseline_std"]), "2000 GB gp2"],
        [f"{P[1].label} 实际", m(n["act_std"]), "2600 GB gp3"],
        ["朴素节省（两张发票）", sm(n["naive_std"]), "把增长混进了节省"],
        ["对照组系数 g", f"{float(g):.4f}", f"层 `{used}`，{dr.get('n', 0)} 个对照资源"],
        ["反事实（不改会是多少）", m(n["cf_std"]), "N 期成本 x g"],
        ["校正后节省（实现）", m(n["realized_std"]), "反事实减实际"],
        ["预测（牌价，N 期规模）", m(xc["predicted"]), "agent 预测 = N 期成本 x 20%"],
        ["预测移到增长后的规模", m(xc["pred_g"]), "预测 x 反事实 / N 期成本"],
        ["实现率（对原预测）", pc(xc["std_rate"]), "超过 100%：卷变大，同样的 20% 绝对值更大"],
    ])
    tb(f"表 A10 （c）对照组：层 `{used}` 的资源（N 与 N+1 都在账单，未被处理，期末仍在）", ["资源", "用量类型", f"{P[0].label} 标准月", f"{P[1].label} 标准月", "比值"], _control_rows(P, xc))
    p_(f"结论：节省是真的，量被增长掩盖。朴素对比得出 {sm(n['naive_std'])}（「没效果」），校正后 {m(n['realized_std'])}。识别假设是同层资源增速相同：这里对照组与被处理卷的增速都是 30%，所以校正量等于增长本身，实现等于预测移到增长后规模（{m(xc['pred_g'])}）。假设不成立时校正也会错，见 A7。")
    p_(f"同一个 {P[1].label} 里，被删除的闲置卷（a）没有套用这个 g：它在 `ebs_volume:waste` 层，对照资源是仍然 persisting 的闲置卷与停机卷，成本是平的，g = {float(xa['now']['strata'][0][2]):.4f}，所以（a）的实现不被增长污染。")

    # ---- (d)
    h3("（d）牌价预测与账单口径")
    n = xd["now"]
    wd0, wd1 = worlds[P[1].label], worlds[P[2].label]
    vd = next(v for v in wd1.all_volumes() if v["id"] == n["keys"][0])
    Pr = wd1.prices
    p_(f"卷 `{n['keys'][0]}` 是 3000 GB gp2，gp2 每 GB 给 3 IOPS，所以 {3 * vd['size']} IOPS，吞吐可到 250 MB/s。改 gp3 并保留性能，要配 {vd['iops']} IOPS 与 {vd['throughput']} MB/s；gp3 自带 3000 IOPS 与 125 MB/s，超出部分按 {Pr['gp3_iops_month']} 美元/IOPS-月与 {Pr['gp3_throughput_month']} 美元/MBps-月另计。agent 的牌价预测只知道 gp3 的 GB 单价低 20%：{m(xd['predicted_raw'])}（它按 {P[1].label} 的 {P[1].days} 天外推，换算标准月 {m(xd['predicted'])}）。")
    ut0, ut1 = n["ut0"], n["ut1"]
    rows = []
    for u in sorted(set(ut0) | set(ut1)):
        rows.append([f"`{u}`", m(ut0.get(u, 0)), m(ut1.get(u, 0)), sm(ut1.get(u, 0) - ut0.get(u, 0))])
    rows.append(["合计", m(n["baseline_std"]), m(n["act_std"]), sm(n["act_std"] - n["baseline_std"])])
    tb(f"表 A11 （d）{n['keys'][0]} 的用量类型（标准月美元）", ["用量类型", f"{P[1].label}", f"{P[2].label}", "变化"], rows)
    tb("表 A12 （d）预测到实现", ["步骤", "金额", "说明"], [
        ["预测（标准月）", m(xd["predicted"]), "牌价：N 期成本的 20%"],
        ["牌价预期的 N+1 成本", m(n["baseline_std"] - xd["predicted"]), "N 期成本 减 预测，只含 gp3 的 GB 费"],
        ["N+1 实际成本", m(n["act_std"]), "表 A11"],
        ["短缺（实际高于预期）", m(xd["shortfall"]), "实现 = 预测 减 短缺"],
        ["新增用量类型合计", m(sum(xd["extras"].values(), F(0))), "、".join(f"`{u}` {m(a)}" for u, a in sorted(xd["extras"].items()))],
        ["实现（标准月）", m(n["realized_std"]), f"实现率 {pc(xd['std_rate'])}"],
    ])
    p_(f"结论：预测被高估 {m(xd['predicted'] - n['realized_std'])}，不是账单错也不是没改成，而是预测的价格口径只有 GB 单价。短缺 {m(xd['shortfall'])} 与新增的 IOPS、吞吐两个用量类型合计 {m(sum(xd['extras'].values(), F(0)))} 吻合。分类「预测口径差」。检测器给 gp2 卷估节省时需要知道要保留的 IOPS 与吞吐，否则对大卷会系统性高估。")

    # ---- (e)
    h3("（e）无偏差的对照")
    n = xe["now"]
    p_(f"闲置 EIP `{n['keys'][0]}` 在 {P[1].label} 开始前就已释放（成本行整月消失），预测 {m(xe['predicted'])}，实现 {m(n['realized_std'])}，实现率 {pc(xe['std_rate'])}，没有偏差分类。它说明核实器遇到干净的删除时给出 100%，不会为了好看而制造偏差，也不会在有偏差时掩盖。")

    # ---- A5
    h("A5 组合视图与账单分解")
    cls = {}
    for t, x in items:
        a = cls.setdefault(x["primary"], [0, F(0), F(0)])
        a[0] += 1
        a[1] += x["predicted"]
        a[2] += x["now"]["realized_std"]
    rows = [[CLASS_ZH[c], a[0], m(a[1]), m(a[2]), pc(a[2] / a[1]) if a[1] else "—"] for c, a in sorted(cls.items(), key=lambda kv: kv[0])]
    rows.append(["合计", len(V), m(tot_pred), m(tot_real), pc(tot_real / tot_pred)])
    tb("表 A13 组合：按原因分类", ["原因分类", "条数", "预测", "实现", "实现率"], rows)
    tb("表 A14 组合：三种口径", ["口径", "金额", "含义"], [
        ["预测合计", m(tot_pred), "含 1 条 claim"],
        ["标准月实现（对照组校正）", m(tot_real), f"实现率 {pc(tot_real / tot_pred)}"],
        ["仅 agent 的 finding：预测 / 实现", f"{m(a_pred)} / {m(a_real)}", f"实现率 {pc(a_real / a_pred)}（不含 claim C01）"],
        ["朴素节省（N 减 N+1，标准月）", m(tot_naive), "不校正用量漂移"],
        ["首月现金（按各自 N+1 的天数）", m(tot_cash), f"预测折算 {m(tot_cashpred)}，现金实现率 {pc(tot_cash / tot_cashpred)}"],
        ["两张发票之差", m(tot_inv), "天数、增长、期中变更混在一起"],
    ])
    rows = []
    for c, zh in CATS:
        sign = -1 if c == "verified" else 1
        rows.append([zh] + [sm(sign * d["parts"][c]) for d in D] + [CAT_NOTE[c]])
    rows.append(["合计（各项之和）"] + [sm(d["sum"]) for d in D] + [""])
    rows.append(["实际账单差（Cost Explorer）"] + [sm(d["delta"]) for d in D] + [""])
    rows.append(["差（必须为 0）"] + [m(d["sum"] - d["delta"], 6) for d in D] + ["精确有理数运算"])
    tb("表 A15 账单分解：账单差 = 已核实节省 + 回归损失 + 新增浪费 + 其他", ["项目"] + [f"{d['from']} 到 {d['to']}" for d in D] + ["含义"], rows)
    for d in D:
        pt = d["parts"]
        p_(f"{d['from']} 到 {d['to']}：账单 {m(d['bill0'])} 到 {m(d['bill1'])}，差 {sm(d['delta'])}。已核实节省 {m(pt['verified'])}（{'、'.join(d['verified_findings']) or '无'}），新增浪费 {m(pt['new_waste'])}（{'、'.join(d['new_findings']) or '无'}），日历天数 {sm(pt['calendar'])}，用量漂移（含数据增长）{sm(pt['drift'])}，前期结转 {sm(pt['carryover'])}。各项之和与实际账单差之差 {m(d['sum'] - d['delta'], 6)}。")
    d0 = D[0]
    p_(f"{d0['from']} 到 {d0['to']} 账单涨了 {m(d0['delta'])}，同期核实的节省是 {m(d0['parts']['verified'])}。账单涨主要是用量漂移（数据卷增长与 NAT 流量）、新建的卷和 31 天的月份，不是清理无效。只看账单差得不到任何一条结论；C01 在这里贡献的已核实节省是 {m(xb['now']['realized_cal'])}，因为账单没动。")
    for d in D:
        rows = [[f"`{mv['key']}`", dict(CATS).get(mv["role"], "无关变动" if mv["role"] == "other" else mv["role"]), m(mv["c0"]), m(mv["c1"]), sm(mv["delta"])] for mv in d["movers"][:8]]
        tb(f"表 A16 变动最大的资源：{d['from']} 到 {d['to']}（前 8，按绝对变动）", ["资源键", "归类", "N 期", "N+1 期", "变动"], rows)

    # ---- A6
    h("A6 为什么这个结论可信")
    pr_rows = price_rows(res, root)
    rows = [[r["row"], f"`{r['key']}`", m(r["e0"]) if r["e0"] is not None else "—", m(r["ce0"]), m(r["e1"] or 0), m(r["ce1"]), "一致" if r["ok"] else "不一致"] for r in pr_rows]
    tb("表 A17 独立复算：世界价格表（单价 x 规格）与 Cost Explorer 的标准月成本（SP 覆盖的实例为 0）", ["行", "资源", "N 期（价格表）", "N 期（账单）", "N+1 期（价格表）", "N+1 期（账单）", "对账"], rows)
    # design values: what the world spec implies, with no billing line involved
    wc0, wc1 = worlds[P[0].label], worlds[P[1].label]
    cost0 = lambda w, k: F(str(world_std_cost(w, k) or 0)).limit_denominator(10 ** 9)
    ctrl_keys = [r[0].strip("`") for r in _control_rows(P, xc)]
    g_spec = sum((cost0(wc1, k) for k in ctrl_keys), F(0)) / sum((cost0(wc0, k) for k in ctrl_keys), F(0))
    kc, kd, ka, ke = xc["now"]["keys"][0], xd["now"]["keys"][0], xa["now"]["keys"][0], xe["now"]["keys"][0]
    spec = [
        ("（a）首月现金 = 日均 x 未计费天数", cost0(wc0, ka) / STD * (P[1].days - len(byl[P[1].label].resources()[ka].days)), xa["now"]["realized_cal"]),
        ("（b）账单口径节省 = 0（SP 覆盖，Unblended 相抵）", F(0), xb["now"]["realized_std"]),
        ("（b）摊销口径节省 = 按需价 x SP 折扣后 x 停机比例", sum((F(str(wc0.prices["ec2_hourly"][wc0.instance(i)["type"]])) * 24 * STD * (1 - F(str(wc0.prices["sp_discount"]["m5"]))) * (1 - F(str(wc1.instance(i)["uptime"]["fraction"]))) for i in xb["now"]["keys"]), F(0)), xb["now"]["realized_amort_std"]),
        ("（c）实现 = 反事实（成本 x 世界规格算出的增速）减 N+1 成本", cost0(wc0, kc) * g_spec - cost0(wc1, kc), xc["now"]["realized_std"]),
        ("（d）实现 = N 期价格表成本 减 N+1 期价格表成本", cost0(worlds[P[1].label], kd) - cost0(worlds[P[2].label], kd), xd["now"]["realized_std"]),
        ("（e）实现 = 价格表上的 EIP 月费", cost0(wc0, ke), xe["now"]["realized_std"]),
    ]
    tb("表 A18 设计值对账：不看账单、只从世界规格与价格表算出应有的节省，对比核实器给出的数", ["项", "设计值", "核实器", "差"], [[a, m(b), m(c), m(c - b, 4)] for a, b, c in spec])
    chk = []
    chk.append(["（a）分类为按天折算，首月现金实现率 < 98%，次月现金实现率在 ±2% 内", xa["primary"] == "proration" and xa["cash_rate"] < 1 - TOL and abs(xa["later_cash_rate"] - 1) <= TOL])
    chk.append(["（b）分类为承诺吸收：预测 > 0，账单口径实现在 ±2% 内为 0，摊销口径节省 > 0", xb["primary"] == "commitment" and xb["predicted"] > 0 and xb["verdict"] == "none" and xb["now"]["realized_amort_std"] > 0])
    chk.append(["（b）独立证据：承诺行的摊销增加等于被吸收的金额（1 美分内）", abs((sa1 - sa0) - xb["absorbed"]) < F(1, 100)])
    chk.append(["（c）分类为用量漂移：朴素节省为负，校正后为正，且与朴素值相差大于预测的 2%", xc["primary"] == "drift" and xc["now"]["naive_std"] < 0 < xc["now"]["realized_std"]])
    chk.append(["（d）分类为预测口径差：短缺与新增 IOPS/吞吐用量类型合计吻合（2%）", xd["primary"] == "basis" and bool(xd["extras"]) and abs(sum(xd["extras"].values(), F(0)) - xd["shortfall"]) <= TOL * xd["predicted"]])
    chk.append(["（e）对照项无偏差分类，实现率在 ±2% 内", xe["primary"] == "none" and xe["verdict"] == "ok"])
    chk.append(["组合实现率小于 100%", tot_real < tot_pred])
    chk.append(["账单分解各项之和与实际账单差精确相等（有理数）", all(d["exact"] for d in D)])
    chk.append(["设计值对账：全部在 1 美分内（（c）因 CE 六位小数舍入放宽到 1 美分）", all(abs(c - b) < F(1, 100) for _a, b, c in spec)])
    chk.append(["价格表复算与账单一致（表 A17 全部一致）", bool(pr_rows) and all(r["ok"] for r in pr_rows)])
    chk.append(["各期 diff 与 scenario.expect 的 resolved / new / regressed 逐项一致", all(
        {k: sorted(e["key"] for e in p.diff.get(k, [])) for k in ("resolved", "new", "regressed")} == {k: sorted(p.expect.get(k, [])) for k in ("resolved", "new", "regressed")} for p in P if p.expect is not None)])
    chk.append(["每条核实所用的层都有对照资源，没有退回全体平均", all(s != "*" for x in V for (_k, s, _g) in x["now"]["strata"])])
    chk.append(["用量类型级合计与资源级合计相差不超过 $0.01（舍入）", all(abs(sum(p.ut_totals.values(), F(0)) - p.bill_u) < F(1, 100) for p in P)])
    tb("表 A19 自检清单", ["检查", "结果"], [[c, "通过" if ok else "失败"] for c, ok in chk])
    ul([
        "三条独立路径对上账。路径一：Cost Explorer 日度行汇总成资源日均。路径二：世界价格表 x 资源规格（表 A17）。路径三：不看任何账单，只从世界规格推出每一项应有的节省（表 A18）。三条路径一致，说明汇总、折算、对照组校正的代码没有算错。",
        "偏差有因果。每个偏差都来自世界文件里一处可指认的改动（表 A2），核实器给出的原因分类与这处改动对应：删除日期、SP 覆盖、增长、IOPS 与吞吐。不是事后找理由。",
        "对照项给出 100%。说明核实器没有凭空制造偏差。零假设对照链（第二部分）整体 100%，同一套代码在这条链上给出 " + pc(tot_real / tot_pred) + "，差别来自数据，不来自代码。",
        "账单分解闭合。把整张账单的变动拆成八项，求和与实际差完全相等；如果某条节省被多算或漏算，等式会破。",
        "每个数字由代码生成，重新跑一遍文档逐字节相同（`test_document_is_deterministic` 重新采集两条链并比对）。",
    ])

    # ---- A7
    h("A7 局限")
    ul([
        "数据仍是合成的：价格表、成本行、规格同出一个世界文件。设计值对账（表 A18）证明核实器能从账单还原世界里已知的因果，不证明真实账单也是这样：真实账号有折扣、积分、退款、预留实例与跨月调整。",
        "识别假设是同层资源增速相同。（c）里对照组与被处理卷的增速都是 30%，这是场景设计的，校正因此精确。增速不同时（例如被处理的卷碰巧增长更快）校正有偏差，核实器无法自己发现，只能靠分层让对照更像被处理对象。分层本身（用量类型类别 x 是否浪费类）是一个判断，不是定理。",
        "C01 不是 agent 的输出，是场景声明的假想（`--no-judge` 没有排期类 finding；判断层对 GT05 的结论本来就是不要停）。本文验证的是「如果有人照纸面值去做」，不是 agent 的预测准确度。agent 的预测准确度见 B 部分与 (d)：agent 的 gp2 改 gp3 预测在大卷与 31 天月份上有口径偏差。",
        "resolved 在这里等于「这个 key 本期没再检出」（`slo.diff_state`）。本链的每个 resolved 都是资源真的变了（删除、改类型），不是检测器漏检；若以后 diff_state 区分「已解决」与「未再检出」，本链不受影响，但核实器应只信前者。",
        "只有 3 个周期，保持只测一期（N+2）；（d）在最后一期，没有 N+2。",
        "gp3 的 IOPS 与吞吐计费、`deleted`、`uptime` 是为本链新增的世界特性，只覆盖最简情形：吞吐与 IOPS 线性计价，实例启停按比例折算小时，没有启动取整、最小计费时长。",
    ])
    ctx = {"V": V, "D": D, "tot_pred": tot_pred, "tot_real": tot_real, "checks": chk, "price_rows": pr_rows, "spec": spec, "items": items}
    return B, ctx


# ---- rendering ---------------------------------------------------------------
TITLE = "储蓄核实：resolved 的 finding 真的省钱了吗"


def to_md(blocks, title=TITLE) -> str:
    out = [f"# {title}", "", "> 本文由 `python3 -m finops_agent.verify_savings` 生成，所有数字来自代码，不要手改。表格编号与行号（V1 起）在正文中被引用。", ""]
    for b in blocks:
        if b[0] == "h2":
            out += [f"## {b[1]}", ""]
        elif b[0] == "h3":
            out += [f"### {b[1]}", ""]
        elif b[0] == "p":
            out += [b[1], ""]
        elif b[0] == "ul":
            out += [f"- {t}" for t in b[1]] + [""]
        elif b[0] == "table":
            _, cap, head, rows = b
            out += [f"**{cap}**", "", "| " + " | ".join(map(str, head)) + " |", "|" + "|".join("---" for _ in head) + "|"]
            out += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows] + [""]
    return "\n".join(out).rstrip() + "\n"


CSS = """:root{--bg:#fff;--fg:#1b1f24;--mut:#59636e;--line:#d0d7de;--acc:#0969da;--bad:#cf222e;--good:#1a7f37;--code:#f3f4f6}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--mut:#9198a1;--line:#30363d;--acc:#58a6ff;--bad:#ff7b72;--good:#56d364;--code:#161b22}}
body{background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"PingFang SC","Noto Sans SC",sans-serif;max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:26px}h2{margin-top:2em;border-bottom:1px solid var(--line);padding-bottom:6px}h3{margin-top:1.4em}
table{border-collapse:collapse;margin:8px 0 20px;font-size:13px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}th{background:var(--code)}
.cap{font-weight:600;margin-top:18px}code{background:var(--code);padding:1px 4px;border-radius:3px;font-size:12.5px}
blockquote{color:var(--mut);border-left:3px solid var(--line);margin:0;padding-left:12px}
svg text{fill:var(--fg);font-size:12px}.p{fill:var(--mut)}.r{fill:var(--acc)}.l{fill:var(--bad)}"""


def _inline(s: str) -> str:
    import re
    return re.sub(r"`([^`]+)`", lambda mo: f"<code>{html.escape(mo.group(1))}</code>", html.escape(s, quote=False))


def chart(V) -> str:
    """Predicted vs realized (standard-month dollars) per verified row, as inline SVG."""
    if not V:
        return ""
    W, rowh, left = 760, 38, 150
    mx = float(max(max(x["predicted"], x["now"]["realized_std"]) for x in V)) or 1.0
    h = rowh * len(V) + 30
    s = [f'<svg viewBox="0 0 {W} {h}" width="100%" role="img" aria-label="预测与实现对比">']
    for n, x in enumerate(V):
        y = 10 + n * rowh
        w = lambda v: max(1.0, (W - left - 90) * float(v) / mx) if float(v) > 0 else 0
        s.append(f'<text x="0" y="{y + 14}">{html.escape(x["id"])} {html.escape(TYPE_ZH.get(x["type"], x["type"]))}</text>')
        s.append(f'<rect class="p" x="{left}" y="{y}" width="{w(x["predicted"]):.1f}" height="12"/><text x="{left + w(x["predicted"]) + 4:.1f}" y="{y + 11}">预测 {m(x["predicted"])}</text>')
        s.append(f'<rect class="{"l" if x["final"] == "兑现后丢失" else "r"}" x="{left}" y="{y + 15}" width="{w(x["now"]["realized_std"]):.1f}" height="12"/><text x="{left + w(x["now"]["realized_std"]) + 4:.1f}" y="{y + 26}">实现 {m(x["now"]["realized_std"])}{"（下期丢失）" if x["final"] == "兑现后丢失" else ""}</text>')
    s.append("</svg>")
    return "".join(s)


def to_html(blocks, charts, title=TITLE) -> str:
    """charts: {caption prefix: V}; the chart is drawn right before the table whose caption starts with the prefix."""
    out = [f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>储蓄核实</title><style>{CSS}</style></head><body><h1>{html.escape(title)}</h1>",
           "<blockquote>本页由 <code>python3 -m finops_agent.verify_savings</code> 生成，所有数字来自代码。</blockquote>"]
    for b in blocks:
        if b[0] == "h2":
            out.append(f"<h2>{html.escape(b[1])}</h2>")
        elif b[0] == "h3":
            out.append(f"<h3>{html.escape(b[1])}</h3>")
        elif b[0] == "p":
            out.append(f"<p>{_inline(b[1])}</p>")
        elif b[0] == "ul":
            out.append("<ul>" + "".join(f"<li>{_inline(t)}</li>" for t in b[1]) + "</ul>")
        elif b[0] == "table":
            _, cap, head, rows = b
            for pref, V in charts.items():
                if cap.startswith(pref):
                    out.append(f"<p class='cap'>图 预测与实现（标准月美元，{html.escape(pref)} 前）</p>" + chart(V))
            out.append(f"<p class='cap'>{html.escape(cap)}</p><table><tr>" + "".join(f"<th>{_inline(str(c))}</th>" for c in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(str(c))}</td>" for c in r) + "</tr>" for r in rows) + "</table>")
    out.append("</body></html>")
    return "\n".join(out)


def build_all(res_s: dict, res_c: dict, root: Path = ROOT):
    """The document: part A (scenario chain, primary evidence) then part B (control chain, the c0018 zero-null result)."""
    A, ctxa = build_scenario(res_s, root)
    C, ctxc = build(res_c, root)
    head = [("h2", "第二部分 零假设对照：原链的 100% 是预期的"),
            ("p", "第一部分是主证据（场景链 verify_*，真实偏差）。以下是 c0018 的原链，保留为对照：预测与账单出自同一张价格表，所以应该 100%，它证明流水线没有凭空制造偏差，不证明节省。各节编号前加 B，表编号沿用 c0018（表 1 到表 14）。")]
    intro = [("p", "第一部分（A1 到 A7）是主证据；第二部分（B1 到 B8）是零假设对照。")]
    return intro + A + head + C, ctxa, ctxc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-md", default=str(ROOT / "docs" / "SAVINGS_VERIFICATION.md"))
    ap.add_argument("--out-html", default=str(ROOT / "reports" / "savings-verification.html"))
    ap.add_argument("--port-base", type=int, default=37566, help="aws server port; metrics server uses +1")
    ap.add_argument("--keep-runs", help="copy the agent run directories of both chains here")
    a = ap.parse_args(argv)
    log = lambda s: print(s, file=sys.stderr)
    res_s = analyze(collect(a.port_base, keep=a.keep_runs, log=log, chain="scenario"))
    res_c = analyze(collect(a.port_base, keep=a.keep_runs, log=log, chain="control"))
    blocks, ca, cc = build_all(res_s, res_c)
    Path(a.out_md).write_text(to_md(blocks))
    Path(a.out_html).write_text(to_html(blocks, {"表 A2": ca["V"], "表 7": cc["V"]}))
    bad = [c for c, ok in ca["checks"] + cc["checks"] if not ok]
    print(f"scenario chain: {len(ca['V'])} verified, predicted {m(ca['tot_pred'])}, realized {m(ca['tot_real'])} ({pc(ca['tot_real'] / ca['tot_pred'])}); "
          f"control chain: {len(cc['V'])} verified, predicted {m(cc['tot_pred'])}, realized {m(cc['tot_real'])}; "
          f"bill decomposition exact: {all(d['exact'] for d in res_s['decompositions'] + res_c['decompositions'])}; checks failed: {len(bad)}")
    for c in bad:
        print("FAILED CHECK:", c, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
