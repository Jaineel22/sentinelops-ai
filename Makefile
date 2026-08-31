# Developer command interface for SentinelOps AI (Phase 0).
# On Windows, run these from Git Bash (bundled `make`) or use the equivalent
# commands documented in docs/development/setup.md.

VENV ?= .venv
ifeq ($(OS),Windows_NT)
  BIN := $(VENV)/Scripts
else
  BIN := $(VENV)/bin
endif
PY := $(BIN)/python

.DEFAULT_GOAL := help

.PHONY: help venv install test lint format typecheck check run docker-build docker-run

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

venv: ## Create the virtual environment
	python -m venv $(VENV)

install: ## Install runtime + dev dependencies into the venv (editable)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

test: ## Run the test suite
	$(PY) -m pytest

lint: ## Lint with Ruff
	$(PY) -m ruff check .

format: ## Auto-format with Ruff
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck: ## Static type-check with mypy
	$(PY) -m mypy

check: lint typecheck test ## Run all quality gates

run: ## Run the API locally with autoreload
	$(PY) -m uvicorn sentinelops_api.main:app --reload --app-dir apps/api

docker-build: ## Build the Docker image
	docker build -t sentinelops-ai:phase0 .

docker-run: ## Run the Docker image
	docker run --rm -p 8000:8000 sentinelops-ai:phase0
