"""Regenerate the report fixtures from REAL pipeline runs against the synthetic K8s world (local test server, read-only).

Usage: PYTHONPATH=. python3 finops_agent/k8s/fixtures/make_fixtures.py
Two `k8s run`s sharing one ledger, as scripts/k8s_days.sh does for days 1 and 2 (LLM from cassettes, FINOPS_LLM_MODE=replay):
  day1/ (as_of 2026-10-25) and day2/ (as_of 2026-10-26): k8s_decisions.json, k8s_plans.json
  k8s_state.json         the ledger after day 2
  score.json             eval.score_k8s on day 2 (what `make k8s-eval` writes as k8s_eval.json)
Not touched here: demo/ (k8s_actions.jsonl and k8s_state.json copied from a real `make k8s-demo`, see scripts/k8s_demo.sh),
day2/k8s_verify.json and day2/k8s_unit_cost.json (hand-written; the real verify file comes with the verification change).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from finops_agent.__main__ import main as cli
from eval.score_k8s import score

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WORLD = ROOT / "testenv/k8s_world.yaml"
SLOS = ROOT / "testenv/k8s_slos.yaml"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    os.environ.setdefault("FINOPS_LLM_MODE", "replay")
    port = free_port()
    srv = subprocess.Popen([sys.executable, "-m", "testenv.metrics_server", "--world", "k8s", "--port", str(port)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
    try:
        time.sleep(2.5)
        with tempfile.TemporaryDirectory() as t:
            state = Path(t) / "k8s_state.json"
            prev = None
            for day, as_of in (("day1", "2026-10-25T00:00:00Z"), ("day2", "2026-10-26T00:00:00Z")):
                argv = ["k8s", "run", "--prom", f"http://127.0.0.1:{port}", "--slos", str(SLOS), "--nodes-from", str(WORLD),
                        "--as-of", as_of, "--run-id", f"k8s-{day}", "--out", str(Path(t) / "runs"), "--state", str(state),
                        "--world-label", "k8s-synth"] + (["--prev-run", str(prev)] if prev else [])
                cli(argv)
                run = Path(t) / "runs" / f"k8s-{day}"
                (HERE / day).mkdir(exist_ok=True)
                for f in ("k8s_decisions.json", "k8s_plans.json"):
                    shutil.copy(run / f, HERE / day / f)
                prev = run
            shutil.copy(state, HERE / "k8s_state.json")
            doc = json.loads((HERE / "day2/k8s_decisions.json").read_text())
            (HERE / "score.json").write_text(json.dumps(score(doc, yaml.safe_load(WORLD.read_text())), indent=2, ensure_ascii=False) + "\n")
    finally:
        srv.terminate()
    print("fixtures regenerated")


if __name__ == "__main__":
    main()
