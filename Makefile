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
        docker-build docker-build-orders

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-20s %s\n", $$1, $$2}'

venv: ## Create the virtual environment
	python -m venv $(VENV)

install: ## Install runtime + dev dependencies (editable)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

test: ## Run unit tests (no broker needed)
	$(PY) -m pytest

test-integration: ## Run integration tests (needs `make compose-up` first)
	KAFKA_BOOTSTRAP_SERVERS=localhost:29092 $(PY) -m pytest -m integration

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
