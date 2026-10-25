"""Recommender: deterministic request suggestions from facts (contract formulas, no judgement)."""
from __future__ import annotations

import math

GIB = 1024 ** 3
UNITS = {"Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3}


def parse_bytes(s) -> float:
    """'64Mi' -> bytes. A bare number is bytes."""
    s = str(s).strip()
    for suffix, mult in UNITS.items():
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * mult
    return float(s)


def ceil_to(x: float, step: float) -> float:
    """Round up to a multiple of step; the round() absorbs float noise like 0.65000000001."""
    return math.ceil(round(x / step, 9)) * step


def recommend(facts: dict, policy: dict) -> dict | None:
    """Return {request, method, headroom} in the facts' unit (core | GiB), or None when usage is missing."""
    u = facts["usage"]
    if facts["resource"] == "cpu":
        if u.get("p99") is None:
            return None
        h, step = policy["cpu_headroom"], policy["cpu_step"]
        return {"request": round(ceil_to(u["p99"] * (1 + h), step), 6),
                "method": f"p99*(1+{h:g}) ceil {step:g}", "headroom": h}
    if u.get("max") is None:
        return None
    h = policy["mem_headroom"]
    step_gib = parse_bytes(policy["mem_step"]) / GIB
    return {"request": round(ceil_to(u["max"] * (1 + h), step_gib), 6),
            "method": f"max_working_set*(1+{h:g}) ceil {policy['mem_step']}", "headroom": h}


def is_noop(rec: dict | None, current_request) -> bool:
    return rec is not None and current_request is not None and rec["request"] >= current_request - 1e-9
