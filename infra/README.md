# infra/ — local stack, shared base images, CI

This directory owns the Docker-first local development stack and the shared base
images for LeagueOfLeagues. It is Phase-0 scaffolding: it must actually build and
run, but the services it composes are still skeletons.

## Layout

```
infra/
├── images/
│   ├── python-base/Dockerfile   # lol/python-base:dev  (3.12-slim + uv + non-root "app")
│   └── node-base/Dockerfile     # lol/node-base:dev    (node:22-slim, non-root "node")
├── docker-compose.yml           # postgres, redis, backend, worker
├── docker-compose.override.yml  # dev source mounts + hot reload
└── README.md                    # this file
```

The CI workflow lives at the repo root: `.github/workflows/ci.yml`.

## Build order — BASE IMAGES FIRST (important)

The `backend` and `worker` service Dockerfiles are **multi-stage** and start
`FROM lol/python-base:dev`. That tag exists **only locally** — it is never pushed
to a registry. So you must build the shared base images before building or
running any service:

```bash
# 1. Build the shared base images (do this first, once, and after toolchain bumps)
make build-bases
#   which is equivalent to:
#     docker build -t lol/python-base:dev infra/images/python-base
#     docker build -t lol/node-base:dev   infra/images/node-base

# 2. Then build + run the stack
docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.yml up --build
```

If you skip step 1 you'll get a `pull access denied for lol/python-base` (or
`lol/node-base`) error, because Docker tries to fetch the non-existent base image
from a registry.

> The `make build-bases` target is owned by the root `Makefile` (separate slice).
> Until it exists, run the two `docker build` commands above by hand.

## Configuration (12-factor)

All configuration is environment-driven. Every variable referenced by the compose
files also exists in `.env.example` at the repo root. Copy it and edit:

```bash
cp .env.example .env
```

The shared variable set is:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres DSN used by backend + worker (async driver). |
| `REDIS_URL` | Redis URL (cache, WS pub/sub, arq broker). |
| `LOG_LEVEL` | Structured-log level (`DEBUG`/`INFO`/...). |
| `ENVIRONMENT` | `development` / `test` / `production`. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Postgres initdb + healthcheck. |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` / `DISCORD_BOT_TOKEN` | Discord OAuth + bot (placeholders). |
| `RIOT_API_KEY` | Account-V1 personal key (placeholder). |
| `JWT_SECRET` | Backend token signing (placeholder). |

The compose files set sane defaults (`${VAR:-default}`) so `docker compose config`
and a bare `up` work even before `.env` is filled in. `env_file: ../.env` is marked
`required: false`, so a missing `.env` does not break parsing.

## Services

| Service | Image | Notes |
|---------|-------|-------|
| `postgres` | `postgres:16` | Named volume `postgres_data`; `pg_isready` healthcheck. |
| `redis` | `redis:7` | `--appendonly yes` (AOF); `redis-cli ping` healthcheck. |
| `backend` | `lol/backend:dev` (built from `services/backend`) | FastAPI/uvicorn; `GET /healthz` healthcheck; port `8000`. `depends_on` postgres+redis healthy. |
| `worker` | `lol/worker:dev` (built from `services/worker`) | arq; `depends_on` postgres+redis healthy. |
| `bot`, `frontend` | — | Phase-0 placeholders, commented out in `docker-compose.yml`. |

The `backend`/`worker` build **context is the repo root** (`..`) so their images
can `COPY` the shared `libs/`. Their Dockerfiles are owned by the backend/worker
slices.

## Dev hot reload

`docker-compose.override.yml` (auto-merged when both `-f` files are passed, or when
you run compose from inside `infra/`) mounts the whole repo at `/workspace` and runs:

- backend: `uvicorn ... --reload` watching `services/backend/src` and `libs`
- worker: `arq ... --watch services/worker/src`

A named volume shadows each container's `/app/.venv` so the host bind-mount does
not clobber the image's installed dependencies.

## Validate the compose file

```bash
docker compose -f infra/docker-compose.yml config
```

This parses and renders the fully-resolved config (no containers started). Use it
in review to confirm the YAML and keys are valid.

## CI (`.github/workflows/ci.yml`)

| Job | What it does |
|-----|--------------|
| `lint` | `ruff check` + `ruff format --check` across the tree. |
| `typecheck` | `mypy src` per standalone Python package. |
| `test` | `pytest` per package, with Postgres 16 + Redis 7 service containers. |
| `cve-scan` | Trivy `fs` scan (always); image scan is a documented, disabled placeholder. |
| `lol-lcu-fixtures` | Placeholder for the LCU fixture-validation harness (`libs/lol-lcu`). |

Jobs are Phase-0 tolerant: packages that don't exist yet are skipped, not failed,
so CI stays green as slices land incrementally.
