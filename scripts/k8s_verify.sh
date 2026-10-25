#!/usr/bin/env bash
# K8s savings verification end to end (c0033).
#   1. serve the base k8s world, run the agent (template narration, no LLM) -> decisions and plans BEFORE any change
#   2. verify the applied changes against the after-scenario overlays (testenv/k8s_scenarios/after_*.yaml)
#   3. write runs/k8s-verify/k8s_verify.json (contract shape), k8s_verify_detail.json and docs/K8S_SAVINGS_VERIFICATION.md
# Read-only: no kubectl, no AWS. Port 19394 (override with K8S_VERIFY_PORT).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python3}
PORT=${K8S_VERIFY_PORT:-19394}
RUN=${K8S_VERIFY_RUN:-k8s-verify}
OUT_DOC=${OUT_DOC:-docs/K8S_SAVINGS_VERIFICATION.md}
if lsof -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then echo "port $PORT is busy" >&2; exit 1; fi
rm -rf runs/$RUN; mkdir -p runs/$RUN
$PY -m testenv.metrics_server --port $PORT --world k8s 2>/dev/null & PID=$!
trap '{ kill $PID; wait $PID; } 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do curl -sf localhost:$PORT/-/healthy >/dev/null && break; sleep 0.5; done
curl -sf localhost:$PORT/-/healthy >/dev/null || { echo "metrics server on $PORT did not start" >&2; exit 1; }
$PY -m finops_agent k8s run --prom http://127.0.0.1:$PORT --slos testenv/k8s_slos.yaml --nodes-from testenv/k8s_world.yaml \
  --run-id $RUN --state runs/$RUN/k8s_state.json --world-label k8s-synth --narrate template >/dev/null
$PY -m finops_agent.k8s.verify --decisions runs/$RUN/k8s_decisions.json --plans runs/$RUN/k8s_plans.json \
  --scenario testenv/k8s_scenarios/after_apply.yaml --control testenv/k8s_scenarios/after_clean.yaml \
  --out-dir runs/$RUN --doc "$OUT_DOC"
echo "wrote runs/$RUN/k8s_verify.json and $OUT_DOC"
