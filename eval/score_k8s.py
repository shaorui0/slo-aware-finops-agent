#!/usr/bin/env python3
"""Score a K8s mainline run against the world's `expect` (the answer key).

  python3 -m eval.score_k8s runs/<id>/k8s_decisions.json [--world testenv/k8s_world.yaml] [--baseline eval/k8s_baseline.json] [--write-baseline]

Metrics (contract: k8s-mainline spec, section Evaluation)
  verdict_accuracy     decisions whose verdict equals expect, per (workload, resource)
  unsafe_allow         expect is DENY or UNKNOWN but the verdict is ALLOW (hard gate: must be 0)
  unknown_recall       share of expect-UNKNOWN entries judged UNKNOWN
  request_in_range     ALLOW / REQUIRE_APPROVAL recommendations inside expect.request_range
  realizable_accuracy  only where expect has realizable_usd_month [lo, hi]; None when no entry has it
Narrative check (hard gate, K4): every number in every narrative must be grounded in its own decision (judge/validate.py rules),
so `narrative_ungrounded` must be 0. Informational: narrative sources (how many decisions carry an LLM narrative).

Exit 1 when a gate fails: unsafe_allow > 0, verdict_accuracy < 0.9, unknown_recall < 1.0, request_in_range < 0.8,
any ungrounded number in a narrative, or any metric is worse than eval/k8s_baseline.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

from finops_agent.judge import validate
from finops_agent.k8s import narrate

ROOT = Path(__file__).resolve().parent.parent
MIN_VERDICT_ACCURACY, MIN_UNKNOWN_RECALL, MIN_IN_RANGE = 0.9, 1.0, 0.8
METRICS = ("verdict_accuracy", "unsafe_allow", "unknown_recall", "request_in_range", "realizable_accuracy")
EPS = 0.01


def _rate(n, d):
    return round(n / d, 4) if d else None


def ungrounded_in_narratives(decisions: list[dict]) -> list[dict]:
    out = []
    for d in decisions:
        n = d.get("narrative")
        if not n or "why" not in n:
            continue
        text = " ".join(n["why"].values()) + " " + " ".join(n["would_change"]) + " " + (n.get("proposal") or "")
        bad = validate.ungrounded_numbers(text, narrate.grounding_brief(d))
        if bad:
            out.append({"id": d["id"], "numbers": bad})
    return out


def score(doc: dict, world: dict) -> dict:
    expect = {(w["namespace"], w["name"], r): e for w in world["workloads"] for r, e in w["expect"].items()}
    got = {(d["namespace"], d["workload"], d["resource"]): d for d in doc["decisions"]}
    wrong, unsafe, unknown_total, unknown_hit, rng_total, rng_hit, rz_total, rz_hit, rz_bad, rng_bad = [], [], 0, 0, 0, 0, 0, 0, [], []
    for key, e in sorted(expect.items()):
        d = got.get(key)
        name = f"{key[1]}/{key[2]}"
        v = d["verdict"] if d else None
        if v != e["verdict"]:
            wrong.append({"id": name, "got": v, "expect": e["verdict"]})
        if v == "ALLOW" and e["verdict"] in ("DENY", "UNKNOWN"):
            unsafe.append(name)
        if e["verdict"] == "UNKNOWN":
            unknown_total += 1
            unknown_hit += v == "UNKNOWN"
        if d and v in ("ALLOW", "REQUIRE_APPROVAL") and e.get("request_range"):
            lo, hi = e["request_range"]
            rng_total += 1
            r = d["recommendation"]["request"]
            if r is not None and lo - 1e-9 <= r <= hi + 1e-9:
                rng_hit += 1
            else:
                rng_bad.append({"id": name, "request": r, "range": [lo, hi]})
        if d and e.get("realizable_usd_month"):
            lo, hi = e["realizable_usd_month"]
            rz_total += 1
            x = d["savings"]["realizable_usd_month"]
            if lo - EPS <= x <= hi + EPS:
                rz_hit += 1
            else:
                rz_bad.append({"id": name, "realizable_usd_month": x, "range": [lo, hi]})
    extra = sorted(set(got) - set(expect))
    return {
        "verdict_accuracy": _rate(len(expect) - len(wrong), len(expect)),
        "unsafe_allow": len(unsafe),
        "unknown_recall": _rate(unknown_hit, unknown_total),
        "request_in_range": _rate(rng_hit, rng_total),
        "realizable_accuracy": _rate(rz_hit, rz_total),
        "n": {"expected": len(expect), "decisions": len(got), "unknown_expected": unknown_total, "ranged": rng_total, "realizable": rz_total},
        "wrong_verdicts": wrong, "unsafe_allow_ids": unsafe, "out_of_range": rng_bad, "realizable_misses": rz_bad,
        "not_in_world": [f"{k[1]}/{k[2]}" for k in extra],
        "narrative_ungrounded": ungrounded_in_narratives(doc["decisions"]),
        "narrative_sources": dict(sorted(Counter((d.get("narrative") or {}).get("source", "none") for d in doc["decisions"]).items())),
    }


def gates(s: dict, baseline: dict | None) -> list[str]:
    fails = []
    if s["unsafe_allow"]:
        fails.append(f"unsafe_allow {s['unsafe_allow']} != 0: {s['unsafe_allow_ids']}")
    if s["verdict_accuracy"] is None or s["verdict_accuracy"] < MIN_VERDICT_ACCURACY:
        fails.append(f"verdict_accuracy {s['verdict_accuracy']} < {MIN_VERDICT_ACCURACY}: {s['wrong_verdicts']}")
    if s["unknown_recall"] is not None and s["unknown_recall"] < MIN_UNKNOWN_RECALL:
        fails.append(f"unknown_recall {s['unknown_recall']} < {MIN_UNKNOWN_RECALL}")
    if s["request_in_range"] is not None and s["request_in_range"] < MIN_IN_RANGE:
        fails.append(f"request_in_range {s['request_in_range']} < {MIN_IN_RANGE}: {s['out_of_range']}")
    if s["narrative_ungrounded"]:
        fails.append(f"ungrounded numbers in narratives: {s['narrative_ungrounded']}")
    if baseline:
        for k in METRICS:
            b, v = baseline.get(k), s[k]
            if b is None:
                continue
            worse = (v is None or v > b + 1e-9) if k == "unsafe_allow" else (v is None or v + 1e-9 < b)
            if worse:
                fails.append(f"{k} {v} is worse than baseline {b}")
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("decisions")
    ap.add_argument("--world", default=str(ROOT / "testenv" / "k8s_world.yaml"))
    ap.add_argument("--baseline", default=str(ROOT / "eval" / "k8s_baseline.json"))
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.decisions).read_text())
    s = score(doc, yaml.safe_load(Path(a.world).read_text()))
    Path(a.decisions).with_name("k8s_eval.json").write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n")
    bp = Path(a.baseline)
    baseline = json.loads(bp.read_text()) if bp.exists() else None
    if a.write_baseline:
        bp.write_text(json.dumps({k: s[k] for k in METRICS}, indent=2) + "\n")
    fails = gates(s, None if a.write_baseline else baseline)
    print(json.dumps({k: s[k] for k in METRICS}))
    print(f"n={s['n']} narrative={s['narrative_sources']}")
    if s["wrong_verdicts"]:
        print("wrong: " + ", ".join(f"{w['id']} got {w['got']} expect {w['expect']}" for w in s["wrong_verdicts"]))
    if fails:
        print("GATE FAILED: " + "; ".join(fails), file=sys.stderr)
        return 1
    print("gates: pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
