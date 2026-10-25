"""Controlled execution of one approved K8s resource plan on the local k3d test cluster.

Gates, in order (any failure stops, and is recorded as `refused`):
  1. plan.context == k3d-finops-test
  2. the approval file exists (a human creates it; this module never does)
  3. live deployment resources == plan.before (otherwise: state drift)
Then: INTENT-prefixed patch -> rollout status -> observe -> stop condition ->
rollback_cmd -> rollout status -> verify resources == before.

Every kubectl call goes through `kubectl()`, which ALWAYS passes `--context`
taken from the plan. Write commands are executed as a string whose first line
is a `# INTENT:` comment (what the project hook also requires).

Usage:
  python3 -m finops_agent.k8s.act apply  --plans runs/<id>/k8s_plans.json --plan-id P-...
  python3 -m finops_agent.k8s.act status --plans runs/<id>/k8s_plans.json
"""
from __future__ import annotations

import argparse
import json
import operator
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TEST_CONTEXT = "k3d-finops-test"
ALLOWED_WRITE_VERBS = {"patch", "scale", "set", "rollout"}
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}
METRIC_NAMES = {"throttle_ratio": "cpu_throttle_ratio", "p99_seconds": "http_request_duration_p99_seconds"}
KUBE_SIGNALS = {"restarts", "oom_killed"}
EXIT = {"verified": 0, "refused": 3, "rolled_back": 4, "failed": 5}
MAX_METRIC_MISSES = 3


class Refused(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg):
    print(f"[act {datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- kubectl

def kubectl(plan, args, intent=None, timeout=120):
    """The single entry point for kubectl. Always passes --context from the plan.

    `intent` set  -> the command is a mutation; it runs as "# INTENT: ...\\nkubectl ..." .
    Returns CompletedProcess (stdout/stderr text, never raises on non-zero exit).
    """
    ctx = plan.get("context")
    if ctx != TEST_CONTEXT:
        raise Refused(f"context {ctx!r} is not {TEST_CONTEXT}")
    argv = ["kubectl", "--context", ctx, *args]
    cmd = shlex.join(argv)
    if intent:
        cmd = f"# INTENT: {' '.join(intent.split())}\n{cmd}"
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)


def sanitize_cmd(plan, cmd_text, allowed_verbs=ALLOWED_WRITE_VERBS):
    """Split a plan command into kubectl args (context removed, it is re-added by kubectl())."""
    tokens = shlex.split(cmd_text)
    if not tokens or tokens[0].rsplit("/", 1)[-1] != "kubectl":
        raise Refused(f"plan command does not start with kubectl: {cmd_text[:60]!r}")
    out, i, verb = [], 1, None
    while i < len(tokens):
        t = tokens[i]
        if t == "--context":
            if tokens[i + 1:i + 2] != [plan["context"]]:
                raise Refused("plan command carries a different --context")
            i += 2
            continue
        if t.startswith("--context="):
            if t.split("=", 1)[1] != plan["context"]:
                raise Refused("plan command carries a different --context")
            i += 1
            continue
        if verb is None and not t.startswith("-") and (not out or out[-1] not in ("-n", "--namespace")):
            verb = t
        out.append(t)
        i += 1
    if verb not in allowed_verbs:
        raise Refused(f"plan command verb {verb!r} is not one of {sorted(allowed_verbs)}")
    return out


# ---------------------------------------------------------------- resources

_UNITS = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "P": 10**15,
          "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50}


def norm_quantity(v):
    """'500m' == '0.5' == 0.5 (cpu); '1Gi' == '1024Mi' (memory). Returns an exact number."""
    s = str(v).strip()
    if s.endswith("m"):
        return round(float(s[:-1]) / 1000, 6)
    m = re.fullmatch(r"([0-9.]+)([A-Za-z]*)", s)
    if not m or m.group(2) not in _UNITS:
        return s
    return round(float(m.group(1)) * _UNITS[m.group(2)], 6)


