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
	dev dev-backend dev-frontend dev-bot dev-desktop desktop-win desktop-mac sim-join infra-up infra-down db-migrate

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

# --- Datastores only (for HOST dev: app tiers run on the host, DB in docker) --
# Non-default host ports: 5432/6379 are usually taken by other stacks on the same
# daemon. These MUST match the DSNs in services/<svc>/.env. Compose pins
# `name: lol`, so the containers are lol-postgres-1 / lol-redis-1.
DEV_POSTGRES_PORT ?= 5442
DEV_REDIS_PORT    ?= 6389

infra-up: ## Start ONLY postgres + redis (host dev; ports 5442/6389).
	POSTGRES_PORT=$(DEV_POSTGRES_PORT) REDIS_PORT=$(DEV_REDIS_PORT) \
		$(COMPOSE_CMD) up -d postgres redis

infra-down: ## Stop the postgres + redis dev containers.
	$(COMPOSE_CMD) stop postgres redis

db-migrate: ## Apply migrations to the dev DB (works on an empty database too).
	cd services/backend && set -a && . ./.env && set +a && \
		PYTHONPATH=src $(UV) run alembic upgrade head

# --- Local host dev (NO docker) ----------------------------------------------
# The SPA "Dev login" needs BOTH processes up: the backend on :8000 AND the Vite
# dev server on :5173 (which proxies /api -> :8000). A "(500)" on dev-login means
# the proxy could not reach the backend, i.e. `dev-backend` is not running.
# Run these in two terminals, or `make dev` to run both together.
dev-backend: ## Run the FastAPI backend on :8000 (host; sources services/backend/.env).
	cd services/backend && set -a && . ./.env && set +a && \
		PYTHONPATH=src $(UV) run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

dev-frontend: ## Run the Vite admin SPA on :5173 (proxies /api -> :8000).
	cd services/frontend && npm run dev

dev-bot: ## Run the Discord bot (host; sources services/bot/.env). Needs the backend up.
	cd services/bot && set -a && . ./.env && set +a && \
		PYTHONPATH=src $(UV) run python -m app.main

dev-desktop: ## Build + launch the Electron desktop (LCU) client natively (must reach 127.0.0.1 LCU).
	cd services/desktop && npm run build && npm start

sim-join: ## DEV: simulate a 2nd player requesting to join a team. `make sim-join CODE=ABCD2345 NAME="Faker"`
	cd services/backend && set -a && . ./.env && set +a && \
		PYTHONPATH=src $(UV) run python scripts/sim_joiner.py "$(CODE)" "$(NAME)"

dev: ## Run backend (:8000) AND frontend (:5173) together; Ctrl-C stops both.
	@echo "==> backend :8000  +  frontend :5173  (Ctrl-C stops both)"
	@trap 'kill 0' EXIT INT TERM; \
		( cd services/backend && set -a && . ./.env && set +a && \
			PYTHONPATH=src $(UV) run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload ) & \
		( cd services/frontend && npm run dev ) & \
		wait

# --- Quality gates -----------------------------------------------------------
# Each loop iterates the package dirs; `[ -d ... ]` keeps it green before a
# package exists, and `--directory` keeps each uv project standalone.
#
# The `uv sync --all-extras --dev` before each run is LOAD-BEARING, not tidiness.
# pytest/mypy live in each package's `[project.optional-dependencies] dev`, which
# a plain `uv run` does NOT install. When the tool is missing from the project
# venv, `uv run` does not fail — it falls through to whatever is on PATH. On a
# machine with conda/micromamba that silently ran lol-realtime's suite under a
# system Python 3.9 with no pydantic (collection error), and `uv run mypy` died
# with "Failed to spawn". Syncing first is what CI does, and it makes the local
# run reproduce CI instead of the developer's PATH.
test: ## Run pytest across all Python libs and services.
	@for d in $(PY_PKG_DIRS); do \
		if [ -d "$$d" ]; then \
			echo "==> pytest $$d"; \
			$(UV) sync --directory "$$d" --all-extras --dev --quiet || exit $$?; \
			$(UV) run --directory "$$d" --no-sync pytest || exit $$?; \
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

typecheck: ## Type-check all Python packages with mypy (each package's own config).
# NB: no --config-file. Each package's [tool.mypy] loads the pydantic plugin and
# sets mypy_path=src; forcing the repo-root mypy.ini (which has neither) produces
# 41 phantom "Class cannot subclass Base" errors. Run inside the package so its
# config and its synced venv are the ones in effect.
	@for d in $(PY_PKG_DIRS); do \
		if [ -d "$$d/src" ]; then \
			echo "==> mypy $$d/src"; \
			$(UV) sync --directory "$$d" --all-extras --dev --quiet || exit $$?; \
			$(UV) run --directory "$$d" --no-sync mypy src || exit $$?; \
		else \
			echo "==> skip (no src) $$d"; \
		fi; \
	done

# --- Desktop client, built from WSL for Windows ------------------------------
# The client MUST run natively (it reaches the League client on 127.0.0.1), so a
# Linux build is useless for testing. This stages a runnable win32-x64 build on a
# Windows-visible path, pointed at the devel server by default. Override with
# BACKEND_URL= / OUT_DIR=. See the header of the script for why not electron-builder.
desktop-win: ## Build the desktop client for Windows into $$OUT_DIR (default J:\loltournament).
	@bash services/desktop/scripts/build-win.sh

# Build + install the macOS app so a Finder/Spotlight launch always runs the
# latest build against the right backend (a Finder launch inherits no shell env,
# so both urls are baked into the installed bundle). Override with
# BACKEND_URL= / APP_DEST=.
desktop-mac: ## Build + install the desktop client to $$APP_DEST (default /Applications/LeagueOfLeagues.app).
	@bash services/desktop/scripts/install-macos.sh

# --- Placeholders (wired up in later phases) ---------------------------------
gen-clients: ## (placeholder) Regenerate OpenAPI/TS clients + WS JSON-schema.
	@echo "[gen-clients] TODO: export OpenAPI spec + lol-realtime WS JSON-schema, regenerate lol-clients-ts."

fixtures: ## (placeholder) Validate lol-lcu parsers against pinned live fixtures.
	@echo "[fixtures] TODO: run lol-lcu fixture validation harness (eog-stats-block / eventdata parsers vs pinned live-client fixtures)."
