# LeagueOfLeagues — root Makefile.
#
# Conventions:
#   - Python package/runtime manager = uv (provided by the python-base image).
#   - Lint/format = ruff, types = mypy, tests = pytest.
#   - Docker base images are built FIRST, then `docker compose up`.
#
# All targets are `make -n` friendly: the dry run prints exactly what would run.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- Tooling (override on the CLI, e.g. `make test UV=uv`) -------------------
UV            ?= uv
DOCKER        ?= docker
COMPOSE       ?= $(DOCKER) compose
COMPOSE_FILE  ?= infra/docker-compose.yml
COMPOSE_CMD   := $(COMPOSE) -f $(COMPOSE_FILE)

# --- Docker base images ------------------------------------------------------
PYTHON_BASE_IMAGE   ?= lol/python-base:dev
NODE_BASE_IMAGE     ?= lol/node-base:dev
PYTHON_BASE_CONTEXT := infra/images/python-base
NODE_BASE_CONTEXT   := infra/images/node-base

# --- Python package directories (each is a STANDALONE uv project) ------------
# Edit this list as packages land. Targets loop over it and skip any dir that
# does not yet exist, so the Makefile is usable throughout Phase 0 scaffolding.
PY_LIB_DIRS := \
	libs/lol-core \
	libs/lol-schemas \
	libs/lol-riot \
	libs/lol-realtime \
	libs/lol-discord

PY_SERVICE_DIRS := \
	services/backend \
	services/worker \
	services/bot

PY_PKG_DIRS := $(PY_LIB_DIRS) $(PY_SERVICE_DIRS)

.PHONY: help up down build-bases test test-db lint format typecheck gen-clients fixtures \
	dev dev-backend dev-frontend

# --- DB / RLS integration test config ----------------------------------------
# Local equivalent of the CI `db-rls` job. Assumes a REACHABLE Postgres (e.g.
# `make up`, or a local server) listening on $(DB_HOST):$(DB_PORT) with the owner
# role/db `lol/lol`/`lol`. Override on the CLI, e.g. `make test-db DB_HOST=db`.
DB_HOST          ?= localhost
DB_PORT          ?= 5432
OWNER_DATABASE_URL ?= postgresql+asyncpg://lol:lol@$(DB_HOST):$(DB_PORT)/lol
APP_DATABASE_URL   ?= postgresql+asyncpg://app_rw:app_rw@$(DB_HOST):$(DB_PORT)/lol

help: ## Show this help.
	@echo "LeagueOfLeagues — available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- Build / run the stack ---------------------------------------------------
build-bases: ## Build the shared python-base and node-base Docker images.
	$(DOCKER) build -t $(PYTHON_BASE_IMAGE) $(PYTHON_BASE_CONTEXT)
	$(DOCKER) build -t $(NODE_BASE_IMAGE) $(NODE_BASE_CONTEXT)

up: build-bases ## Build base images, then bring the stack up (detached, built).
	$(COMPOSE_CMD) up --build -d

down: ## Stop the stack and remove containers/networks (keeps named volumes).
	$(COMPOSE_CMD) down

# --- Local host dev (NO docker) ----------------------------------------------
# The SPA "Dev login" needs BOTH processes up: the backend on :8000 AND the Vite
# dev server on :5173 (which proxies /api -> :8000). A "(500)" on dev-login means
# the proxy could not reach the backend, i.e. `dev-backend` is not running.
# Run these in two terminals, or `make dev` to run both together.
dev-backend: ## Run the FastAPI backend on :8000 (host; reads services/backend/.env).
	PYTHONPATH=src $(UV) run --directory services/backend \
		uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

dev-frontend: ## Run the Vite admin SPA on :5173 (proxies /api -> :8000).
	cd services/frontend && npm run dev

dev: ## Run backend (:8000) AND frontend (:5173) together; Ctrl-C stops both.
	@echo "==> backend :8000  +  frontend :5173  (Ctrl-C stops both)"
	@trap 'kill 0' EXIT INT TERM; \
		( PYTHONPATH=src $(UV) run --directory services/backend \
			uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload ) & \
		( cd services/frontend && npm run dev ) & \
		wait

# --- Quality gates -----------------------------------------------------------
# Each loop iterates the package dirs; `[ -d ... ]` keeps it green before a
# package exists, and `--directory` keeps each uv project standalone.
test: ## Run pytest across all Python libs and services.
	@for d in $(PY_PKG_DIRS); do \
		if [ -d "$$d" ]; then \
			echo "==> pytest $$d"; \
			$(UV) run --directory "$$d" pytest || exit $$?; \
		else \
			echo "==> skip (missing) $$d"; \
		fi; \
	done

test-db: ## Apply migrations + run the backend RLS isolation matrix (needs a reachable Postgres).
	@echo "==> alembic upgrade head (owner DSN; migration 0002 creates app_rw + RLS)"
	@DATABASE_URL="$(OWNER_DATABASE_URL)" \
		$(UV) run --directory services/backend alembic upgrade head
	@echo "==> pytest services/backend (LOL_DB_TESTS=1 -> RLS matrix RUNS)"
	@LOL_DB_TESTS=1 \
		OWNER_DATABASE_URL="$(OWNER_DATABASE_URL)" \
		APP_DATABASE_URL="$(APP_DATABASE_URL)" \
		DATABASE_URL="$(OWNER_DATABASE_URL)" \
		$(UV) run --directory services/backend pytest -q

lint: ## Lint all Python code with ruff (check only).
	$(UV) run ruff check .

format: ## Auto-format all Python code with ruff.
	$(UV) run ruff format .

typecheck: ## Type-check all Python packages with mypy.
	@for d in $(PY_PKG_DIRS); do \
		if [ -d "$$d/src" ]; then \
			echo "==> mypy $$d/src"; \
			$(UV) run mypy --config-file mypy.ini "$$d/src" || exit $$?; \
		else \
			echo "==> skip (no src) $$d"; \
		fi; \
	done

# --- Placeholders (wired up in later phases) ---------------------------------
gen-clients: ## (placeholder) Regenerate OpenAPI/TS clients + WS JSON-schema.
	@echo "[gen-clients] TODO: export OpenAPI spec + lol-realtime WS JSON-schema, regenerate lol-clients-ts."

fixtures: ## (placeholder) Validate lol-lcu parsers against pinned live fixtures.
	@echo "[fixtures] TODO: run lol-lcu fixture validation harness (eog-stats-block / eventdata parsers vs pinned live-client fixtures)."