def norm_res(d):
    return {k: norm_quantity(v) for k, v in (d or {}).items()}


def target_of(plan):
    """(namespace, workload) from the plan: explicit keys if present, else parsed from apply_cmd."""
    ns, w = plan.get("namespace"), plan.get("workload")
    if ns and w:
        return ns, w
    m = re.search(r"-n\s+(\S+)\s+patch\s+deploy(?:ment)?/?\s*(\S+)", plan.get("apply_cmd", "")) \
        or re.search(r"patch\s+deploy(?:ment)?/?\s*(\S+).*?-n\s+(\S+)", plan.get("apply_cmd", ""))
    if not m:
        raise Refused("cannot determine namespace/workload from plan")
    a, b = m.groups()
    return (a, b) if "-n" in plan["apply_cmd"].split("patch")[0] else (b, a)


def container_name(plan):
    return plan["patch"]["spec"]["template"]["spec"]["containers"][0]["name"]


def read_live(plan):
    """Return (resources dict shaped like plan.before, deployment json)."""
    ns, w = target_of(plan)
    r = kubectl(plan, ["-n", ns, "get", "deploy", w, "-o", "json"])
    if r.returncode != 0:
        raise Refused(f"cannot read live deployment: {r.stderr.strip()[:200]}")
    dep = json.loads(r.stdout)
    cname = container_name(plan)
    c = next((c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == cname), None)
    if c is None:
        raise Refused(f"container {cname!r} not found in live deployment")
    res = c.get("resources") or {}
    return {"requests": res.get("requests") or {}, "limits": res.get("limits") or {},
            "replicas": dep["spec"].get("replicas", 1)}, dep


def same_resources(a, b):
    return (norm_res(a.get("requests")) == norm_res(b.get("requests"))
            and norm_res(a.get("limits")) == norm_res(b.get("limits"))
            and a.get("replicas") == b.get("replicas"))


def patched_target(plan):
    c = plan["patch"]["spec"]["template"]["spec"]["containers"][0]["resources"]
    return c.get("requests") or {}, c.get("limits") or {}


def matches_patch(plan, live):
    req, lim = patched_target(plan)
    return (all(norm_quantity(live["requests"].get(k)) == norm_quantity(v) for k, v in req.items() if k in live["requests"])
            and all(k in live["requests"] for k in req)
            and all(k in live["limits"] and norm_quantity(live["limits"][k]) == norm_quantity(v) for k, v in lim.items()))


# ---------------------------------------------------------------- observation

