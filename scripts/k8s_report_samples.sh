#!/usr/bin/env bash
# Regenerate reports/k8s-sample/{daily,weekly,monthly}.html from the committed fixtures (no network, no cluster).
set -euo pipefail
cd "$(dirname "$0")/.."
F=finops_agent/k8s/fixtures
OUT=reports/k8s-sample
mkdir -p "$OUT"
python3 -m finops_agent.k8s.report --run $F/day2 --prev $F/day1 --state $F/k8s_state.json --view daily --out $OUT/daily.html
python3 -m finops_agent.k8s.report --run $F/day2 --state $F/k8s_state.json --view weekly --out $OUT/weekly.html
python3 -m finops_agent.k8s.report --run $F/day2 --state $F/k8s_state.json --score $F/score.json --view monthly --out $OUT/monthly.html
echo "wrote $OUT/daily.html $OUT/weekly.html $OUT/monthly.html"
