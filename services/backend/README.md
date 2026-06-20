# lol-backend

FastAPI backend for LeagueOfLeagues — source of truth for tournaments, accounts,
auth, autonomous match orchestration, and result corroboration (PLAN.md §3.1).

## Phase 0 status

Skeleton that builds, lints, type-checks, and runs. Feature routers
(`auth/ accounts/ tournaments/ orchestration/ results/ realtime/`) are stubs.

## Layout

```
src/app/
  main.py          FastAPI app: /healthz, /metrics, mounts stub routers
  config.py        pydantic-settings (DATABASE_URL, REDIS_URL, LOG_LEVEL, ENVIRONMENT, JWT_SECRET)
  logging.py       structured JSON logging (structlog)
  db.py            async SQLAlchemy engine/session + per-request RLS GUC dependency
  auth/ accounts/ tournaments/ orchestration/ results/ realtime/   stub routers
alembic/           async migrations (0001_baseline)
tests/             health/observability smoke tests
```

## Develop

```bash
uv sync --extra dev          # resolves path deps on lol-schemas / lol-core
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check src tests
uv run mypy src
uv run alembic upgrade head  # requires a reachable Postgres (DATABASE_URL)
```

## Endpoints

- `GET /healthz` -> `{"status": "ok"}` (liveness)
- `GET /metrics` -> Prometheus exposition
- `GET /api/v1/<module>/_ping` -> stub liveness per module
