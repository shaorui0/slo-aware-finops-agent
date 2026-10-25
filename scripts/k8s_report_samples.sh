#!/usr/bin/env bash
# Regenerate reports/k8s-sample/{daily,weekly,monthly}.html from REAL runs (no cluster, no network):
#   1. make_fixtures.py: two `k8s run`s on the synthetic world sharing one ledger (as scripts/k8s_days.sh days 1 and 2; LLM from
#      cassettes), plus the exam score for day 2 from eval.score_k8s  ->  fixtures day1/, day2/, k8s_state.json, score.json
#   2. daily   = day 2 vs day 1 with the days ledger
#      weekly  = day 2 with the REAL actions and ledger copied from a `make k8s-demo` run (fixtures/demo/)
#      monthly = day 2 with the score and the demo's actions
# The verify file (day2/k8s_verify.json) is still a hand-written fixture; the real one comes with the verification change.
# To refresh fixtures/demo/ after a new demo run:  cp runs/k8s-demo/k8s_actions.jsonl runs/k8s-demo/k8s_state.json finops_agent/k8s/fixtures/demo/
set -euo pipefail
cd "$(dirname "$0")/.."
F=finops_agent/k8s/fixtures
OUT=reports/k8s-sample
mkdir -p "$OUT"
PYTHONPATH=. python3 $F/make_fixtures.py >/dev/null
python3 -m finops_agent.k8s.report --run $F/day2 --prev $F/day1 --state $F/k8s_state.json --view daily --out $OUT/daily.html
python3 -m finops_agent.k8s.report --run $F/day2 --state $F/demo/k8s_state.json --actions $F/demo/k8s_actions.jsonl --view weekly --out $OUT/weekly.html
python3 -m finops_agent.k8s.report --run $F/day2 --state $F/demo/k8s_state.json --actions $F/demo/k8s_actions.jsonl --score $F/score.json --view monthly --out $OUT/monthly.html
echo "wrote $OUT/daily.html $OUT/weekly.html $OUT/monthly.html"
