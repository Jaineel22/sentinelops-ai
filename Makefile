# Developer command interface for SentinelOps AI.
# On Windows, run from Git Bash (bundled `make`) or use the equivalent commands
# in docs/development/setup.md.

VENV ?= .venv
ifeq ($(OS),Windows_NT)
  BIN := $(VENV)/Scripts
else
  BIN := $(VENV)/bin
endif
PY := $(BIN)/python

.DEFAULT_GOAL := help

.PHONY: help venv install test test-integration lint format typecheck check \
        run run-orders traffic \
        compose-up compose-down compose-logs \
        docker-build docker-build-orders \
        ml-test data-prepare nab-download ml-experiments ml-experiment ml-infer-demo \
        ml-docker-build \
        db-migrate db-revision run-correlator run-detector incident-scenario \
        docker-build-correlator docker-build-detector \
        db-migrate-rca run-rca rca-scenario rca-e2e-scenario docker-build-rca \
        db-migrate-remediation run-remediation remediation-e2e-scenario docker-build-remediation \
        mlops-retrain mlops-retrain-promote mlops-retrain-demo mlops-drift-retrain \
        docker-build-mlflow phase6-demo phase6-summary \
        phase7-verify phase7-summary

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-20s %s\n", $$1, $$2}'

venv: ## Create the virtual environment
	python -m venv $(VENV)

install: ## Install runtime + dev + ml + incident + rca + remediation dependencies (editable)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev,ml,incident,detector,rca,remediation]"

test: ## Run unit tests (no broker / DB needed)
	$(PY) -m pytest

test-integration: ## Run integration tests (needs kafka+postgres + db-migrate{,-rca,-remediation})
	KAFKA_BOOTSTRAP_SERVERS=localhost:29092 \
	DB_URL=postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops \
	DB_TEST_URL=postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops \
	$(PY) -m pytest -m integration

lint: ## Lint with Ruff
	$(PY) -m ruff check .

format: ## Auto-format with Ruff
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck: ## Static type-check with mypy
	$(PY) -m mypy

check: lint typecheck test ## Run all quality gates

run: ## Run the SentinelOps platform API locally (:8000)
	$(PY) -m uvicorn sentinelops_api.main:app --reload --app-dir apps/api

run-orders: ## Run orders-service locally (:8001) against localhost:29092
	KAFKA_BOOTSTRAP_SERVERS=localhost:29092 \
	$(PY) -m uvicorn orders_service.app:app --reload --app-dir apps/orders-service --port 8001

traffic: ## Generate demo traffic (SCENARIO=normal|latency|errors|surge|sequence)
	$(PY) scripts/generate_traffic.py --scenario $(or $(SCENARIO),normal) --duration $(or $(DURATION),60)

compose-up: ## Start the Phase 1 environment (Kafka + services)
	docker compose up --build -d
	@echo "orders-service: http://localhost:8001  |  platform API: http://localhost:8000"

compose-down: ## Stop the Phase 1 environment
	docker compose down

compose-logs: ## Tail compose logs
	docker compose logs -f

docker-build: ## Build the platform API image
	docker build -t sentinelops-ai:api .

docker-build-orders: ## Build the orders-service image
	docker build -f apps/orders-service/Dockerfile -t sentinelops-ai:orders-service .

docker-build-correlator: ## Build the incident-correlator image
	docker build -f services/incident-correlator/Dockerfile -t sentinelops-ai:incident-correlator .

docker-build-detector: ## Build the anomaly-detector image
	docker build -f services/anomaly-detector/Dockerfile -t sentinelops-ai:anomaly-detector .

docker-build-rca: ## Build the rca-agent image
	docker build -f services/rca-agent/Dockerfile -t sentinelops-ai:rca-agent .

# --- Phase 3: incident correlation -------------------------------------
DB_URL ?= postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops

db-migrate: ## Apply incident-correlator DB migrations (needs Postgres up)
	cd services/incident-correlator && DB_URL=$(DB_URL) $(abspath $(BIN))/alembic upgrade head

db-revision: ## Autogenerate a migration: MSG="add x column"
	cd services/incident-correlator && DB_URL=$(DB_URL) $(abspath $(BIN))/alembic revision --autogenerate -m "$(MSG)"

run-correlator: ## Run incident-correlator locally (:8002) against localhost
	KAFKA_BOOTSTRAP_SERVERS=localhost:29092 DB_URL=$(DB_URL) APP_PORT=8002 \
	$(PY) -m incident_correlator

run-detector: ## Run anomaly-detector locally (:8003) against localhost
	KAFKA_BOOTSTRAP_SERVERS=localhost:29092 APP_PORT=8003 \
	DETECTOR_TARGET_METRICS_URL=http://localhost:8001/metrics \
	$(PY) -m anomaly_detector

incident-scenario: ## Deterministic end-to-end demo: telemetry -> anomalies -> ONE incident
	$(PY) scripts/incident_scenario.py

# --- Phase 4: AI RCA agent ---------------------------------------------
db-migrate-rca: ## Apply rca-agent DB migrations (needs Postgres up)
	cd services/rca-agent && DB_URL=$(DB_URL) $(abspath $(BIN))/alembic upgrade head

run-rca: ## Run rca-agent locally (:8004) against localhost (RCA_MODE=mock)
	KAFKA_BOOTSTRAP_SERVERS=localhost:29092 DB_URL=$(DB_URL) APP_PORT=8004 \
	KAFKA_CONSUMER_GROUP=rca-agent RCA_INCIDENT_API_BASE_URL=http://localhost:8002 \
	$(PY) -m rca_agent

