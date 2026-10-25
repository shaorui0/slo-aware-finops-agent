#!/usr/bin/env bash
# Local k3d demo of the controlled-execution path (c0030). Touches ONLY cluster finops-test.
# Usage: bash scripts/k8s_demo.sh        (or: make k8s-demo)   Tear down: make k8s-down
set -uo pipefail
cd "$(dirname "$0")/.."

CLUSTER=finops-test
CTX=k3d-finops-test
RUN=runs/k8s-demo
PLANS=$RUN/k8s_plans.json
OBSERVE=${OBSERVE_SECONDS:-45}
mkdir -p "$RUN" runs/approvals
: > "$RUN/demo.log"
rm -f "$RUN/k8s_actions.jsonl" runs/approvals/P-K-shop-api-steady-cpu-canary.approved

say() { echo "$*" | tee -a "$RUN/demo.log"; }
step() { say ""; say "=== [$1] $2"; }
# every cluster command names the test context explicitly; writes carry an INTENT line
kc() { kubectl --context "$CTX" "$@"; }
kw() { local why=$1; shift; say "# INTENT: $why"; kc "$@" 2>&1 | tee -a "$RUN/demo.log"; }
act() { python3 -m finops_agent.k8s.act "$@" 2>&1 | tee -a "$RUN/demo.log"; return "${PIPESTATUS[0]}"; }
metrics() { kc get --raw /api/v1/namespaces/shop/services/api-steady:8080/proxy/metrics 2>/dev/null | tr '\n' ' '; }
resources() { kc -n shop get deploy api-steady -o jsonpath='{.spec.template.spec.containers[0].resources}'; }
set_load() { kw "set loadgen to $1 rps to drive the canary" -n shop set env deploy/loadgen RPS="$1" >/dev/null
             kc -n shop rollout status deploy/loadgen --timeout=120s | tee -a "$RUN/demo.log"; }

step 1 "create cluster $CLUSTER (idempotent)"
if k3d cluster list "$CLUSTER" >/dev/null 2>&1; then
  say "cluster exists, reusing"; k3d cluster start "$CLUSTER" 2>&1 | tail -1 | tee -a "$RUN/demo.log"
else
  k3d cluster create "$CLUSTER" --servers 1 --agents 0 --no-lb --wait \
    --kubeconfig-switch-context=false 2>&1 | tail -3 | tee -a "$RUN/demo.log"
fi
if docker image inspect python:3.12-alpine >/dev/null 2>&1 || docker pull -q python:3.12-alpine >/dev/null 2>&1; then
  k3d image import python:3.12-alpine -c "$CLUSTER" 2>&1 | tail -1 | tee -a "$RUN/demo.log"
fi
kc get nodes 2>&1 | tee -a "$RUN/demo.log"

step 2 "deploy namespace shop: api-steady (over-provisioned) + loadgen"
kw "create the demo workload in the disposable test cluster" apply -k ops/k3d
kc -n shop rollout status deploy/api-steady --timeout=180s | tee -a "$RUN/demo.log"
kc -n shop rollout status deploy/loadgen --timeout=180s | tee -a "$RUN/demo.log"
say "resources before: $(resources)"
sleep 10; say "baseline metrics: $(metrics)"

step 3 "write k8s_plans.json (hand-written fixture in the c0027 contract shape)"
PLAN_ID=$(python3 ops/k3d/make_plan.py "$RUN" "$OBSERVE")
say "plan: $PLAN_ID  observe_seconds=$OBSERVE"

step 4 "refusal 1: no approval file"
act apply --plans $PLANS --plan-id "$PLAN_ID"; say "exit=$? (3 = refused)"

step 5 "refusal 2: wrong context (copy of the plan edited)"
act apply --plans $RUN/k8s_plans_wrongctx.json --plan-id "$PLAN_ID"; say "exit=$? (3 = refused)"
say "resources unchanged: $(resources)"

step 6 "human approves: create the approval file"
echo "approved by demo operator $(date -u +%FT%TZ)" > "runs/approvals/$PLAN_ID.approved"
ls runs/approvals | tee -a "$RUN/demo.log"

step 7 "inject heavy load (40 rps), apply canary: expect stop condition and automatic rollback"
set_load 40
sleep 10; say "metrics under heavy load before patch: $(metrics)"
act apply --plans $PLANS --plan-id "$PLAN_ID"; RC1=$?; say "exit=$RC1 (4 = rolled_back)"
say "resources after rollback: $(resources)"

step 8 "reset to normal load (5 rps), apply again: expect verified"
set_load 5
sleep 10
act apply --plans $PLANS --plan-id "$PLAN_ID"; RC2=$?; say "exit=$RC2 (0 = verified)"
say "resources after canary: $(resources)"
say "final metrics: $(metrics)"

step 9 "summary"
act status --plans $PLANS
say "--- k8s_actions.jsonl (outcome, reason)"
python3 - <<'PY' | tee -a "$RUN/demo.log"
import json
for l in open("runs/k8s-demo/k8s_actions.jsonl"):
    r = json.loads(l)
    print(f"{r['finished_at'] if 'finished_at' in r else r.get('applied_at')}  {r['outcome']:<12} {r['reason']}")
PY
if [ "$RC1" = 4 ] && [ "$RC2" = 0 ]; then say "DEMO OK: refusals, rollback and verified path all behaved"; exit 0; fi
say "DEMO UNEXPECTED: rollback rc=$RC1 success rc=$RC2"; exit 1