def parse_metrics(text):
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            parts = line.split()
            if len(parts) == 2:
                try:
                    out[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return out


def scrape_app(plan, port):
    ns, w = target_of(plan)
    r = kubectl(plan, ["get", "--raw", f"/api/v1/namespaces/{ns}/services/{w}:{port}/proxy/metrics"], timeout=20)
    if r.returncode != 0:
        return None
    return parse_metrics(r.stdout)


def pod_signals(plan, dep):
    ns, _ = target_of(plan)
    sel = ",".join(f"{k}={v}" for k, v in (dep["spec"].get("selector", {}).get("matchLabels") or {}).items())
    args = ["-n", ns, "get", "pods", "-o", "json"] + (["-l", sel] if sel else [])
    r = kubectl(plan, args)
    if r.returncode != 0:
        return None
    restarts = oom = 0
    for p in json.loads(r.stdout).get("items", []):
        if p["metadata"].get("deletionTimestamp"):
            continue
        for cs in p.get("status", {}).get("containerStatuses", []):
            restarts += cs.get("restartCount", 0)
            reasons = [((cs.get("lastState") or {}).get("terminated") or {}).get("reason"),
                       ((cs.get("state") or {}).get("terminated") or {}).get("reason")]
            oom += reasons.count("OOMKilled")
    return {"restarts": restarts, "oom_killed": oom}


def check_stop(conditions, values):
    """First stop condition that holds, as a dict, or None. Missing values never trigger here."""
    for c in conditions:
        v = values.get(c["signal"])
        if v is not None and OPS[c["op"]](v, c["value"]):
            return {**c, "observed": v}
    return None


def observe(plan, dep, port, poll_seconds):
    """Poll for observe_seconds. Returns (stop or None, last values, samples)."""
    conds = plan.get("stop_conditions", [])
    need_metric = {c["signal"] for c in conds} & set(METRIC_NAMES)
    deadline = time.monotonic() + plan.get("observe_seconds", 0)
    misses, values, samples = 0, {}, []
    while True:
        values = {}
        if need_metric:
            m = scrape_app(plan, port)
            if m is None:
                misses += 1
            else:
                misses = 0
                for sig in need_metric:
                    if METRIC_NAMES[sig] in m:
                        values[sig] = m[METRIC_NAMES[sig]]
        if {c["signal"] for c in conds} & KUBE_SIGNALS:
            ps = pod_signals(plan, dep)
            if ps:
                values.update(ps)
        samples.append({"t": now(), **values})
        log(f"observe {values}")
        if misses >= MAX_METRIC_MISSES:
            return {"signal": "metrics_unavailable", "op": ">=", "value": MAX_METRIC_MISSES, "observed": misses}, values, samples
        stop = check_stop(conds, values)
        if stop:
            return stop, values, samples
        if time.monotonic() >= deadline:
            return None, values, samples
        time.sleep(poll_seconds)


# ---------------------------------------------------------------- apply

def rollout_ok(plan, timeout_s=120):
    ns, w = target_of(plan)
    r = kubectl(plan, ["-n", ns, "rollout", "status", f"deploy/{w}", f"--timeout={timeout_s}s"], timeout=timeout_s + 30)
    return r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]


def append_record(path, rec):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log(f"outcome={rec['outcome']} reason={rec['reason']}")


def load_plans(path):
    doc = json.loads(Path(path).read_text())
    return doc["plans"] if isinstance(doc, dict) else doc


