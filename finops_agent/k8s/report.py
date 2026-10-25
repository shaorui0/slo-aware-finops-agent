"""Periodic K8s report: one data model, three Chinese HTML views (daily / weekly / monthly).

Contract: openspec/changes/c0032-k8s-report/specs/k8s-report/spec.md (decision/plan/ledger shapes: c0027 k8s-mainline).
Read-only: renders what the pipeline wrote; every optional input may be missing and is then reported as missing, never guessed.
Every number in a daily card comes from the decision JSON (or its plan), or is one division away from two of its numbers.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from finops_agent.report import CSS as BASE_CSS

VIEWS = ("daily", "weekly", "monthly")
VIEW_TITLE = {"daily": "K8s requests 日报", "weekly": "K8s requests 周报", "monthly": "K8s requests 月报"}
VIEW_QUESTION = {"daily": "今天有什么要我处理？", "weekly": "上周做的改动真的省了吗？有没有伤到服务？",
                 "monthly": "这套东西值不值得信？"}
VERDICT_ZH = {"ALLOW": "可以做", "REQUIRE_APPROVAL": "需要你拍板", "DENY": "不能动", "UNKNOWN": "数据不够", "NOOP": "无需调整"}
VERDICT_CLS = {"ALLOW": "ok", "REQUIRE_APPROVAL": "warn", "DENY": "bad", "UNKNOWN": "info", "NOOP": "mute"}
COND_ZH = {"A": "数据够不够", "B": "降完还扛得住吗", "C": "服务现在健康吗", "D": "出问题能回滚吗"}
STATUS_ZH = {"PASS": "通过", "FAIL": "不通过", "UNKNOWN": "未知"}
STATUS_CLS = {"PASS": "ok", "FAIL": "bad", "UNKNOWN": "info"}
OUTCOME_ZH = {"verified": "已验证", "applied": "已应用，观察中", "rolled_back": "已回滚", "refused": "被拒绝", "failed": "失败"}
OUTCOME_CLS = {"verified": "ok", "applied": "info", "rolled_back": "bad", "refused": "warn", "failed": "bad"}
STATE_ZH = {"proposed": "已提案", "approved": "已批准", "applied": "已应用", "verified": "已核实", "rolled_back": "已回滚",
            "expired": "已过期", "halted": "已冻结"}
FLOW = ["proposed", "approved", "applied", "verified"]
REACHED = {"verified", "applied", "rolled_back", "failed"}   # the plan actually touched the cluster
SECTION_ORDER = ["现状", "证据", "做了哪些分析", "怎么做", "做完应该是什么样", "结论"]

CSS = BASE_CSS + """
.legend{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;padding:6px 16px 10px;margin:16px 0}
.legend h2{margin:8px 0;border:0;padding:0;font-size:16px}.legend dl{margin:0;display:grid;grid-template-columns:max-content 1fr;gap:4px 12px}
.legend dt{font-weight:600}.legend dd{margin:0}.legend p{margin:6px 0}
.halt{background:var(--bad);color:#fff;border-radius:8px;padding:12px 16px;margin:0 0 16px;font-weight:600}
.summary{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:16px 0;font-size:16px}
.summary b{font-size:20px}.summary .sub{display:block;margin-top:4px;font-size:13px;color:var(--muted)}
.v{font-size:12px;border-radius:4px;padding:1px 8px;font-weight:600;border:1px solid currentColor;white-space:nowrap}
.v-ok{color:var(--ok)}.v-warn{color:var(--warn)}.v-bad{color:var(--bad)}.v-info{color:var(--accent)}.v-mute{color:var(--muted)}
article.card h3{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:16px}
.part{border-left:3px solid var(--line);padding:2px 0 2px 12px;margin:12px 0}.part h4{margin:0 0 4px;font-size:14px;color:var(--accent)}
.part p{margin:4px 0}.part ul{margin:4px 0;padding-left:20px}.kv{display:grid;grid-template-columns:max-content 1fr;gap:2px 14px;margin:4px 0}
.kv dt{color:var(--muted)}.kv dd{margin:0}pre.cmd{white-space:pre-wrap;word-break:break-all;background:var(--bg);border:1px solid var(--line);
border-radius:6px;padding:8px;font-size:12px;margin:4px 0}.llm{border:1px dashed var(--line);border-radius:6px;padding:8px 10px;margin:8px 0;background:var(--bg)}
.money{font-size:15px;margin:4px 0 8px}.llm .tag{color:var(--accent)}.note{font-size:13px;color:var(--muted)}.good{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}.chips span{border:1px solid var(--line);border-radius:999px;padding:2px 12px;font-size:13px}
td.q code{display:block;max-width:420px}@media (max-width:600px){.legend dl,.kv{grid-template-columns:1fr}.legend dt{margin-top:6px}}
"""

# ------------------------------------------------------------------ small helpers
def _e(x) -> str:
    return escape(str(x))


def n(x) -> str:
    """Plain number, at most 4 significant digits, no scientific notation."""
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, str):
        return x
    s = f"{x:.4g}"
    if "e" in s:
        s = f"{x:.8f}".rstrip("0").rstrip(".")
    return s


def usd(x) -> str:
    return "—" if x is None else ("-" if x < 0 else "") + f"${abs(x):,.2f}"


def pct(x) -> str:
    return "—" if x is None else f"{x * 100:.3g}%"


def ratio_div(a, b):
    return None if a is None or not b else a / b


def badge(verdict) -> str:
    return f"<span class='v v-{VERDICT_CLS.get(verdict, 'mute')}'>{_e(verdict)} {_e(VERDICT_ZH.get(verdict, ''))}</span>"


def status_badge(status) -> str:
    return f"<span class='v v-{STATUS_CLS.get(status, 'mute')}'>{_e(STATUS_ZH.get(status, status))}</span>"


def wrap_table(head, rows, cls="") -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class=wrap><table class='{cls}'><tr>{th}</tr>{body}</table></div>"


def missing(what: str, file: str) -> str:
    return f"<p class=note>没有{_e(what)}（{_e(file)}），这一块不能渲染。</p>"


def _dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ input normalisation
def _dlist(inputs) -> list[dict]:
    return list((inputs.get("decisions") or {}).get("decisions") or [])


def _plans_by_decision(inputs) -> dict:
    p = inputs.get("plans")
    plans = p.get("plans", []) if isinstance(p, dict) else (p or [])
    return {x.get("decision_id"): x for x in plans if isinstance(x, dict)}


def ledger(inputs) -> tuple[list[dict], bool | None]:
    """-> (proposals, safe_halt) from runs/k8s_state.json as ledger.py writes it:
    {version, safe_halt, halt, last_run, proposals: [{plan_id, key, namespace, workload, resource, verdict, target, status, history[{status, ...}]}]}."""
    st = inputs.get("state")
    if not isinstance(st, dict):
        return [], None
    return list(st.get("proposals") or []), bool(st["safe_halt"]) if "safe_halt" in st else None


def ledger_state(entries, d) -> str | None:
    """Status of the newest proposal for this decision's workload/resource (a key can have older expired / rolled-back ones)."""
    mine = [e for e in entries if (e.get("namespace"), e.get("workload"), e.get("resource")) == (d.get("namespace"), d.get("workload"), d.get("resource"))]
    return mine[-1].get("status") if mine else None


def final_actions(actions) -> list[dict]:
    """Last record per plan_id, in first-seen order."""
    last = {}
    for a in actions or []:
        last[a.get("plan_id")] = a
    return list(last.values())


def quality(inputs) -> dict:
    """Agent-quality numbers shared by the monthly view and the tests. None = cannot be computed from the inputs."""
    decs = _dlist(inputs)
    verdicts = Counter(d.get("verdict") for d in decs)
    fin = final_actions(inputs.get("actions"))
    verify = inputs.get("verify") or []
    reached = [a for a in fin if a.get("outcome") in REACHED]
    bad_slo = {v.get("plan_id") for v in verify if v.get("slo_ok") is False}
    rolled = [a for a in reached if a["outcome"] == "rolled_back"]
    safe = [a for a in reached if a["outcome"] in ("verified", "applied") and a.get("plan_id") not in bad_slo]
    pred = sum(v.get("predicted_usd_month") or 0 for v in verify)
    real = sum(v.get("realized_usd_month") or 0 for v in verify)
    score = inputs.get("score")
    refused = None
    if isinstance(score, dict) and isinstance(score.get("n"), dict) and score.get("unknown_recall") is not None:
        tot = score["n"].get("unknown_expected") or 0     # eval/score_k8s.py: the exam's UNKNOWN items and the share the agent got
        refused = {"correct": round(score["unknown_recall"] * tot), "total": tot, "checked": True}
    elif decs:
        k = verdicts["UNKNOWN"]
        refused = {"correct": k, "total": k, "checked": False}
    return {
        "verdicts": verdicts, "n_decisions": len(decs),
        "unsafe_allow": score.get("unsafe_allow") if isinstance(score, dict) else None,
        "realization": real / pred if pred else None, "predicted": pred, "realized": real,
        "verified_net": sum(v.get("realized_usd_month") or 0 for v in verify if v.get("slo_ok")),
        "reached": len(reached), "rollback_rate": len(rolled) / len(reached) if reached else None,
        "safe_rate": len(safe) / len(reached) if reached else None,
        "gate_refused": sum(1 for a in inputs.get("actions") or [] if a.get("outcome") == "refused"),   # every attempt the gates stopped
        "refused": refused,
    }


# ------------------------------------------------------------------ shared page parts
def data_window(decs) -> str:
    wins = Counter(e.get("window") for d in decs for e in d.get("evidence", []) if e.get("window") not in (None, "instant"))
    return wins.most_common(1)[0][0] if wins else None


def halt_banner(inputs) -> str:
    _, halt = ledger(inputs)
    if not halt:
        return ""
    return ("<div class=halt role=alert>全局 safe_halt 已置位：有已应用的变更观察到 SLO 退化。"
            "在人工清除之前，只出报告，不出任何执行计划。</div>")


def header(view, inputs) -> str:
    doc = inputs.get("decisions") or {}
    decs = _dlist(inputs)
    win = data_window(decs)
    as_of = doc.get("as_of")
    t = _dt(as_of)
    days = {d.get("usage", {}).get("valid_days") for d in decs if d.get("usage", {}).get("valid_days") is not None}
    span = ""
    if win and t and win.endswith("d") and win[:-1].isdigit():
        span = f"{(t - timedelta(days=int(win[:-1]))).strftime('%Y-%m-%d')} 至 {t.strftime('%Y-%m-%d')}（近 {_e(win)}）"
    elif win:
        span = f"近 {_e(win)}"
    cluster = doc.get("cluster") or doc.get("world") or "未标注"
    return (f"<h1>{VIEW_TITLE[view]}</h1><p class=muted>{VIEW_QUESTION[view]}</p>"
            f"<p class=muted>截至 {_e(as_of or '未标注')} · 集群/世界：{_e(cluster)} · 数据窗口：{span or '未标注'}"
            + (f" · 各 workload 有效天数 {_e(min(days))}–{_e(max(days))}" if days else "") + "</p>")


LEGEND = """
<section class=legend><h2>怎么读这份报告</h2>
<p>每个 workload 的每种资源（CPU、内存）有一条判定。判定由固定规则给出，不是模型的意见。</p>
<dl>
<dt><span class='v v-ok'>ALLOW</span> 可以做</dt><dd>四个条件都通过，没有 HPA 联动。仍需要人创建 approval 文件才会执行。</dd>
<dt><span class='v v-warn'>REQUIRE_APPROVAL</span> 需要你拍板</dt><dd>能做，但有条件未知或有 HPA 联动，需要你判断。</dd>
<dt><span class='v v-bad'>DENY</span> 不能动</dt><dd>服务在退化、风险太高或没有回滚容量。报告会写明为什么。</dd>
<dt><span class='v v-info'>UNKNOWN</span> 数据不够</dt><dd>数据天数、覆盖率或 SLO 缺失。表里写明补什么数据就能判。</dd>
<dt><span class='v v-mute'>NOOP</span> 无需调整</dt><dd>推荐值不低于当前值，没有可降的空间。</dd>
</dl>
<p><b>四个条件。</b>A 数据够不够（有效天数、样本覆盖率）；B 降完还扛得住吗（余量、OOM、CPU throttling、集群 Pending）；
C 服务现在健康吗（SLO 燃烧率、错误预算、批任务 deadline）；D 出问题能回滚吗（有快照和回滚命令，回滚时集群有空闲容量）。
任何条件为未知都不算通过。</p>
<p><b>两种节省，别混。</b>「账面释放的预留」是 request 降低的那部分按牌价折算的钱，只说明预留变少了，账单不一定变。
「节点能缩才兑现的钱」是用装箱模拟算出节点数真的能少几台，再乘节点单价。节点数下不来时这个数是 0。
每条都是只应用这一条变更时的单独模拟，几条一起做的总和可能和逐条相加不同。</p>
</section>"""


def page(view, inputs, body) -> str:
    return (f"<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{VIEW_TITLE[view]}</title>"
            f"<style>{CSS}</style></head><body><main>{halt_banner(inputs)}{body}"
            f"<p class=note>由 finops_agent.k8s.report 生成。本页只读渲染，不执行任何命令。</p></main></body></html>")


# ------------------------------------------------------------------ daily
def summary(decs) -> str:
    allow = [d for d in decs if d.get("verdict") == "ALLOW"]
    real = sum(d["savings"]["realizable_usd_month"] for d in allow)
    rv = sum(d["savings"]["request_value_usd_month"] for d in allow)
    c = Counter(d.get("verdict") for d in decs)
    return (f"<div class=summary id=summary>可以做的 {len(allow)} 条合计，节点能缩才兑现："
            f"<b data-realizable='{real:.6f}'>{usd(real)}/月</b>"
            f"<span class=sub>另：账面释放的预留 <span data-request-value='{rv:.6f}'>{usd(rv)}/月</span>（口径不同，不等于账单减少，不要与上面相加）</span>"
            f"<span class=sub>待你批准 {c['REQUIRE_APPROVAL']} · 被拒 {c['DENY']} · 数据不足 {c['UNKNOWN']} · 无需调整 {c['NOOP']}</span></div>")


def compare(inputs) -> str:
    decs = {d["id"]: d for d in _dlist(inputs)}
    prev = {d["id"]: d for d in ((inputs.get("prev") or {}).get("decisions") or [])}
    entries, _ = ledger(inputs)
    h = ["<h2>和上次比</h2>"]
    if not prev and not entries:
        return "".join(h) + "<p class=note>没有上次的决策文件，也没有台账，无法比较。</p>"
    if prev:
        new = sorted(set(decs) - set(prev))
        gone = sorted(set(prev) - set(decs))
        chg = sorted(i for i in set(decs) & set(prev) if decs[i]["verdict"] != prev[i]["verdict"])
        same = len(set(decs) & set(prev)) - len(chg)
        li = []
        li.append(f"<li><b>新增</b> {len(new)}" + (f"：{_e('、'.join(new))}" if new else "") + "</li>")
        li.append(f"<li><b>不变</b> {same}</li>")
        li.append(f"<li><b>消失</b> {len(gone)}" + (f"：{_e('、'.join(gone))}" if gone else "") + "</li>")
        li.append("<li><b>判定变化</b> " + (str(len(chg)) if not chg else str(len(chg)) + "<ul>" + "".join(
            f"<li>{_e(i)}：{badge(prev[i]['verdict'])} → {badge(decs[i]['verdict'])}"
            f"<span class=note> {_e((decs[i].get('reasons') or [''])[0])}</span></li>" for i in chg) + "</ul>") + "</li>")
        h.append(f"<ul>{''.join(li)}</ul>")
    else:
        h.append("<p class=note>没有上次的决策文件，只能按台账看。</p>")
    exp = [e for e in entries if e.get("status") == "expired"]
    if entries:
        h.append(f"<p>台账里已过期的提案 {len(exp)}" + (
            "：" + _e("、".join(f"{e.get('namespace')}/{e.get('workload')}/{e.get('resource')}" for e in exp)) if exp else "") + "</p>")
    return "".join(h)


def _peak_hours(hours) -> str:
    if not hours:
        return "无"
    if len(hours) >= 24:
        return "全天持平，没有明显高峰"
    runs, s, p = [], hours[0], hours[0]
    for x in hours[1:] + [None]:
        if x is not None and x == p + 1:
            p = x
            continue
        runs.append(f"{s}" if s == p else f"{s}–{p}")
        s = p = x
    return "UTC " + "、".join(runs) + " 时"


def _kv(rows) -> str:
    return "<dl class=kv>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>"


def sec_status(d) -> str:
    c, u = d.get("current", {}), d.get("usage", {})
    unit = c.get("unit", "")
    rows = [("request / limit / 副本", f"{n(c.get('request'))} / {n(c.get('limit'))} {_e(unit)} × {n(c.get('replicas'))} 个副本"),
            ("使用量 p50 / p95 / p99 / max", f"{n(u.get('p50'))} / {n(u.get('p95'))} / {n(u.get('p99'))} / {n(u.get('max'))} {_e(unit)}"),
            ("高峰小时", _e(_peak_hours(u.get("peak_hours_utc") or []))),
            ("OOM 事件", f"{n(u.get('oom_events'))} 次")]
    if d.get("resource") == "cpu":
        rows.insert(3, ("throttle 峰值", pct(u.get("throttle_peak"))))
    return _kv(rows)


_QUANTILE = re.compile(r"quantile_over_time\(0\.(\d+)")
_SLO_CATALOG = {"deadline_seconds": "批任务 deadline（SLO 目录，秒）", "availability": "可用性目标（SLO 目录）",
                "p99_seconds": "p99 延迟目标（SLO 目录，秒）"}
_STATIC = [("kube_pod_container_resource_limits", "当前 limit"), ("kube_deployment_spec_replicas", "副本数"),
           ("cfs_throttled_periods_total", "高峰期 CPU throttle 比例（被限流的周期 / 总周期）"),
           ("last_terminated_reason", "OOMKilled 次数（窗口内）"),
           ("horizontalpodautoscaler_spec_target_metric", "HPA 目标指标"),
           ("horizontalpodautoscaler_status_current_replicas", "HPA 当前副本数"),
           ("horizontalpodautoscaler_spec_max_replicas", "HPA 最大副本数"),
           ("slo_burn_rate_1h", "SLO 近一小时燃烧率（1 表示刚好烧完预算的速度）"),
           ("slo_error_budget_remaining_ratio", "剩余错误预算比例"),
           ("job_last_duration_seconds", "批任务最长耗时（秒）")]
HOURLY_ORDER = ("有效天数（至少有一个小时有数据的天数）", "覆盖率（有数据的小时占窗口总小时的比例）", "高峰小时（UTC，使用量接近峰值的小时）")


def evidence_labels(evs) -> dict:
    """id -> human label, derived from the query text and window (the evidence entries themselves are part of the narration
    prompt, which cassettes are keyed on, so labels are not stored in them). The three entries that share one hourly query are
    told apart by their order, which is the order finops_agent.k8s.collect writes them: valid_days, coverage, peak_hours."""
    out, hourly = {}, {}
    for e in evs:
        q, w = str(e.get("query")), str(e.get("window"))
        label = None
        if q.startswith("slo_catalog:"):
            label = _SLO_CATALOG.get(q.rsplit(".", 1)[-1])
        elif "kube_pod_container_resource_requests" in q:
            cpu = 'resource="cpu"' in q
            label = "当前 request" if "workload=" in q else f"集群里设了{'CPU' if cpu else '内存'} request 的 pod 数"
        elif "kube_pod_status_phase" in q:
            label = "集群里 Pending 的 pod 数"
        elif "kube_node_status_allocatable" in q:
            label = ("节点数" if q.startswith("count(") else
                     "单节点可分配 CPU（core）" if 'resource="cpu"' in q else "单节点可分配内存（GiB）")
        elif w.endswith("@1h") and "cfs_" not in q:
            i = hourly.get((q, w), 0)
            hourly[(q, w)] = i + 1
            label = HOURLY_ORDER[i] if i < len(HOURLY_ORDER) else None
        elif _QUANTILE.search(q):
            label = "使用量 p" + _QUANTILE.search(q).group(1).ljust(2, "0")
        elif "max_over_time(container_" in q:
            label = "使用量 max（窗口内最大值）"
        else:
            label = next((t for k, t in _STATIC if k in q), None)
        out[e["id"]] = label
    return out


def _ev_value(e) -> str:
    v = e.get("value")
    if isinstance(v, list):
        return _e(_peak_hours(v))
    return n(v)


def sec_evidence(d) -> str:
    u, conds = d.get("usage", {}), d.get("conditions", {})
    used = {i for c in conds.values() for i in c.get("evidence", [])}
    ev = [e for e in d.get("evidence", []) if e["id"] in used] or d.get("evidence", [])
    labels = evidence_labels(d.get("evidence", []))
    shown = [e for e in ev if e.get("value") is not None]    # no value = this workload has no such thing (no HPA, not a batch job, ...)
    hidden = len(ev) - len(shown)
    comp = f"有效天数 {n(u.get('valid_days'))}，覆盖率 {n(u.get('coverage'))}"
    rows = [[_e(e["id"]), _e(labels.get(e["id"]) or "（未标注）"), f"<b>{_ev_value(e)}</b>", _e(e.get("window")),
             f"<code>{_e(e.get('query'))}</code>"] for e in shown]
    cond_a = conds.get("A", {})
    return (f"<p>数据完整度：{_e(cond_a.get('why', comp))}</p><details><summary>展开证据表（只列判定用到、且有值的）</summary>"
            + wrap_table(["证据", "含义", "值", "窗口", "查询"], rows)
            + (f"<p class=note>另有 {hidden} 条被判定引用的证据没有值（这个 workload 没有对应的东西，例如没有 HPA 或不是批任务），不列出。</p>" if hidden else "")
            + "</details>")


def sec_analysis(d) -> str:
    rows = []
    for k in "ABCD":
        c = (d.get("conditions") or {}).get(k)
        if not c:
            rows.append([k, _e(COND_ZH[k]), status_badge("UNKNOWN"), "没有这个条件的结果"])
            continue
        ev = "、".join(c.get("evidence", []))
        rows.append([f"<b>{k}</b>", _e(COND_ZH[k]), status_badge(c["status"]),
                     _e(c.get("why", "")) + (f"<span class=note>（证据 {_e(ev)}）</span>" if ev else "")])
    return wrap_table(["条件", "问的是", "状态", "为什么"], rows)


def sec_how(d, plan) -> str:
    rec, c = d.get("recommendation", {}), d.get("current", {})
    unit = c.get("unit", "")
    out = [f"<p>把 request 从 {n(c.get('request'))} 调到 <b>{n(rec.get('request'))}</b> {_e(unit)}（方法：{_e(rec.get('method'))}）。</p>"]
    if not plan:
        out.append("<p class=note>还没有执行计划（k8s_plans.json 里没有这条）。计划由流水线在 ALLOW 或 REQUIRE_APPROVAL 时生成；"
                   "全局 safe_halt 期间也不会生成。</p>")
        return "".join(out)
    stops = "；".join(f"{s.get('signal')} {s.get('op')} {n(s.get('value'))}" for s in plan.get("stop_conditions", [])) or "无"
    out.append(_kv([
        ("执行命令", f"<pre class=cmd>{_e(plan.get('apply_cmd'))}</pre>"),
        ("回滚命令", f"<pre class=cmd>{_e(plan.get('rollback_cmd'))}</pre>"),
        ("观察期", f"{n(plan.get('observe_seconds'))} 秒"),
        ("停止条件", f"{_e(stops)}（任一命中就自动回滚）"),
        ("canary 范围", _e((plan.get("canary") or {}).get("scope", "未标注"))),
        ("审批文件", f"<code>{_e(plan.get('approval_file'))}</code> <span class=note>由人创建，agent 不会创建；文件不存在就不执行</span>")]))
    return "".join(out)


def money_line(d) -> str:
    """One plain sentence: does money move if this is done, and why."""
    sv = d.get("savings", {})
    real, rv = sv.get("realizable_usd_month") or 0, sv.get("request_value_usd_month")
    lead = "这条要你拍板，批准并做完后：" if d.get("verdict") == "REQUIRE_APPROVAL" else ""
    if real > 0:
        body = (f"<b class=good>钱会动</b>：节点数从 {n(sv.get('nodes_before'))} 降到 {n(sv.get('nodes_after'))}，每月省 {usd(real)}。"
                "前提是这几台节点真的被缩掉；只改 request、节点没缩，账单不变。")
    else:
        body = (f"<b>钱不会动</b>：request 降了（账面少预留 {usd(rv)}/月），但节点数还是 {n(sv.get('nodes_before'))} 个，账单不变。"
                "这一条的意义是把预留还给调度器，不是省钱。")
    return f"<p class=money>{lead}{body}</p>"


def sec_after(d) -> str:
    rec, c, u, sv = d.get("recommendation", {}), d.get("current", {}), d.get("usage", {}), d.get("savings", {})
    new = rec.get("request")
    unit = c.get("unit", "")
    rows = []
    if new:
        if d.get("resource") == "cpu":
            rows.append(("p99 利用率", f"{pct(ratio_div(u.get('p99'), new))}（p99 {n(u.get('p99'))} / 新 request {n(new)} {_e(unit)}；现在是 "
                         f"{pct(ratio_div(u.get('p99'), c.get('request')))}）"))
            top = u.get("p99")
        else:
            rows.append(("p99 利用率", f"{pct(ratio_div(u.get('p99'), new))}（p99 {n(u.get('p99'))} / 新 request {n(new)} {_e(unit)}）"))
            rows.append(("峰值利用率", f"{pct(ratio_div(u.get('max'), new))}（峰值 {n(u.get('max'))} / 新 request {n(new)} {_e(unit)}）"))
            top = u.get("max")
        hd = None if not top else new / top - 1
        rows.append(("余量", f"{pct(hd)}（新 request 比{'p99' if d.get('resource') == 'cpu' else '峰值'}高出的部分）"))
    rows.append(("账面释放的预留", f"{usd(sv.get('request_value_usd_month'))}/月 <span class=note>（只说明预留变少，不等于账单减少）</span>"))
    nb, na = sv.get("nodes_before"), sv.get("nodes_after")
    real = sv.get("realizable_usd_month") or 0
    if real > 0:
        rows.append(("节点能缩才兑现的钱", f"<b class=good>{usd(real)}/月</b>，条件：节点数从 {n(nb)} 到 {n(na)}"))
    else:
        rows.append(("节点能缩才兑现的钱", f"<b>{usd(0)}/月</b>：降了预留但节点数下不来，所以账单不变（仍是 {n(nb)} 个节点）"))
    return money_line(d) + _kv(rows)


def sec_verdict(d) -> str:
    out = [f"<p>{badge(d.get('verdict'))} {_e('；'.join(d.get('reasons') or []))}</p>"]
    nar = d.get("narrative")
    if isinstance(nar, dict) and isinstance(nar.get("why"), dict):
        tag = ("由模型撰写、数字已核对（文中每个数字都能在证据里找到；判定本身由规则给出，不是模型定的）" if nar.get("source") == "llm"
               else "由模板生成，数字取自证据（没有调用模型）")
        why = nar["why"]
        parts = [f"<p><b>现状</b> {_e(why.get('status'))}</p>", f"<p><b>证据</b> {_e(why.get('evidence'))}</p>",
                 f"<p><b>结论</b> {_e(why.get('conclusion'))}</p>"]
        if nar.get("proposal"):
            parts.append(f"<p><b>提案</b> {_e(nar['proposal'])}</p>")
        if nar.get("would_change"):
            parts.append("<p><b>什么情况下判定会变</b></p><ul>" + "".join(f"<li>{_e(w)}</li>" for w in nar["would_change"]) + "</ul>")
        if nar.get("evidence"):
            parts.append(f"<p class=note>引用证据：{_e('、'.join(nar['evidence']))}</p>")
        out.append(f"<div class=llm><span class=tag>{tag}</span>{''.join(parts)}</div>")
    return "".join(out)


def card(d, plan, state) -> str:
    sv = d.get("savings", {})
    secs = [("现状", sec_status(d)), ("证据", sec_evidence(d)), ("做了哪些分析", sec_analysis(d)), ("怎么做", sec_how(d, plan)),
            ("做完应该是什么样", sec_after(d)), ("结论", sec_verdict(d))]
    assert [s for s, _ in secs] == SECTION_ORDER
    head = (f"<h3>{_e(d.get('namespace'))}/{_e(d.get('workload'))} · {'CPU' if d.get('resource') == 'cpu' else '内存'} {badge(d.get('verdict'))}"
            + (f" <span class='v v-mute'>台账：{_e(STATE_ZH.get(state, state))}</span>" if state else "")
            + f" <span class=muted style='font-weight:400'>{_e(d.get('team'))} · {_e(d['id'])}</span></h3>")
    return (f"<article class=card id='{_e(d['id'])}'>{head}"
            + "".join(f"<section class=part><h4>{t}</h4>{b}</section>" for t, b in secs) + "</article>")


def refused_table(decs) -> str:
    rows = []
    for d in decs:
        v = d.get("verdict")
        if v not in ("DENY", "UNKNOWN"):
            continue
        want = "FAIL" if v == "DENY" else "UNKNOWN"
        letters = [k for k in "ABCD" if (d.get("conditions", {}).get(k) or {}).get("status") == want]
        why = "<br>".join(f"<b>{k}</b> {_e(d['conditions'][k].get('why'))}" for k in letters) or _e("；".join(d.get("reasons") or []))
        need = list(d.get("unknown_needs") or [])
        if not need:
            need = [{"B": "先消除风险（OOM、throttling 或 Pending），下一轮自动重评",
                     "C": "等服务恢复健康，下一轮自动重评；期间冻结优化",
                     "D": "给回滚留出集群容量（放宽节点上限或先释放别处预留）"}.get(k, "条件恢复后下一轮自动重评") for k in letters] or ["无"]
        extra = [r for r in d.get("reasons", []) if "可考虑上调" in r or "仅供人工考虑上调" in r]
        rows.append([f"{_e(d.get('namespace'))}/{_e(d.get('workload'))}<br><span class=note>{badge(v)}</span>",
                     "CPU" if d.get("resource") == "cpu" else "内存", "、".join(letters) or "—", why,
                     "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in need + extra) + "</ul>"])
    if not rows:
        return "<p class=note>今天没有被拒或数据不足的条目。</p>"
    return wrap_table(["workload", "资源", "哪个条件", "为什么", "什么数据或动作能改变结论"], rows)


def noop_list(decs) -> str:
    rows = [[f"{_e(d.get('namespace'))}/{_e(d.get('workload'))}", "CPU" if d.get("resource") == "cpu" else "内存",
             n(d["current"].get("request")), n((d.get("recommendation") or {}).get("request")), _e(d.get("current", {}).get("unit"))]
            for d in decs if d.get("verdict") == "NOOP"]
    if not rows:
        return ""
    return ("<details><summary>无需调整（点开看列表）</summary>"
            + wrap_table(["workload", "资源", "当前 request", "推荐值", "单位"], rows) + "</details>")


def daily(inputs) -> str:
    decs = _dlist(inputs)
    if not inputs.get("decisions"):
        return page("daily", inputs, header("daily", inputs) + missing("决策文件", "runs/<id>/k8s_decisions.json"))
    plans, entries = _plans_by_decision(inputs), ledger(inputs)[0]
    act = sorted((d for d in decs if d.get("verdict") in ("ALLOW", "REQUIRE_APPROVAL")),
                 key=lambda d: (d["verdict"] != "ALLOW", -(d["savings"].get("realizable_usd_month") or 0), d["id"]))
    body = [header("daily", inputs), LEGEND, summary(decs), compare(inputs), "<h2>今天要你处理的</h2>"]
    body += [card(d, plans.get(d["id"]), ledger_state(entries, d)) for d in act] or ["<p class=note>今天没有可做或待批准的条目。</p>"]
    body += ["<h2>被拒与数据不足</h2><p class=note>这张表和省钱同样重要：它说明哪些优化被挡住了、凭什么、怎样才能放行。</p>",
             refused_table(decs), noop_list(decs)]
    return page("daily", inputs, "".join(body))


# ------------------------------------------------------------------ weekly
def _observed(a) -> str:
    parts = []
    o = a.get("observed")
    if isinstance(o, dict) and o:
        parts.append(", ".join(f"{k}={n(v)}" for k, v in o.items()))
    elif o:
        parts.append(json.dumps(o, ensure_ascii=False))
    sc = a.get("stop_condition")
    if isinstance(sc, dict):
        parts.append(f"触发 {sc.get('signal')} {sc.get('op')} {n(sc.get('value'))}（实测 {n(sc.get('observed'))}）")
    return _e("；".join(parts)) or "—"


def _ledger_cell(a) -> str:
    led = a.get("ledger")
    if not isinstance(led, dict):
        return "<span class=note>不变</span>" if a.get("outcome") == "refused" else "—"
    if led.get("status"):
        return _e(STATE_ZH.get(led["status"], led["status"]))
    return f"<span class=warn>没写成：{_e(led.get('error') or led.get('note') or '')}</span>"


def weekly_actions(inputs) -> str:
    acts = inputs.get("actions")
    if not acts:
        return "<h2>已执行的变更</h2>" + missing("执行记录", "runs/<id>/k8s_actions.jsonl")
    fin = final_actions([a for a in acts if a.get("outcome") != "refused"])   # last real outcome per plan; gate refusals are listed apart
    refused = [a for a in acts if a.get("outcome") == "refused"]
    c = Counter(a.get("outcome") for a in fin)
    chips = "".join(f"<span>{OUTCOME_ZH[k]} {c[k]}</span>" for k in OUTCOME_ZH if c[k])
    chips += f"<span>闸门拦下 {len(refused)} 次</span>" if refused else ""
    rows = [[f"<b>{_e(a.get('plan_id'))}</b>", f"<span class='v v-{OUTCOME_CLS.get(a.get('outcome'), 'mute')}'>{_e(OUTCOME_ZH.get(a.get('outcome'), a.get('outcome')))}</span>",
             _e(a.get("reason")), _observed(a), _ledger_cell(a), _e(a.get("started_at"))] for a in fin]
    out = ["<h2>已执行的变更</h2>", f"<div class=chips>{chips}</div>", wrap_table(["plan", "结果", "原因", "观察值", "台账", "开始时间"], rows),
           "<p class=note>每个 plan 只列最后一条记录。「已应用，观察中」说明观察期还没结束或进程中断。</p>"]
    if refused:
        out.append("<h3>闸门拦下的尝试</h3>" + wrap_table(["plan", "原因", "时间"], [[_e(a.get("plan_id")), _e(a.get("reason")), _e(a.get("started_at"))] for a in refused])
                   + "<p class=note>被拦下是闸门在起作用，不是故障；集群没有被动过，台账状态也不变。</p>")
    return "".join(out)


def weekly_verify(inputs) -> str:
    v = inputs.get("verify")
    if not v:
        return ("<h2>核实结果：预测 vs 实际</h2><p><b>本周没有已核实的变更。</b>"
                "<span class=note>核实记录来自 runs/&lt;id&gt;/k8s_verify.json；没有它，预测的节省不算数。</span></p>")
    rows, dev = [], []
    for x in v:
        r = x.get("ratio")
        cls = "good" if r is not None and r >= 0.8 else "warn"
        rows.append([f"<b>{_e(x.get('plan_id'))}</b>", usd(x.get("predicted_usd_month")), usd(x.get("realized_usd_month")),
                     f"<span class={cls}>{pct(r)}</span>", f"{n(x.get('nodes_before'))} → {n(x.get('nodes_after'))}",
                     "保持" if x.get("slo_ok") else "<span class=bad>退化</span>", _e(x.get("window"))])
        for d in x.get("deviations") or []:
            dev.append([_e(x.get("plan_id")), _e(d.get("cause")), usd(d.get("usd")), _e(d.get("explanation"))])
    out = ["<h2>核实结果：预测 vs 实际</h2>",
           wrap_table(["plan", "预测 /月（牌价估算）", "实际 /月（核实后）", "实现率", "节点数", "SLO", "观察窗口"], rows)]
    notes = sorted({x["note"] for x in v if x.get("note")})
    out += [f"<p class=note><b>注：</b>{_e(t)}</p>" for t in notes]
    out.append("<h3>偏差按原因拆开</h3>" + (wrap_table(["plan", "原因", "金额 /月", "解释"], dev) if dev else "<p class=note>没有记录偏差。</p>"))
    return "".join(out)


def weekly_rollbacks(inputs) -> str:
    fin = final_actions(inputs.get("actions"))
    rolled = [a for a in fin if a.get("outcome") == "rolled_back"]
    entries, halt = ledger(inputs)
    halted = [e for e in entries if e.get("status") == "halted"]
    out = ["<h2>回滚与 halt</h2>"]
    out.append("<p>safe_halt：" + ("<b class=bad>已置位</b>" if halt else "未置位" if halt is not None else "<span class=note>没有台账，未知</span>") + "</p>")
    if rolled:
        out.append(wrap_table(["plan", "触发的停止条件", "原因"], [[_e(a.get("plan_id")), _observed({"stop_condition": a.get("stop_condition")}), _e(a.get("reason"))] for a in rolled]))
    else:
        out.append("<p class=note>本周没有回滚。</p>")
    if halted:
        out.append("<p>台账里已冻结（halted）的提案：" + _e("、".join(f"{e.get('namespace')}/{e.get('workload')}/{e.get('resource')}" for e in halted)) + "</p>")
    return "".join(out)


def ledger_flow(entries) -> dict:
    reach = Counter()
    for e in entries:
        seen = {h.get("status") for h in e.get("history") or []} | {e.get("status")}
        top = max((FLOW.index(s) for s in seen if s in FLOW), default=-1)
        for s in FLOW[: top + 1]:
            reach[s] += 1
    cur = Counter(e.get("status") for e in entries)
    return {"reached": reach, "current": cur, "total": len(entries)}


def weekly_ledger(inputs) -> str:
    entries, _ = ledger(inputs)
    if not entries:
        return "<h2>台账流转</h2>" + missing("台账", "runs/k8s_state.json")
    f = ledger_flow(entries)
    flow = "".join(f"<span>{STATE_ZH[s]} {f['reached'][s]}</span>" for s in FLOW)
    side = "".join(f"<span>{STATE_ZH[s]} {f['current'][s]}</span>" for s in ("rolled_back", "expired", "halted"))
    moved = [e for e in entries if any(h.get("status") in ("approved", "applied", "verified", "rolled_back", "halted") for h in e.get("history") or [])]
    rows = [[f"<b>{_e(e.get('plan_id'))}</b>", " → ".join(_e(STATE_ZH.get(h.get("status"), h.get("status"))) for h in e.get("history") or []),
             _e(((e.get("history") or [{}])[-1]).get("note", ""))] for e in moved]
    return (f"<h2>台账流转</h2><p>共 {f['total']} 条提案。「到达」指走到过这一步（含已走过的）。</p>"
            f"<div class=chips>{flow}</div><div class=chips>{side}</div>"
            + ("<h3>已经动过手的提案：走过的路</h3>" + wrap_table(["plan", "状态流转", "最后一条备注"], rows) if rows else
               "<p class=note>还没有提案走到批准或执行。</p>"))


def weekly(inputs) -> str:
    body = [header("weekly", inputs), weekly_actions(inputs), weekly_verify(inputs), weekly_rollbacks(inputs), weekly_ledger(inputs)]
    return page("weekly", inputs, "".join(body))


# ------------------------------------------------------------------ monthly
def unit_cost(inputs) -> str:
    u = inputs.get("unit_cost")
    if not u or not u.get("requests_total"):
        return ("<p>每百万请求成本：<b>不可得</b>。<span class=note>需要 runs/&lt;id&gt;/k8s_unit_cost.json："
                "http_requests_total 在窗口内的增量，加上同一窗口的集群成本。缺其中任一个就不算，不拿别的数字替代。</span></p>")
    per = u["cluster_cost_usd"] / u["requests_total"] * 1e6
    return (f"<p>每百万请求成本：<b>{usd(per)}</b> <span class=note>= 集群成本 {usd(u['cluster_cost_usd'])} / "
            f"{n(u['requests_total'])} 次请求 × 1e6（请求量查询 <code>{_e(u.get('query'))}</code>，窗口 {n(u.get('window_days'))} 天）</span></p>")


def monthly(inputs) -> str:
    q = quality(inputs)
    verify = inputs.get("verify") or []
    body = [header("monthly", inputs), "<h2>已核实的净节省</h2>"]
    if verify:
        body.append(f"<div class=summary>已核实净节省（SLO 保持的变更）：<b>{usd(q['verified_net'])}/月</b>"
                    f"<span class=sub>预测 {usd(q['predicted'])}/月，实际 {usd(q['realized'])}/月，实现率 {pct(q['realization'])}</span></div>")
    else:
        body.append("<div class=summary>已核实净节省：<b>$0.00/月</b><span class=sub>没有核实记录（k8s_verify.json）。预测的节省不算节省。</span></div>")
    body.append(unit_cost(inputs))
    body.append("<h2>agent 自身质量</h2>")
    v = q["verdicts"]
    ua = q["unsafe_allow"]
    rf = q["refused"]
    rows = [
        ["判定分布", " · ".join(f"{k} {v[k]}" for k in VERDICT_ZH) + f"（共 {q['n_decisions']} 条）", "规则给出的判定，不是模型意见"],
        ["unsafe ALLOW", "未提供考卷分数" if ua is None else f"<b class={'good' if ua == 0 else 'bad'}>{n(ua)}</b>", "考卷里应该拒绝或数据不足却判了 ALLOW 的条数。硬闸为 0"],
        ["实现率", pct(q["realization"]) if q["realization"] is not None else "没有核实记录", "Σ实际 / Σ预测。低于 80% 说明预测系统性偏乐观"],
        ["回滚率", pct(q["rollback_rate"]) if q["rollback_rate"] is not None else "没有执行记录", f"回滚的 plan / 已触达集群的 plan（{q['reached']}）"],
        ["安全执行率", pct(q["safe_rate"]) if q["safe_rate"] is not None else "没有执行记录", "已触达集群的 plan 里，验证通过、没回滚、没失败、SLO 没退化的占比"],
        ["数据不够时说不知道", "—" if not rf else f"{rf['correct']} / {rf['total']}" + ("" if rf["checked"] else "（未对照考卷，仅为 UNKNOWN 条数）"),
         f"考卷里应判 UNKNOWN 的条目，agent 也判成 UNKNOWN 的数量（宁可说不知道，不能硬判）。另有执行闸门拒绝 {q['gate_refused']} 次"],
    ]
    body.append(wrap_table(["指标", "值", "含义"], rows))
    sc = inputs.get("score")
    body.append("<h2>考卷式评分表是什么</h2><p>评分表把世界文件里预先写好的标准答案（每个 workload、每种资源应该是什么 verdict、request 应落在什么区间）"
                "和 agent 的实际判定逐条对照，给出 verdict 准确率、unsafe ALLOW、UNKNOWN 召回、request 落区间比例。"
                "它回答的是「在已知答案的题上，这套规则做对了多少」，不等于线上效果，所以要和上面的核实记录一起看。"
                "预注册世界先冻结再只评一次，原始结果在 <code>eval/EXPERIMENTS.md</code>。</p>")
    if isinstance(sc, dict):
        nn = sc.get("n") if isinstance(sc.get("n"), dict) else {}
        meaning = {"verdict_accuracy": "判定和标准答案一致的比例", "unsafe_allow": "应拒绝或数据不足却判 ALLOW 的条数，硬闸为 0",
                   "unknown_recall": "应判 UNKNOWN 的条目里判对的比例", "request_in_range": "ALLOW/需批准的推荐值落在标准区间内的比例",
                   "realizable_accuracy": "节点能缩才兑现的金额落在标准区间内的比例"}
        rows = [["考卷题数", n(nn.get("expected")), "世界文件里写了标准答案的 (workload, 资源) 条数"]]
        rows += [[_e(k), n(sc[k]), meaning[k]] for k in meaning if sc.get(k) is not None]
        if isinstance(sc.get("narrative_ungrounded"), list):
            rows.append(["narrative_ungrounded", n(len(sc["narrative_ungrounded"])), "模型文字里出现、但在证据里找不到的数字条数，硬闸为 0"])
        body.append(wrap_table(["指标", "值", "含义"], rows))
    else:
        body.append("<p class=note>这次没有给 --score，所以不显示分数。</p>")
    return page("monthly", inputs, "".join(body))


# ------------------------------------------------------------------ entry points
def render(view: str, inputs: dict) -> str:
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}")
    return {"daily": daily, "weekly": weekly, "monthly": monthly}[view](inputs)


def _read_json(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.is_file() else None


def load_inputs(run, prev=None, state=None, score=None, actions=None, verify=None) -> dict:
    """runs/<id> files by their usual names; `actions` / `verify` / `score` / `state` paths override the defaults
    (k8s_actions.jsonl and k8s_verify.json in the run dir; k8s_eval.json, what eval/score_k8s.py writes next to the decisions)."""
    run = Path(run)
    ap = Path(actions) if actions else run / "k8s_actions.jsonl"
    acts = [json.loads(x) for x in ap.read_text().splitlines() if x.strip()] if ap.is_file() else None
    return {"decisions": _read_json(run / "k8s_decisions.json"),
            "prev": _read_json(Path(prev) / "k8s_decisions.json") if prev else None,
            "plans": _read_json(run / "k8s_plans.json"), "state": _read_json(state) if state else None,
            "actions": acts, "verify": _read_json(verify) if verify else _read_json(run / "k8s_verify.json"),
            "unit_cost": _read_json(run / "k8s_unit_cost.json"),
            "score": _read_json(score) if score else _read_json(run / "k8s_eval.json")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="K8s periodic report (daily / weekly / monthly)")
    ap.add_argument("--run", required=True, help="runs/<id> directory holding k8s_decisions.json etc.")
    ap.add_argument("--prev", help="previous runs/<id> directory (daily diff)")
    ap.add_argument("--state", help="runs/k8s_state.json (ledger)")
    ap.add_argument("--score", help="eval score JSON (monthly); default runs/<id>/k8s_eval.json if present")
    ap.add_argument("--actions", help="k8s_actions.jsonl (default: in the run directory)")
    ap.add_argument("--verify", help="k8s_verify.json (default: in the run directory)")
    ap.add_argument("--view", required=True, choices=VIEWS)
    ap.add_argument("--out", help="output HTML file (default: stdout)")
    a = ap.parse_args(argv)
    html = render(a.view, load_inputs(a.run, a.prev, a.state, a.score, a.actions, a.verify))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(html)
    else:
        sys.stdout.write(html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
