"""K8s mainline, end to end:

  SLO catalog (3rd input) + Prometheus  ->  collect  ->  decide (deterministic verdicts)
    ->  narrate (isolated LLM call per decision, record/replay, grounded)  ->  proposal ledger  ->  plans
    ->  runs/<id>/{k8s_decisions.json, k8s_plans.json, k8s_summary.json, k8s_trace.jsonl, k8s_{daily,weekly,monthly}.html}
    and runs/k8s_state.json

CLI (wired in finops_agent/__main__.py):
  python3 -m finops_agent k8s run --prom URL --slos testenv/k8s_slos.yaml --nodes-from testenv/k8s_world.yaml --run-id k8s-local [--prev-run runs/<id>] [--score k8s_eval.json]
  python3 -m finops_agent k8s mark --state runs/k8s_state.json --ref <plan_id|key> --status approved|applied|verified|rolled_back
  python3 -m finops_agent k8s ledger --state runs/k8s_state.json [--only-moved]
  python3 -m finops_agent k8s clear-halt --state runs/k8s_state.json
Read-only against the cluster and AWS; plans are JSON, nothing is executed.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from finops_agent.collect import Trace
from finops_agent.k8s import ledger, narrate, plans, report
from finops_agent.k8s.collect import Prom, collect_facts, load_slos
from finops_agent.k8s.policy import decide, load_policy, write_decisions
from finops_agent.llm import LLMClient

ROOT = Path(__file__).resolve().parents[2]


def parse_as_of(s) -> float | None:
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def nodes_bounds(args) -> tuple[int, int | None]:
    """min_nodes / max_nodes: the node autoscaler's configuration, not a metric. --nodes-from reads `nodes.min` / `nodes.max`."""
    lo, hi = 1, None
    if args.nodes_from:
        n = (yaml.safe_load(Path(args.nodes_from).read_text()) or {}).get("nodes") or {}
        lo, hi = n.get("min", lo), n.get("max")
    if args.min_nodes is not None:
        lo = args.min_nodes
    if args.max_nodes is not None:
        hi = args.max_nodes
    return lo, hi