def apply_plan(plan, actions_path, root=Path("."), port=8080, poll_seconds=5):
    """Run one plan through every gate. Returns the final outcome record."""
    pid = plan.get("plan_id", "?")
    rec = {"plan_id": pid, "decision_id": plan.get("decision_id"), "context": plan.get("context"),
           "started_at": now()}

    def finish(outcome, reason, **extra):
        rec.update(outcome=outcome, reason=reason, finished_at=now(), **extra)
        append_record(actions_path, rec)
        return rec

    try:
        # 1. context
        if plan.get("context") != TEST_CONTEXT:
            return finish("refused", f"context {plan.get('context')!r} != {TEST_CONTEXT}")
        # 2. approval
        appr = Path(plan.get("approval_file", ""))
        appr = appr if appr.is_absolute() else root / appr
        if appr.name != f"{pid}.approved":
            return finish("refused", f"approval_file name must be {pid}.approved")
        if not appr.is_file():
            return finish("refused", f"approval file missing: {plan.get('approval_file')}")
        rec["approved_by_file"] = plan["approval_file"]
        # parse commands up front: a malformed plan must not reach the cluster
        apply_args = sanitize_cmd(plan, plan["apply_cmd"], {"patch", "scale", "set"})
        rollback_args = sanitize_cmd(plan, plan["rollback_cmd"])
        ns, w = target_of(plan)
        # 3. drift
        live, dep = read_live(plan)
        if not same_resources(live, plan["before"]):
            return finish("refused", "state drift: live resources differ from plan.before",
                          live=live, expected=plan["before"])
        # 4. patch with INTENT
        intent = (f"{pid}: canary {plan.get('decision_id')} on {ns}/{w}; apply the approved resource patch, "
                  f"observe {plan.get('observe_seconds')}s, roll back automatically on a stop condition")
        log(f"gates passed for {pid}; applying")
        r = kubectl(plan, apply_args, intent=intent)
        rec["executed"] = f"# INTENT: {intent}\n{shlex.join(['kubectl', '--context', plan['context'], *apply_args])}"
        if r.returncode != 0:
            return finish("failed", f"apply_cmd failed: {r.stderr.strip()[:200]}")
        rec["applied_at"] = now()
        append_record(actions_path, {**rec, "outcome": "applied", "reason": "patch applied, observing"})
        # 5. rollout
        ok, tail = rollout_ok(plan)
        stop = None if ok else {"signal": "rollout_failed", "op": "==", "value": 0, "observed": tail[0]}
        values, samples = {}, []
        # 6. observe
        if ok:
            stop, values, samples = observe(plan, dep, port, poll_seconds)
        rec.update(observed=values, samples=samples)
        # 7. rollback
        if stop:
            log(f"STOP condition hit: {stop}; rolling back")
            rintent = (f"{pid}: stop condition {stop['signal']} {stop['op']} {stop['value']} observed {stop['observed']}; "
                       f"restore the pre-change resources")
            rr = kubectl(plan, rollback_args, intent=rintent)
            rec["rollback_executed"] = f"# INTENT: {rintent}\n{shlex.join(['kubectl', '--context', plan['context'], *rollback_args])}"
            ok2, _ = rollout_ok(plan)
            live2, _ = read_live(plan)
            restored = rr.returncode == 0 and ok2 and same_resources(live2, plan["before"])
            if restored:
                return finish("rolled_back", f"stop condition {stop['signal']} {stop['op']} {stop['value']} "
                                             f"(observed {stop['observed']}); resources verified equal to before",
                              stop_condition=stop, live_after=live2)
            return finish("failed", "rollback did not restore before-state; manual intervention needed",
                          stop_condition=stop, live_after=live2)
        # verify target state
        live3, _ = read_live(plan)
        if matches_patch(plan, live3):
            return finish("verified", f"observed {plan['observe_seconds']}s without a stop condition; live resources match the patch",
                          live_after=live3)
        return finish("failed", "no stop condition but live resources do not match the patch", live_after=live3)
    except Refused as e:
        return finish("refused", str(e))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as e:
        return finish("failed", f"{type(e).__name__}: {e}")


def cmd_apply(a):
    plans = {p["plan_id"]: p for p in load_plans(a.plans)}
    if a.plan_id not in plans:
        print(f"unknown plan id {a.plan_id}; have: {sorted(plans)}", file=sys.stderr)
        return 2
    rec = apply_plan(plans[a.plan_id], Path(a.plans).parent / "k8s_actions.jsonl", root=Path(a.root),
                     port=a.metrics_port, poll_seconds=a.poll_seconds)
    return EXIT.get(rec["outcome"], 5)


def cmd_status(a):
    plans = load_plans(a.plans)
    path = Path(a.plans).parent / "k8s_actions.jsonl"
    latest = {}
    if path.exists():
        for line in path.read_text().splitlines():
            r = json.loads(line)
            latest[r["plan_id"]] = r
    for p in plans:
        if a.plan_id and p["plan_id"] != a.plan_id:
            continue
        r = latest.get(p["plan_id"])
        print(f"{p['plan_id']}\t{r['outcome'] if r else 'pending'}\t{r['reason'] if r else 'not attempted'}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="finops_agent.k8s.act", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("apply", help="run one approved plan through the gates")
    q.add_argument("--plans", required=True)
    q.add_argument("--plan-id", required=True)
    q.add_argument("--root", default=".", help="base directory for the plan's relative approval_file")
    q.add_argument("--metrics-port", type=int, default=8080)
    q.add_argument("--poll-seconds", type=float, default=5)
    q.set_defaults(fn=cmd_apply)
    q = sub.add_parser("status", help="latest outcome per plan")
    q.add_argument("--plans", required=True)
    q.add_argument("--plan-id")
    q.set_defaults(fn=cmd_status)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
