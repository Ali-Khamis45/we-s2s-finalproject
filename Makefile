# Speech Confidence Coach
#
# `make help` lists everything. On Windows use scripts/make.ps1, which mirrors
# these targets.
#
# One boundary worth stating up front: `bench` needs downloaded models and a
# served LLM, so it cannot run in CI. `bench-fast` is the model-free subset that
# can — it proves the harness still executes, not the numbers.

BACKEND := backend
FRONTEND := frontend
PY := $(BACKEND)/.venv/bin/python
PYTHONPATH := $(CURDIR)/$(BACKEND)
export PYTHONPATH

.DEFAULT_GOAL := help
.PHONY: help setup dev test test-backend test-frontend lint types check-types \
        bench bench-fast eval-retrieval schema clean

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install both halves
	python -m venv $(BACKEND)/.venv
	$(PY) -m pip install -r $(BACKEND)/requirements.txt
	cd $(FRONTEND) && npm install

dev: ## Run backend and frontend together
	@echo "backend  http://127.0.0.1:8000/docs"
	@echo "frontend http://localhost:5173"
	@$(PY) -m uvicorn app.main:app --app-dir $(BACKEND) --port 8000 & \
	 cd $(FRONTEND) && npm run dev; \
	 kill %1

test: test-backend test-frontend ## Run every test

test-backend: ## Backend suite (no models needed)
	cd $(BACKEND) && .venv/bin/python -m pytest -q

test-frontend: ## Frontend suite
	cd $(FRONTEND) && npm test

lint: ## Ruff and TypeScript
	cd $(BACKEND) && .venv/bin/python -m ruff check app tests scripts
	cd $(FRONTEND) && npx tsc --noEmit

schema: ## Write docs/openapi.json from the app
	$(PY) $(BACKEND)/scripts/dump_openapi.py

types: schema ## Regenerate the frontend types from the schema
	cd $(FRONTEND) && npx openapi-typescript ../docs/openapi.json -o src/lib/api-types.gen.ts
	@echo "Commit docs/openapi.json and api-types.gen.ts together with the change."

check-types: ## Fail if the committed schema or types are stale (CI)
	$(PY) $(BACKEND)/scripts/dump_openapi.py --check
	cd $(FRONTEND) && npx openapi-typescript ../docs/openapi.json -o /tmp/api-types.check.ts \
		&& diff -q /tmp/api-types.check.ts src/lib/api-types.gen.ts

bench: ## Full verification run. NEEDS MODELS AND A SERVED LLM — not for CI.
	$(PY) $(BACKEND)/scripts/verify_acoustic_branch.py $(BACKEND)/scripts/words
	$(PY) $(BACKEND)/scripts/verify_retrieval.py
	$(PY) $(BACKEND)/scripts/bench_whisper.py $(BACKEND)/scripts/words/dysfluent_utterance.wav
	$(PY) $(BACKEND)/scripts/bench_latency.py $(BACKEND)/scripts/words/dysfluent_utterance.wav 5

bench-fast: ## The model-free subset CI can run: proves the harness executes
	cd $(BACKEND) && .venv/bin/python -m pytest -q tests/test_acoustic.py tests/test_contract.py

eval-retrieval: ## Recall@k, MRR, nDCG and the gate curve (needs the corpus)
	$(PY) $(BACKEND)/scripts/calibrate_gate.py

clean: ## Remove build output and caches
	rm -rf $(FRONTEND)/dist $(BACKEND)/.pytest_cache
	find $(BACKEND) -name __pycache__ -type d -prune -exec rm -rf {} +
