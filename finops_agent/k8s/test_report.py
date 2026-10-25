"""Tests for finops_agent.k8s.report (rendering only; fixtures from the synthetic K8s world, see fixtures/make_fixtures.py)."""
import copy
import html
import json
import re
import tempfile
import unittest
from pathlib import Path

from finops_agent.k8s import report as R

FX = Path(__file__).resolve().parent / "fixtures"


def jl(p):
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]


def inputs(**over):
    d = {"decisions": json.loads((FX / "day2/k8s_decisions.json").read_text()),
         "prev": json.loads((FX / "day1/k8s_decisions.json").read_text()),
         "plans": json.loads((FX / "day2/k8s_plans.json").read_text()),
         "state": json.loads((FX / "k8s_state.json").read_text()),
         "actions": jl(FX / "day2/k8s_actions.jsonl"),
         "verify": json.loads((FX / "day2/k8s_verify.json").read_text()),
         "unit_cost": json.loads((FX / "day2/k8s_unit_cost.json").read_text()),
         "score": json.loads((FX / "score.json").read_text())}
    d.update(over)
    return d


def cards(page):
    return re.findall(r"<article class=card id='([^']+)'>(.*?)</article>", page, re.S)


def text(frag):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", frag)))


def nums(obj, out=None):
    """All numbers in a JSON value: numeric leaves and digit runs inside strings."""
    out = set() if out is None else out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        out.update(float(m) for m in re.findall(r"\d+(?:\.\d+)?", obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            nums(v, out)
    elif isinstance(obj, list):
        for v in obj:
            nums(v, out)
    return out


def allowed(d, plan):
    base = nums(d) | nums(plan)
    u, c, new = d["usage"], d["current"], d["recommendation"].get("request")
    top = u["p99"] if d["resource"] == "cpu" else u["max"]
    der = set()
    for a, b in ((u["p99"], new), (u["max"], new), (u["p99"], c["request"])):
        if a is not None and b:
            der.add(a / b)
    if top and new:
        der.add(new / top - 1)
    return base | {x * 100 for x in base} | der | {x * 100 for x in der}


def close(v, pool, rel, ab=0.0):
    return any(abs(v - p) <= max(rel * abs(p), ab) for p in pool)


def ungrounded(card_html, d, plan):
    t = text(card_html)
    pool = allowed(d, plan)
    bad = []
    for m in re.findall(r"\$(\d[\d,]*\.\d\d)", t):
        if not close(float(m.replace(",", "")), pool, 0, 0.006):
            bad.append("$" + m)
    t2 = re.sub(r"\$\d[\d,]*\.\d\d", " ", t)
    for m in re.findall(r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)%", t2):
        if not close(float(m), pool, 0.006):
            bad.append(m + "%")
    t3 = re.sub(r"\d+(?:\.\d+)?%", " ", t2)
    for m in re.findall(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?", t3):
        if not close(float(m), pool, 1e-3):
            bad.append(m)
    return bad


class DailyTest(unittest.TestCase):
    def setUp(self):
        self.inp = inputs()
        self.page = R.render("daily", self.inp)
        self.decs = self.inp["decisions"]["decisions"]

    def test_six_sections_once_in_order(self):
        cs = cards(self.page)
        want = {d["id"] for d in self.decs if d["verdict"] in ("ALLOW", "REQUIRE_APPROVAL")}
        self.assertEqual({i for i, _ in cs}, want)
        for i, c in cs:
            self.assertEqual(re.findall(r"<h4>(.*?)</h4>", c), R.SECTION_ORDER, i)

    def test_summary_money_is_sum_of_allow_realizable(self):
        want = sum(d["savings"]["realizable_usd_month"] for d in self.decs if d["verdict"] == "ALLOW")
        self.assertGreater(want, 0)
        m = re.search(r"data-realizable='([^']+)'>([^<]+)<", self.page)
        self.assertAlmostEqual(float(m.group(1)), want, places=4)
        self.assertEqual(m.group(2), f"${want:,.2f}/月")
        rv = sum(d["savings"]["request_value_usd_month"] for d in self.decs if d["verdict"] == "ALLOW")
        self.assertAlmostEqual(float(re.search(r"data-request-value='([^']+)'", self.page).group(1)), rv, places=4)
        self.assertIn("口径不同", self.page)

    def test_denied_and_unknown_in_table_with_needs(self):
        m = re.search(r"<h2>被拒与数据不足</h2>.*?(<table.*?</table>)", self.page, re.S)
        tbl = m.group(1)
        rows = re.findall(r"<tr>(.*?)</tr>", tbl, re.S)[1:]
        sel = [d for d in self.decs if d["verdict"] in ("DENY", "UNKNOWN")]
        self.assertEqual(len(rows), len(sel))
        for d in sel:
            row = next(r for r in rows if f"{d['namespace']}/{d['workload']}" in r and ("CPU" if d["resource"] == "cpu" else "内存") in text(r))
            for need in d["unknown_needs"]:
                self.assertIn(html.escape(need), row)

    def test_cards_are_grounded(self):
        by_id = {d["id"]: d for d in self.decs}
        plans = R._plans_by_decision(self.inp)
        for i, c in cards(self.page):
            self.assertEqual(ungrounded(c, by_id[i], plans.get(i)), [], i)

    def test_grounding_check_catches_a_made_up_number(self):
        i, c = cards(self.page)[0]
        d = next(x for x in self.decs if x["id"] == i)
        self.assertEqual(ungrounded(c.replace("<h4>结论</h4>", "<h4>结论</h4><p>能省 $9876.54 和 73.1%</p>"), d, None)[:2], ["$9876.54", "73.1%"])

    def test_zero_realizable_explained(self):
        zero = next(d for d in self.decs if d["verdict"] == "ALLOW" and d["savings"]["realizable_usd_month"] == 0)
        c = dict(cards(self.page))[zero["id"]]
        self.assertIn("降了预留但节点数下不来，所以账单不变", text(c))
        pos = next(d for d in self.decs if d["verdict"] == "ALLOW" and d["savings"]["realizable_usd_month"] > 0)
        self.assertIn(f"节点数从 {pos['savings']['nodes_before']} 到 {pos['savings']['nodes_after']}", text(dict(cards(self.page))[pos["id"]]))

    def test_utilization_is_p99_over_new_request(self):
        d = next(x for x in self.decs if x["id"] == "K-shop-api-steady-cpu")
        want = R.pct(d["usage"]["p99"] / d["recommendation"]["request"])
        self.assertIn(want, text(dict(cards(self.page))[d["id"]]))

    def test_plan_fields_and_missing_plan(self):
        c = text(dict(cards(self.page))["K-shop-api-steady-cpu"])
        for w in ("kubectl --context", "回滚命令", "观察期", "停止条件", "canary 范围", "runs/approvals/"):
            self.assertIn(w, c)
        page = R.render("daily", inputs(plans=None))
        self.assertIn("还没有执行计划", page)

    def test_narrative_labelled_only_when_present(self):
        self.assertEqual(self.page.count("LLM 撰写"), 1)
        i = copy.deepcopy(self.inp)
        for d in i["decisions"]["decisions"]:
            d["narrative"] = None
        self.assertNotIn("LLM 撰写", R.render("daily", i))

    def test_legend_and_diff(self):
        for w in ("可以做", "需要你拍板", "不能动", "数据不够", "无需调整", "账面释放的预留", "节点能缩才兑现的钱"):
            self.assertIn(w, self.page)
        self.assertIn("判定变化", self.page)
        self.assertIn("K-shop-burning-slo-cpu", text(self.page.split("和上次比")[1].split("今天要你处理的")[0]))

    def test_safe_halt_banner(self):
        self.assertNotIn("class=halt", self.page)
        st = copy.deepcopy(self.inp["state"])
        st["safe_halt"] = True
        for v in R.VIEWS:
            p = R.render(v, inputs(state=st))
            self.assertIn("safe_halt 已置位", p)
            self.assertLess(p.index("class=halt"), p.index("<h1>"))

    def test_missing_optional_inputs_do_not_crash(self):
        only = {"decisions": self.inp["decisions"]}
        for v in R.VIEWS:
            self.assertIn("<html", R.render(v, only))
            self.assertIn("<html", R.render(v, {}))
        self.assertIn("没有上次的决策文件", R.render("daily", only))

    def test_ledger_as_dict_keyed(self):
        st = {"safe_halt": False, "proposals": {"shop/api-steady/cpu": {"state": "verified"}}}
        self.assertIn("台账：已核实", R.render("daily", inputs(state=st)))


class WeeklyMonthlyTest(unittest.TestCase):
    def test_weekly_without_verify(self):
        for v in (None, []):
            p = R.render("weekly", inputs(verify=v))
            self.assertIn("本周没有已核实的变更", p)

    def test_weekly_content(self):
        p = text(R.render("weekly", inputs()))
        for w in ("已验证", "已回滚", "被拒绝", "失败", "p99_seconds > 0.2", "实现率", "节点装箱碎片", "台账流转", "已提案"):
            self.assertIn(w, p)
        self.assertIn("93.8%", p)

    def test_ledger_flow_counts(self):
        f = R.ledger_flow(R.ledger(inputs())[0])
        self.assertEqual([f["reached"][s] for s in R.FLOW], [6, 3, 2, 1])
        self.assertEqual(f["current"]["expired"], 1)

    def test_monthly_quality(self):
        q = R.quality(inputs())
        self.assertAlmostEqual(q["verified_net"], 131.52)
        self.assertAlmostEqual(q["realization"], 131.52 / 140.16)
        self.assertEqual(q["reached"], 3)                    # verified, rolled_back, failed; the refused one never touched the cluster
        self.assertAlmostEqual(q["rollback_rate"], 1 / 3)
        self.assertAlmostEqual(q["safe_rate"], 1 / 3)
        self.assertEqual(q["unsafe_allow"], 0)
        self.assertEqual(q["gate_refused"], 1)

    def test_monthly_text(self):
        p = text(R.render("monthly", inputs()))
        self.assertIn("eval/EXPERIMENTS.md", p)
        self.assertIn("每百万请求成本", p)
        self.assertIn("$4.62", p)                            # 840.96 / 182e6 * 1e6
        self.assertIn("unsafe ALLOW", p)

    def test_monthly_without_inputs(self):
        p = text(R.render("monthly", {"decisions": inputs()["decisions"]}))
        self.assertIn("不可得", p)
        self.assertIn("没有核实记录", p)
        self.assertIn("未提供考卷分数", p)


class CliTest(unittest.TestCase):
    def test_cli_writes_file_without_local_paths(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "d.html"
            rc = R.main(["--run", str(FX / "day2"), "--prev", str(FX / "day1"), "--state", str(FX / "k8s_state.json"),
                         "--view", "daily", "--out", str(out)])
            self.assertEqual(rc, 0)
            s = out.read_text()
            self.assertIn("日报", s)
            self.assertNotIn("/Users/", s)

    def test_bad_view(self):
        with self.assertRaises(ValueError):
            R.render("yearly", {})


if __name__ == "__main__":
    unittest.main()
