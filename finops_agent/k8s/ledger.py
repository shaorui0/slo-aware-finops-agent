"""Proposal ledger: runs/k8s_state.json, the cross-run memory of the K8s mainline (k8s-mainline spec, c0031 spec k8s-pipeline).

One proposal per (namespace, workload, resource) is open at a time. Per run:
  same verdict and target within same_target_tolerance (5%)  -> unchanged (only last_seen moves)
  verdict or target changed, or no longer actionable         -> the old proposal is expired, a new one is created if still actionable
  an applied proposal whose workload now fails condition C   -> halted, and global safe_halt (no plans, no new proposals) until cleared by a human
Timestamps are the run's as_of, never the wall clock, so a multi-day simulation replays.
approved / applied / verified / rolled_back are written by `mark` (the executor calls it); a run never infers them,
except proposed -> approved when the approval file exists.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ACTIONABLE = ("ALLOW", "REQUIRE_APPROVAL")
OPEN = ("proposed", "approved")                 # subject to dedupe and expiry
LIVE = ("proposed", "approved", "applied", "halted")
TRANSITIONS = {
    "proposed": ("approved",),
    "approved": ("applied",),
    "applied": ("verified", "rolled_back"),
    "halted": ("applied", "verified", "rolled_back"),
}


def empty_state() -> dict:
    return {"version": 1, "safe_halt": False, "halt": None, "last_run": None, "proposals": []}


def load_state(path) -> dict:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else empty_state()


def save_state(path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def key_of(d: dict) -> str:
    return f"{d['namespace']}/{d['workload']}/{d['resource']}"


def _target(d: dict) -> dict:
    return {"request": d["recommendation"]["request"], "unit": d["current"].get("unit")}


def _same_target(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * abs(a) + 1e-12


def _log(p: dict, run_id, as_of, status, note=""):
    p["status"] = status
    p["history"].append({"run_id": run_id, "as_of": as_of, "status": status, "note": note})


def _new(d: dict, run_id, as_of) -> dict:
    t = _target(d)
    p = {"plan_id": f"P-{d['id']}-{t['request']:g}", "key": key_of(d), "decision_id": d["id"],
         "namespace": d["namespace"], "workload": d["workload"], "resource": d["resource"], "verdict": d["verdict"],
         "target": t, "current_request": d["current"].get("request"), "status": "proposed",
         "created_run": run_id, "first_seen": as_of, "last_seen": as_of, "history": []}
    _log(p, run_id, as_of, "proposed", f"{d['verdict']}，目标 {t['request']:g} {t['unit']}")
    return p


def update(state: dict, decisions: list[dict], run_id: str, as_of: str, tol: float, approvals_dir=None) -> tuple[dict, dict]:
    """-> (new state, diff). diff lists proposal keys under new / unchanged / expired / halted / approved / suppressed / held."""
    st = copy.deepcopy(state)
    diff = {"new": [], "unchanged": [], "expired": [], "halted": [], "approved": [], "suppressed": [], "held": [], "frozen": False}
    props = st["proposals"]
    by_wl = {}
    for d in decisions:
        by_wl.setdefault((d["namespace"], d["workload"]), []).append(d)

    # 1. an applied proposal on a workload whose SLO is now failing: halt
    for p in props:
        if p["status"] != "applied":
            continue
        bad = [d for d in by_wl.get((p["namespace"], p["workload"]), []) if d["conditions"]["C"]["status"] == "FAIL"]
        if bad:
            why = bad[0]["conditions"]["C"]["why"]
            _log(p, run_id, as_of, "halted", f"已 applied 后 SLO 退化：{why}")
            st["safe_halt"], st["halt"] = True, {"since": as_of, "run_id": run_id, "key": p["key"], "reason": why}
            diff["halted"].append(p["key"])
    st["last_run"] = {"run_id": run_id, "as_of": as_of}
    if st["safe_halt"]:   # report only: nothing new, nothing expired, no plans (the pipeline reads safe_halt)
        diff["frozen"] = True
        return st, diff

    # 2. dedupe / expire / create
    actionable = {key_of(d): d for d in decisions if d["verdict"] in ACTIONABLE and d["recommendation"].get("request") is not None}
    verdict_of = {key_of(d): d["verdict"] for d in decisions}
    for key, d in sorted(actionable.items()):
        mine = [p for p in props if p["key"] == key]
        if any(p["status"] in ("applied", "halted") for p in mine):
            diff["held"].append(key)
            continue
        tgt = d["recommendation"]["request"]
        open_ = next((p for p in mine if p["status"] in OPEN), None)
        if open_ is not None:
            if open_["verdict"] == d["verdict"] and _same_target(open_["target"]["request"], tgt, tol):
                open_["last_seen"] = as_of
                open_["latest_recommendation"] = tgt
                diff["unchanged"].append(key)
                continue
            why = "verdict 变了" if open_["verdict"] != d["verdict"] else "目标值变化超过容差"
            _log(open_, run_id, as_of, "expired", f"{why}：{open_['verdict']} {open_['target']['request']:g} -> {d['verdict']} {tgt:g}")
            diff["expired"].append(key)
        else:
            rb = [p for p in mine if p["status"] == "rolled_back"]
            if rb and rb[-1]["verdict"] == d["verdict"] and _same_target(rb[-1]["target"]["request"], tgt, tol):
                diff["suppressed"].append(key)    # rolled back before: not re-proposed until the target moves
                continue
        props.append(_new(d, run_id, as_of))
        diff["new"].append(key)
    for p in props:
        if p["status"] in OPEN and p["key"] not in actionable:
            now = verdict_of.get(p["key"])
            _log(p, run_id, as_of, "expired", f"本轮 verdict 为 {now}，不再是可提案" if now else "workload 已不在指标里")
            diff["expired"].append(p["key"])

    # 3. approval files (existence only: the agent never approves anything itself)
    if approvals_dir is not None:
        for p in props:
            if p["status"] == "proposed" and (Path(approvals_dir) / f"{p['plan_id']}.approved").exists():
                _log(p, run_id, as_of, "approved", "approval 文件存在")
                diff["approved"].append(p["key"])
    return st, diff


def plan_targets(state: dict) -> dict:
    """decision id -> proposal, for proposals that may get a plan now (proposed / approved, and not safe_halt)."""
    if state.get("safe_halt"):
        return {}
    return {p["decision_id"]: p for p in state["proposals"] if p["status"] in OPEN}


def mark(state: dict, ref: str, status: str, run_id: str = "manual", as_of: str = "", note: str = "") -> dict:
    """Move the live proposal named by plan_id or key to `status` (executor / human). Illegal transitions raise ValueError."""
    st = copy.deepcopy(state)
    p = next((p for p in reversed(st["proposals"]) if ref in (p["plan_id"], p["key"], p["decision_id"]) and p["status"] in LIVE), None)
    if p is None:
        raise ValueError(f"no live proposal for {ref!r}")
    if status not in TRANSITIONS.get(p["status"], ()):
        raise ValueError(f"{p['status']} -> {status} is not allowed (allowed: {TRANSITIONS.get(p['status'], ())})")
    _log(p, run_id, as_of or (st["last_run"] or {}).get("as_of", ""), status, note)
    return st


def clear_halt(state: dict, note: str = "人工清除") -> dict:
    st = copy.deepcopy(state)
    st["safe_halt"], st["halt"] = False, None
    return st


def counts(diff: dict) -> dict:
    return {k: len(v) for k, v in diff.items() if isinstance(v, list)}
