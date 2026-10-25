#!/usr/bin/env bash
# Three simulated days of the K8s mainline, sharing one proposal ledger (own temp state, own ports).
#   day 1  base world, as_of 2026-10-25   -> proposals are created
#   day 2  base world, as_of 2026-10-26   -> same proposals stay (unchanged); a service that started burning its SLO is expired
#   day 3  drift scenario, as_of 2026-10-28 (api-steady load +60%) -> its proposal is expired and re-proposed with the new target
# Prints per day: new / unchanged / expired counts, then what moved. LLM mode comes from FINOPS_LLM_MODE (default replay).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python3}
export FINOPS_LLM_MODE=${FINOPS_LLM_MODE:-replay}
BASE_PORT=${K8S_DAYS_BASE_PORT:-16992} DRIFT_PORT=${K8S_DAYS_DRIFT_PORT:-16993}
TMP=$(mktemp -d)
STATE=$TMP/k8s_state.json
PIDS=()
cleanup() { { kill "${PIDS[@]}"; wait "${PIDS[@]}"; } 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT
for port in $BASE_PORT $DRIFT_PORT; do
  if lsof -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then echo "port $port is busy" >&2; exit 1; fi
done
$PY -m testenv.metrics_server --port $BASE_PORT --world k8s 2>/dev/null & PIDS+=($!)
$PY -m testenv.metrics_server --port $DRIFT_PORT --world testenv/k8s_scenarios/drift_day3.yaml 2>/dev/null & PIDS+=($!)
for port in $BASE_PORT $DRIFT_PORT; do
  for _ in $(seq 1 60); do curl -sf localhost:$port/-/healthy >/dev/null && break; sleep 0.5; done
  curl -sf localhost:$port/-/healthy >/dev/null || { echo "metrics server on $port did not start" >&2; exit 1; }
done

# day <run-id> <port> <as_of> <label>
day() {
  $PY -m finops_agent k8s run --prom http://127.0.0.1:$2 --slos testenv/k8s_slos.yaml --nodes-from testenv/k8s_world.yaml \
    --as-of "$3" --run-id "$1" --out "$TMP/runs" --state "$STATE" --world-label k8s-synth >/dev/null 2>"$TMP/$1.err" \
    || { cat "$TMP/$1.err" >&2; exit 1; }
  $PY - "$TMP/runs/$1" "$STATE" "$4" <<'PY'
import json, sys
out, state, label = sys.argv[1:4]
s = json.load(open(out + "/k8s_summary.json"))
c, keys = s["ledger"], s["ledger_keys"]
print(f"== {s['run_id']} ({label}, as_of {s['as_of']}) ==")
print(f"   new={c['new']}  unchanged={c['unchanged']}  expired={c['expired']}   plans={s['plans']}  verdicts={s['verdicts']}")
st = json.load(open(state))
for k in ("expired", "new"):
    for key in keys.get(k, []):
        want = "expired" if k == "expired" else "proposed"
        hist = [h for p in st["proposals"] if p["key"] == key for h in p["history"] if h["status"] == want and h["run_id"] == s["run_id"]]
        note = hist[-1]["note"] if hist else ""
        print(f"   {k:9s} {key:30s} {note}")
PY
}

day k8s-day1 $BASE_PORT  2026-10-25T00:00:00Z "day 1, base world"
day k8s-day2 $BASE_PORT  2026-10-26T00:00:00Z "day 2, base world, one day later"
day k8s-day3 $DRIFT_PORT 2026-10-28T00:00:00Z "day 3, drift_day3: api-steady load +60%"

$PY - "$STATE" <<'PY'
import json, sys
st = json.load(open(sys.argv[1]))
print("== api-steady/cpu: ledger history ==")
for p in (p for p in st["proposals"] if p["key"] == "shop/api-steady/cpu"):
    print(f"   {p['plan_id']:34s} {p['status']:9s} first_seen {p['first_seen']}  last_seen {p['last_seen']}")
    for h in p["history"]:
        print(f"      {h['run_id']}  {h['status']:9s} {h['note']}")
PY