rca-scenario: ## Deterministic in-process demo: one incident -> investigation -> RCA
	$(PY) scripts/rca_scenario.py

rca-e2e-scenario: ## Deterministic full-chain demo: incident.opened (Kafka shape) -> RCA -> API
	$(PY) scripts/rca_e2e_scenario.py

# --- Phase 5: human-approved remediation ------------------------------
db-migrate-remediation: ## Apply remediation-controller DB migrations (needs Postgres up)
	cd services/remediation-controller && DB_URL=$(DB_URL) $(abspath $(BIN))/alembic upgrade head

run-remediation: ## Run remediation-controller locally (:8005) against localhost
	KAFKA_BOOTSTRAP_SERVERS=localhost:29092 DB_URL=$(DB_URL) APP_PORT=8005 $(PY) -m remediation_controller

remediation-e2e-scenario: ## Deterministic full-chain demo: incident -> RCA -> approve -> execute -> verify -> events
	$(PY) scripts/remediation_e2e_scenario.py

docker-build-remediation: ## Build the remediation-controller image
	docker build -f services/remediation-controller/Dockerfile -t sentinelops-ai:remediation-controller .

# --- Phase 2: ML anomaly detection ---------------------------------------
ml-test: ## Run only the ML test suite
	$(PY) -m pytest tests/ml

data-generate: ## (needs compose + host orders-service) collect a Track A run: RUN_ID=run_a PLAN=main
	$(PY) -m ml.collection.collector --run-id $(or $(RUN_ID),run_a) --plan $(or $(PLAN),main)

data-prepare: ## Turn raw metric snapshots into committed window datasets
	$(PY) -m ml.data.prepare run_a run_b

nab-download: ## Download the pinned NAB benchmark series (git-ignored)
	$(PY) -m ml.data.nab download

ml-experiments: ## Run all experiments on committed data -> artifacts/reports/
	$(PY) -m ml.experiments run all

ml-experiment: ## Run one experiment: NAME=exp2_isolation_forest_sentinelops
	$(PY) -m ml.experiments run $(NAME)

ml-infer-demo: ## Score a processed run through the saved Isolation Forest
	$(PY) -m ml.inference artifacts/models/exp2_isolation_forest_sentinelops__isolation_forest.joblib \
		ml/data/processed/sentinelops/run_a/windows.csv

ml-docker-build: ## Build the ML pipeline image
	docker build -f ml/Dockerfile -t sentinelops-ai:ml .

# --- Phase 6: MLOps lifecycle ------------------------------------------
# These need a reachable MLflow tracking store. Offline default: a local sqlite
# store; or export MLFLOW_TRACKING_URI=http://localhost:5000 for the compose server.
MLFLOW_TRACKING_URI ?= sqlite:///mlruns/mlflow.db

docker-build-mlflow: ## Build the MLflow tracking-server image
	docker build -f docker/mlflow/Dockerfile -t sentinelops-ai:mlflow .

mlops-retrain: ## Retrain isolation_forest on run_a (seed 42), run the gate, do NOT promote
	MLFLOW_TRACKING_URI=$(MLFLOW_TRACKING_URI) $(PY) -m ml.mlops retrain --dataset run_a --seed 42

mlops-retrain-promote: ## Retrain on run_a and auto-promote to champion if the gate passes
	MLFLOW_TRACKING_URI=$(MLFLOW_TRACKING_URI) \
	  $(PY) -m ml.mlops retrain --dataset run_a --seed 42 --promote-if-passing

mlops-retrain-demo: ## Phase 6E demo: retrain -> track -> register -> gate
	MLFLOW_TRACKING_URI=$(MLFLOW_TRACKING_URI) $(PY) scripts/retraining_demo.py

mlops-drift-retrain: ## Phase 6E demo: drift detected -> retrain -> gate -> promote/reject
	MLFLOW_TRACKING_URI=$(MLFLOW_TRACKING_URI) $(PY) scripts/drift_triggered_retraining.py

phase6-demo: ## Phase 6 end-to-end: train champion -> register -> drift -> retrain -> gate -> promote
	$(PY) scripts/phase6_e2e_demo.py

phase6-summary: ## Print the Phase 6 completion summary
	@echo "=== Phase 6 - MLOps lifecycle (complete) ==="
	@echo "6A tracking . 6B registry+aliases . 6C inference . 6D drift . 6E retraining . 6F docs+e2e"
	@echo "ADRs: 031 (tracking+registry) . 032 (aliases) . 033 (promotion gate) . 034 (PSI drift)"
	@echo "docs: docs/architecture/phase-6.md . docs/phase6-summary.md"
	@echo "run 'make phase6-demo' (sqlite, no server) for end-to-end validation"

# --- Phase 7: real-time ML inference observability -------------------
phase7-verify: ## Phase 7 end-to-end verification (in-process; --url for a live detector)
	$(PY) scripts/phase7_verify.py

phase7-summary: ## Print the Phase 7 completion summary
	@echo "=== Phase 7 - Real-Time ML Inference Observability (complete) ==="
	@echo "7A inference metrics . 7B detection-latency timeline . 7C enhanced /ready + service aggregates"
	@echo "7D Prometheus + Grafana (12-panel dashboard) . 7E docs + verification"
	@echo "docs: docs/architecture/phase-7.md . docs/phase7-summary.md"
	@echo "Grafana http://localhost:3000 (admin/admin) after 'docker compose up'; 'make phase7-verify'"