def run(args) -> Path:
    policy = load_policy(args.policy)
    trace = Trace()
    prom = Prom(args.prom, trace)
    lo, hi = nodes_bounds(args)
    facts = collect_facts(prom, policy, parse_as_of(args.as_of), min_nodes=lo, max_nodes=hi, slos=load_slos(args.slos))
    decisions = decide(facts["facts"], facts["cluster"], policy)
    verdicts = {d["id"]: d["verdict"] for d in decisions}

    use_llm = args.narrate == "llm"
    client = LLMClient(model=args.model) if use_llm and args.model else (LLMClient() if use_llm else None)
    decisions = narrate.narrate_all(decisions, client, trace.calls, use_llm=use_llm)
    assert {d["id"]: d["verdict"] for d in decisions} == verdicts, "narration changed a verdict"   # LLM never sets the verdict

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) / run_id
    as_of = facts["as_of"]
    write_decisions(out / "k8s_decisions.json", as_of, args.world_label, decisions)

    state_path = Path(args.state)
    state, diff = ledger.update(ledger.load_state(state_path), decisions, run_id, as_of,
                                policy["same_target_tolerance"], approvals_dir=Path(args.out) / "approvals")
    targets = ledger.plan_targets(state)       # empty while safe_halt
    to_plan = []
    for d in decisions:
        p = targets.get(d["id"])
        if p is not None:   # the plan follows the proposal's target, so an approval file keeps matching its plan_id
            to_plan.append({**d, "recommendation": {**d["recommendation"], "request": p["target"]["request"]}})
    plan_list, skipped = plans.build_plans(to_plan, policy)
    plans_doc = {"run_id": run_id, "as_of": as_of, "safe_halt": state["safe_halt"], "plans": plan_list}
    out.mkdir(parents=True, exist_ok=True)
    (out / "k8s_plans.json").write_text(json.dumps(plans_doc, indent=2, ensure_ascii=False) + "\n")
    if not args.no_state:
        ledger.save_state(state_path, state)

    rendered = render_reports(out, state, args)

    llm = [c for c in trace.calls if c.get("source") == "llm"]
    nar = Counter(d["narrative"]["source"] for d in decisions)
    summary = {"run_id": run_id, "as_of": as_of, "decisions": len(decisions), "verdicts": dict(sorted(Counter(verdicts.values()).items())),
               "ledger": ledger.counts(diff), "ledger_keys": {k: v for k, v in diff.items() if isinstance(v, list) and v},
               "safe_halt": state["safe_halt"], "plans": len(plan_list), "plans_skipped": skipped, "reports": rendered,
               "narrative": dict(sorted(nar.items())),
               "llm": {"calls": len(llm), "mode": llm[0]["mode"] if llm else None, "tokens_in": sum(c["tokens_in"] for c in llm),
                       "tokens_out": sum(c["tokens_out"] for c in llm), "cost_usd": round(sum(c["cost_usd"] for c in llm), 6),
                       "latency_ms": round(sum(c["latency_ms"] for c in llm), 1), "redactions": sum(len(c.get("redactions", [])) for c in llm)}}
    (out / "k8s_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    (out / "k8s_trace.jsonl").write_text("".join(json.dumps(c, default=str, ensure_ascii=False) + "\n" for c in trace.calls))
    print(f"k8s run {run_id} (as_of {as_of}): {len(decisions)} decisions {summary['verdicts']}, plans {len(plan_list)}, "
          f"ledger {summary['ledger']}{', SAFE_HALT' if state['safe_halt'] else ''}, narrative {summary['narrative']}", file=sys.stderr)
    print(out / "k8s_decisions.json")
    return out


def render_reports(out: Path, state: dict, args) -> list[str]:
    """runs/<id>/k8s_{daily,weekly,monthly}.html. Inputs: this run's files, the ledger as just updated, the previous run
    (--prev-run), and whatever is already in the run dir (k8s_actions.jsonl, k8s_verify.json, k8s_eval.json); --score overrides the score."""
    inputs = report.load_inputs(out, prev=args.prev_run, state=None, score=args.score)
    inputs["state"] = state
    names = []
    for view in report.VIEWS:
        (out / f"k8s_{view}.html").write_text(report.render(view, inputs))
        names.append(f"k8s_{view}.html")
    return names


def format_ledger(state: dict, only_moved: bool = False) -> str:
    """Text dump of runs/k8s_state.json: status, plan id and the statuses each proposal went through."""
    lines = [f"ledger: {len(state['proposals'])} proposals, safe_halt={'ON' if state['safe_halt'] else 'off'}"]
    for p in state["proposals"]:
        path = [h["status"] for h in p["history"]]
        if only_moved and set(path) <= {"proposed", "expired"}:
            continue
        lines.append(f"  {p['status']:<11} {p['plan_id']:<38} {' -> '.join(path)}")
    return "\n".join(lines)


def add_parser(sub) -> None:
    k = sub.add_parser("k8s", help="K8s requests mainline: collect, decide, narrate, plans, ledger")
    ks = k.add_subparsers(dest="k8s_cmd", required=True)
    r = ks.add_parser("run", help="one run: writes runs/<id>/k8s_decisions.json, k8s_plans.json and updates the ledger")
    r.add_argument("--prom", default="http://127.0.0.1:9090")
    r.add_argument("--slos", required=True, help="SLO catalog (third input), e.g. testenv/k8s_slos.yaml")
    r.add_argument("--as-of", default=None, help="ISO time or epoch seconds; default the metrics source's latest point")
    r.add_argument("--nodes-from", default=None, help="yaml with nodes.min / nodes.max (node autoscaler bounds), e.g. testenv/k8s_world.yaml")
    r.add_argument("--min-nodes", type=int, default=None)
    r.add_argument("--max-nodes", type=int, default=None)
    r.add_argument("--policy", default=None)
    r.add_argument("--out", default=str(ROOT / "runs"))
    r.add_argument("--run-id", default=None)
    r.add_argument("--state", default=str(ROOT / "runs" / "k8s_state.json"), help="proposal ledger")
    r.add_argument("--no-state", action="store_true")
    r.add_argument("--prev-run", default=None, help="previous run directory (runs/<id>), for the daily report's comparison")
    r.add_argument("--score", default=None, help="eval score JSON for the monthly report (default: <run dir>/k8s_eval.json if present)")
    r.add_argument("--world-label", default="k8s", help="written to decisions.world")
    r.add_argument("--narrate", choices=("llm", "template"), default="llm", help="llm: isolated call per decision (FINOPS_LLM_MODE=replay|record|live); template: deterministic")
    r.add_argument("--model", default=None)
    m = ks.add_parser("mark", help="move a proposal's status (executor or human)")
    m.add_argument("--state", default=str(ROOT / "runs" / "k8s_state.json"))
    m.add_argument("--ref", required=True, help="plan_id, <ns>/<workload>/<resource> or decision id")
    m.add_argument("--status", required=True, choices=("approved", "applied", "verified", "rolled_back"))
    m.add_argument("--note", default="")
    lg = ks.add_parser("ledger", help="print the proposal ledger: one line per proposal, with the path it has taken")
    lg.add_argument("--state", default=str(ROOT / "runs" / "k8s_state.json"))
    lg.add_argument("--only-moved", action="store_true", help="only proposals past `proposed` (approved / applied / verified / rolled_back / halted)")
    c = ks.add_parser("clear-halt", help="human clears safe_halt")
    c.add_argument("--state", default=str(ROOT / "runs" / "k8s_state.json"))


def dispatch(args) -> None:
    if args.k8s_cmd == "run":
        run(args)
    elif args.k8s_cmd == "mark":
        st = ledger.mark(ledger.load_state(args.state), args.ref, args.status, note=args.note)
        ledger.save_state(args.state, st)
        print(f"{args.ref} -> {args.status}")
    elif args.k8s_cmd == "ledger":
        print(format_ledger(ledger.load_state(args.state), only_moved=args.only_moved))
    elif args.k8s_cmd == "clear-halt":
        ledger.save_state(args.state, ledger.clear_halt(ledger.load_state(args.state)))
        print("safe_halt cleared")
