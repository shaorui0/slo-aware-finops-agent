#!/usr/bin/env bash
# c0035: the single evaluation of the pre-registered fresh K8s world.
#   bash eval/k8s_heldout.sh            replay (cassettes under eval/cassettes/), deterministic
#   FINOPS_LLM_MODE=record bash eval/k8s_heldout.sh   the one recording run (needs `claude` on PATH)
# Serves testenv/heldout/k8s_fresh_world.yaml, runs the K8s agent with the fresh SLO catalog, scores against the world's `expect`.
# The score is printed as-is; a non-zero exit means a gate failed (that is a finding, not something to tune away here).
set -u
cd "$(dirname "$0")/.."
PY=${PY:-python3}
WORLD=${K8S_WORLD:-testenv/heldout/k8s_fresh_world.yaml}
SLOS=${K8S_SLOS:-testenv/heldout/k8s_fresh_slos.yaml}
RUN=${K8S_RUN:-k8s-fresh}
PORT=${K8S_HELDOUT_PORT:-$($PY -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')}
STATE=$(mktemp -d)/k8s_state.json
rm -rf "runs/$RUN"; mkdir -p runs
$PY -m testenv.metrics_server --port "$PORT" --world "$WORLD" 2>/dev/null & pid=$!
ok=1; for _ in $(seq 1 60); do curl -sf "localhost:$PORT/-/healthy" >/dev/null && ok=0 && break; sleep 0.5; done
status=1
if [ $ok = 0 ]; then
  $PY -m finops_agent k8s run --prom "http://127.0.0.1:$PORT" --slos "$SLOS" --nodes-from "$WORLD" \
      --run-id "$RUN" --state "$STATE" --world-label ${K8S_LABEL:-k8s-fresh} \
  && $PY -m eval.score_k8s "runs/$RUN/k8s_decisions.json" --world "$WORLD"
  status=$?
fi
{ kill $pid; wait $pid; } 2>/dev/null
exit $status
