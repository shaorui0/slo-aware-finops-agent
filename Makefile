PY ?= python3
AWS_PORT ?= 4566
PROM_PORT ?= 9090
RUN ?= local
# replay | record | live, see finops_agent/llm.py
export FINOPS_LLM_MODE ?= replay
PROM_URL ?= http://127.0.0.1:9090
AWS_ENDPOINT ?=

.PHONY: test check check-docs servers demo eval record stop alerts-test inspect rot-demo

# Docs checks (links, heading language) are on by default; DOCS_CHECK= (empty) turns them off
DOCS_CHECK ?= 1

# Every test module in the repo, found by discovery (a new test_*.py is picked up without editing this file).
# In-process servers bind port 0 (OS-chosen) except eval/heldout.py, whose block is 47500-47599 (FINOPS_HELDOUT_PORTS overrides).
test:            ## all unit and end-to-end tests (every finops_agent/ eval/ testenv/ tools/ hooks/ test_*.py)
	$(PY) -W ignore -m unittest discover -s hooks -p 'test_*.py'
	$(PY) -m unittest discover -s tools -p 'test_*.py'
	$(PY) -W ignore -m unittest discover -t . -s finops_agent -p 'test_*.py'
	$(PY) -W ignore -m unittest discover -t . -s eval -p 'test_*.py'
	$(PY) -W ignore -m unittest discover -t . -s testenv -p 'test_*.py'

check:           ## rules layer + leak scan; docs checks (links, heading language) only when DOCS_CHECK=1
	$(PY) tools/check_rules.py $(if $(filter 1,$(DOCS_CHECK)),--docs,)

check-docs:      ## only the documentation checks: relative links / backticked paths resolve, headings contain Chinese
	$(PY) tools/check_rules.py --docs

servers:         ## start both test servers in the background
	$(PY) -m testenv.aws_server --port $(AWS_PORT) & echo $$! > .aws_server.pid
	$(PY) -m testenv.metrics_server --port $(PROM_PORT) & echo $$! > .metrics_server.pid
	@for i in 1 2 3 4 5 6 7 8 9 10; do curl -sf localhost:$(AWS_PORT)/health >/dev/null && curl -sf localhost:$(PROM_PORT)/-/healthy >/dev/null && exit 0; sleep 1; done; exit 1

demo: servers    ## run the agent against the synthetic account
	$(PY) -m finops_agent run --aws-endpoint http://127.0.0.1:$(AWS_PORT) --prom http://127.0.0.1:$(PROM_PORT) --run-id $(RUN) --no-state

eval: demo       ## score the run against ground truth; fails on trap/invariant violations or regression
	$(PY) -m eval.score runs/$(RUN)/findings.json; status=$$?; $(MAKE) stop; exit $$status

record:          ## re-record LLM cassettes with real `claude -p` calls (delete stale eval/cassettes/*.json first), then score
	FINOPS_LLM_MODE=record $(MAKE) eval

rot-demo:        ## three runs against base / next_month / month_after: shows resolved, regressed and new (own ports, own temp state)
	# no judge: the later-month worlds have no LLM cassettes (recording is a separate, deliberate act); rot is decided by deterministic detectors
	FINOPS_NO_JUDGE=1 bash scripts/rot_demo.sh

stop:
	-@kill `cat .aws_server.pid` `cat .metrics_server.pid` 2>/dev/null; rm -f .aws_server.pid .metrics_server.pid

PROMTOOL ?= docker run --rm -v $(CURDIR)/config/alerts:/w -w /w --entrypoint promtool prom/prometheus:v2.53.0

alerts-test:     ## validate and unit-test the FinOps alert rules
	$(PROMTOOL) check rules finops.rules.yml
	$(PROMTOOL) test rules finops.rules.test.yml

inspect:         ## one inspection run with cross-run state (what cron calls); exports runs/<id>/finops_metrics.prom
	$(PY) -m finops_agent run $(if $(AWS_ENDPOINT),--aws-endpoint $(AWS_ENDPOINT),) --prom $(PROM_URL) --run-id inspect-$$(date -u +%Y%m%dT%H%M%SZ)

# ---- K8s mainline (c0031) -------------------------------------------------------------------
K8S_PORT ?= 19190
K8S_RUN ?= k8s-local

.PHONY: k8s-eval k8s-days

k8s-eval:        ## K8s mainline: serve the synthetic k8s world, run the agent (LLM from cassettes), score against expect; fails on unsafe ALLOW, thresholds, regression or a missing cassette
	@rm -rf runs/$(K8S_RUN); mkdir -p runs
	@$(PY) -m testenv.metrics_server --port $(K8S_PORT) --world k8s 2>/dev/null & pid=$$!; \
	ok=1; for i in $$(seq 1 60); do curl -sf localhost:$(K8S_PORT)/-/healthy >/dev/null && ok=0 && break; sleep 0.5; done; \
	status=1; if [ $$ok = 0 ]; then \
	  $(PY) -m finops_agent k8s run --prom http://127.0.0.1:$(K8S_PORT) --slos testenv/k8s_slos.yaml --nodes-from testenv/k8s_world.yaml \
	    --run-id $(K8S_RUN) --state runs/$(K8S_RUN)/k8s_state.json --world-label k8s-synth \
	  && $(PY) -m eval.score_k8s runs/$(K8S_RUN)/k8s_decisions.json; status=$$?; fi; \
	{ kill $$pid; wait $$pid; } 2>/dev/null; exit $$status

k8s-days:        ## three simulated days sharing one proposal ledger: new / unchanged / expired per day, day 3 on the drift scenario
	bash scripts/k8s_days.sh
