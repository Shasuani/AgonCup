# CLAUDE.md

Operational guide for working in this repo. Read `PLAN.md` (the Hardened v3 plan)
for the full design; this file is the day-to-day reference.

## What this is

**LeagueOfLeagues** — a multi-tenant *League of Legends* tournament platform.
Up to 16 teams/tournament, many tournaments in parallel, run and reported through
**Discord**, with a desktop client that **autonomously runs each custom match via
the local League client (LCU)** — there is **no Riot Tournament API** (Match-V5
404s on customs; that path never existed). Results are corroborated across the 10
players' (forgeable) clients with admin override; identity is verified once via
**Account-V1** (PUUID is the canonical identity key).

## Monorepo layout

```
libs/                      # versioned, framework-light shared libraries (DRY)
  lol-schemas    # Pydantic DTOs + StrEnums — the ONE canonical Python type source
  lol-core       # domain logic: bracket engine (single+double elim), quorum/anomaly
                 #   adjudication, and the PURE lobby-orchestration state machine
  lol-riot       # Account-V1 client (Riot ID->PUUID) + token-bucket limiter
  lol-realtime   # WS envelope models (lobby commands/acks) + JSON-schema export
  lol-discord    # Discord embed/command helpers
  lol-lcu (TS)   # the single audited LCU/:2999 adapter (RE endpoints, write-gated)
  lol-clients-ts # generated TS API client (from backend OpenAPI)
services/
  backend   # FastAPI: auth/ accounts/ teams/ tournaments/ results/ orchestration/
            #   realtime/ lifecycle/  (+ alembic migrations, app/models, app/security)
  worker    # arq: lobby_watchdog (failover), account_verification_poll, corroboration
  bot       # discord.py AutoShardedBot (guilds intent only)
  frontend  # React + Vite admin SPA
  desktop   # Electron client (runs natively; consumes lol-lcu)
infra/      # docker base images (python-base/node-base), docker-compose, CI
docs/       # research-findings.md, compliance-checklist.md, privacy-policy.md, plan backup
```

Libraries are consumed via **path deps** (`[tool.uv.sources]` in each pyproject)
and baked into images in CI — one distribution model, no drift.

## Commands (Makefile at repo root)

- `make up` — build base images then `docker compose up` (Postgres, Redis, backend, worker).
- `make build-bases` — build `lol/python-base:dev` + `lol/node-base:dev` (must run before service images).
- `make test` / `make lint` / `make format` / `make typecheck` — across all Python packages.
- `make gen-clients` — regenerate `lol-clients-ts` from the backend OpenAPI (placeholder until wired).

**Running tests directly** (each package is standalone; `uv` provides Python 3.12):
```bash
cd services/backend && PYTHONPATH=src python -m pytest -q   # or `uv run pytest`
```
Tooling: **uv** (package manager), **pytest** (+ `pytest-asyncio`, `asyncio_mode=auto`),
**ruff** (lint + format, line-length 100), **mypy** (strict).

## Conventions

- Python **3.12**, `from __future__ import annotations`, full type hints, `src/` layout.
  Dist names hyphenated (`lol-core`), import names underscored (`lol_core`).
- **Enums are `StrEnum`** and live in `lol_schemas.enums` (the canonical home). ORM
  columns store enum **values** via `pg_enum(...)` (VARCHAR + CHECK, `native_enum=False`).
- SQLAlchemy **2.0 async** (`Mapped` / `mapped_column`). Structured logging via
  `app.logging.get_logger`. Pydantic **v2**.
- Keep `lol-core` / `lol-schemas` framework-light (no FastAPI/discord.py imports).
- Docstrings cite the relevant `PLAN.md §` section.

## Multi-tenant RLS — the load-bearing invariant (read before touching DB code)

Every guild-scoped table has Postgres **`FORCE ROW LEVEL SECURITY`** with a
`guild_isolation` policy keyed on `current_setting('app.current_guild', true)`.

- The request path connects as the **non-`BYPASSRLS` `app_rw` role** (via
  `APP_DATABASE_URL`) so the policy actually binds. **Migrations** use the owner
  DSN (`DATABASE_URL`); a superuser bypasses RLS, so the app must NOT use it.
- The tenant GUC is set per-transaction by **`app.auth.deps.get_tenant_session`**
  (and `require_tenant_role` for role-gated routes). **Every guild-scoped query
  must go through it.** Inserts read the acting guild back from the GUC
  (`SELECT current_setting('app.current_guild', true)`), never from request input.
- Unset GUC → policy matches nothing → **fail closed** (zero rows).
- Login/refresh read a user's own roles across guilds via a separate
  `self_role_read` policy keyed on `app.current_user` (set in `_roles_for`).
- The **same-guild invariant** is structural: children reference parents by a
  composite `(id, guild_id)` FK to a `UNIQUE(id, guild_id)` — a row physically
  cannot point at another guild's parent.

Migrations: `services/backend/alembic/versions/` (chain `0001 -> 0002 -> 0003`).
`target_metadata = app.models.Base.metadata` (autogenerate works). Use
**`sa.false()`** for boolean server defaults, never `func.false()` (compiles to a
non-existent `false()` function and breaks `alembic upgrade head`).

## Testing model

- **Unit tests run offline** — pure logic (bracket engine, orchestration state
  machine, adjudication) and FastAPI handlers with a fake/mock session. Mock all
  external HTTP; no live network.
- **Postgres integration tests** (`tests/test_rls_isolation.py`,
  `tests/test_integration_pg.py`) are **gated on `LOL_DB_TESTS=1`** and run in CI
  against a real Postgres (they exercise RLS, cross-guild role escalation,
  cascades — things a fake session cannot prove). They **skip locally** unless a
  DB + the `app_rw` role exist.

## Gotchas

- **No `docker compose` plugin is assumed locally** (only the Docker CLI). The
  live-DB tests therefore run in CI, not on a typical dev box. To run them
  locally: start Postgres, `alembic upgrade head`, then `LOL_DB_TESTS=1` + the
  OWNER/APP DSNs (see the test file headers).
- **lol-lcu is community-reverse-engineered/unsupported** and drifts per League
  patch — it is the single audited fix-point; every **write** (create lobby,
  invite, auto-accept, start) is gated behind opt-in + kill-switch + presence +
  pacing. No in-match gameplay automation, ever. **Korea is excluded.** The
  LCU-write ToS spike is the **beta gate** (`docs/compliance-checklist.md`).
- The desktop client **runs natively** (must reach `127.0.0.1` LCU/:2999); its
  Dockerfile is a build/packaging container only.
- Commits/PRs are configured to carry **no AI attribution** — do not add
  `Co-Authored-By` trailers.

## Status

Phases 0–2 done (~205 tests green): scaffold, data model + RLS, auth (Discord
OAuth2 + JWT + refresh + RBAC), Account-V1 verification, PII lifecycle, tournament/
team/results CRUD + bracket, and autonomous LCU orchestration (state machine +
backend + worker watchdog). See `PLAN.md §7` for the roadmap and the latest commit
body for deferred hardening follow-ups.
