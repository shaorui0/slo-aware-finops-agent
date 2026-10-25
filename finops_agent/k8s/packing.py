"""Bin-packing simulation: how many nodes do the requests need, and what does one change save?

First-fit decreasing over all pods' requests (cpu and memory must both fit) against a homogeneous node
allocatable. Deterministic: ties are broken by (namespace, pod).
"""
from __future__ import annotations

EPS = 1e-9


def pack(pods: list[dict], alloc_cpu: float, alloc_mem_gib: float, min_nodes: int = 1) -> int:
    """pods: [{namespace, pod, cpu, mem}] (cores, GiB). Returns the simulated node count (>= min_nodes)."""
    order = sorted(pods, key=lambda p: (-max(p["cpu"] / alloc_cpu, p["mem"] / alloc_mem_gib), p.get("namespace", ""), p["pod"]))
    nodes: list[list[float]] = []   # remaining [cpu, mem] per node
    for p in order:
        for n in nodes:
            if n[0] + EPS >= p["cpu"] and n[1] + EPS >= p["mem"]:
                n[0] -= p["cpu"]
                n[1] -= p["mem"]
                break
        else:   # also the oversize case: a pod bigger than a node gets a node of its own
            nodes.append([alloc_cpu - p["cpu"], alloc_mem_gib - p["mem"]])
    return max(len(nodes), min_nodes)


def apply_change(pods: list[dict], namespace: str, workload: str, resource: str, request: float) -> list[dict]:
    key = "cpu" if resource == "cpu" else "mem"
    out = []
    for p in pods:
        if p.get("namespace") == namespace and p.get("workload") == workload:
            p = {**p, key: request}
        out.append(p)
    return out


def savings(pods: list[dict], cluster: dict, namespace: str, workload: str, resource: str,
            current: dict, new_request: float, pricing: dict, hours_per_month: float = 730) -> dict:
    """Both savings definitions for applying this one change alone."""
    a_cpu, a_mem = cluster["allocatable"]["cpu"], cluster["allocatable"]["memory_gib"]
    min_nodes = cluster.get("min_nodes", 1)
    before = pack(pods, a_cpu, a_mem, min_nodes)
    after_pods = apply_change(pods, namespace, workload, resource, new_request)
    after = pack(after_pods, a_cpu, a_mem, min_nodes)
    delta = max(0.0, current["request"] - new_request)
    unit_price = pricing["cpu_core_hour_usd"] if resource == "cpu" else pricing["mem_gib_hour_usd"]
    return {
        "request_value_usd_month": round(delta * unit_price * hours_per_month * current["replicas"], 6),
        "realizable_usd_month": round(max(0, before - after) * pricing["node_hour_usd"] * hours_per_month, 6),
        "nodes_before": before,
        "nodes_after": after,
    }


def free_after(pods: list[dict], cluster: dict, namespace: str, workload: str, resource: str,
               new_request: float, nodes_after: int) -> float:
    """Idle allocatable (of the changed resource) across nodes_after nodes once the change is applied."""
    key = "cpu" if resource == "cpu" else "mem"
    total = nodes_after * (cluster["allocatable"]["cpu"] if resource == "cpu" else cluster["allocatable"]["memory_gib"])
    used = sum(p[key] for p in apply_change(pods, namespace, workload, resource, new_request))
    return total - used
