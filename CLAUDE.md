# CLAUDE.md

Operational guide for working in this repo. Read `PLAN.md` (the Hardened v3 plan)
for the full design; this file is the day-to-day reference.

## What this is

**AgonCup** (named *LeagueOfLeagues* until 2026-09-21 — the repo folder, the
`lol-*` / `lol_*` package names, the `@lol/desktop` npm name and the desktop
`appId` keep the old spelling on purpose; nothing a player sees does) — a
multi-tenant *League of Legends* tournament platform.
Up to 16 teams/tournament, many tournaments in parallel, run and reported through
**Discord**, with a desktop client that **autonomously runs each custom match via
the local League client (LCU)** — there is **no Riot Tournament API** (Match-V5
404s on customs; that path never existed). Results are corroborated across the 10
players' (forgeable) clients with admin override; identity is verified via a
**one-time code + desktop/LCU flow with server-side Account-V1 resolution**
(`/register` mints a code; the desktop reads the local LCU `current-summoner`
**Riot ID** + `region` and submits `{code, game_name, tag_line, region}`; the
backend resolves the **canonical PUUID** via Account-V1 `by-riot-id` — the
double-verify — dedupes the `RiotAccount` on that PUUID, fetches a best-effort
Solo/Duo rank via League-V4, and DMs the result in Discord). PUUID is the
canonical identity key; **Account-V1 (Riot key) is REQUIRED** (not the LCU's
36-char UUID, which is stored only as advisory `lcu_puuid`).

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

**Host dev (no Docker; Postgres/Redis on localhost)** — each service reads its own
`services/<svc>/.env` (localhost DSNs); the targets `source` it so vars read from
`os.environ` (e.g. `LIFECYCLE_SERVICE_TOKEN`) are present too:
- `make dev` — backend (`:8000`) + frontend (`:5173`) together (Ctrl-C stops both).
- `make dev-backend` / `make dev-frontend` / `make dev-bot` — run one service on the host.
  The Discord bot needs the backend up (it calls `/api/v1/integrations/discord/*`).

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

Migrations: `services/backend/alembic/versions/` — a **single squashed
`0001_baseline`** holding the whole v3 schema, the `app_rw` role, the per-guild
`guild_isolation` policies and `self_role_read`. `alembic upgrade head` works on
an empty database and an existing one alike.

**Never derive DDL in a migration from `app.models`.** The old `0001..0012` chain
did (`0002` created tables from `Base.metadata.sorted_tables`), which made it
unreplayable: on a fresh database 0002 already produced the current schema and
`0004..0012` died on `DuplicateColumnError`. Migrations are immutable historical
records; models keep moving. Write static `op.create_table(...)` / `op.add_column(...)`,
and keep any table list (e.g. the guild-isolated set) hardcoded in the migration.
CI runs `alembic check` after migrating to catch model/migration drift.

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
- **Two `.env`s, on purpose — host vs Docker.** The **repo-root `.env`** uses
  **docker-compose hostnames** (`postgres`/`redis`, incl.
  `APP_DATABASE_URL=...@postgres`); each **`services/<svc>/.env`** uses
  **localhost** for host runs. VS Code's Python extension auto-loads the repo-root
  `.env` (`python.envFile` default), so a host-run debug config MUST set
  `"envFile": "${workspaceFolder}/services/<svc>/.env"` (and leave
  `APP_DATABASE_URL` empty) — otherwise the request path connects to `postgres:5432`
  and every DB hit dies with `socket.gaierror`. `make dev-*` avoid this by sourcing
  the per-service `.env`.
- **SPA "dev-login failed (500)"** ≈ the backend isn't listening on `:8000`: the
  Vite `/api` proxy manufactures the 500 (now a clearer 502 `backend_unreachable`).
  Start the backend (`make dev-backend`); it is **not** a backend bug.
- **Discord bot**: needs a real `DISCORD_BOT_TOKEN` + a shared `BOT_SERVICE_TOKEN`
  (and `LIFECYCLE_SERVICE_TOKEN`) matching the backend's `.env`. Guilds onboard via
  the bot's `on_guild_join` **and** an `on_ready` reconcile sweep (servers added
  while the bot was offline), both idempotent. Run with `make dev-bot`.
- **lol-lcu is community-reverse-engineered/unsupported** and drifts per League
  patch — it is the single audited fix-point; every **write** (create lobby,
  invite, auto-accept, start) is gated behind opt-in + kill-switch + presence +
  pacing. No in-match gameplay automation, ever. **Korea is excluded.** The
  LCU-write ToS spike is the **beta gate** (`docs/compliance-checklist.md`).
- The desktop client **runs natively** (must reach `127.0.0.1` LCU/:2999); its
  Dockerfile is a build/packaging container only.
- **Verification is code + desktop/LCU + server-side Account-V1**, not the old
  icon-nonce: `/register` mints a one-time code (15-min TTL, single-use,
  superseded on re-register); the desktop reads the local LCU `current-summoner`
  **Riot ID** (`gameName`/`tagLine`) + `region` (from `/riotclient/region-locale`;
  the LCU `puuid` is a non-canonical 36-char UUID and `session.platformId` is
  `null`, so neither is trusted) READ-ONLY and POSTs
  `{code, game_name, tag_line, region}` (+ advisory `lcu_puuid`/`summoner_id`) to
  the unauthenticated, rate-limited `POST /api/v1/accounts/verify-desktop`. The
  **code is the credential**; the backend resolves the **canonical 78-char PUUID**
  via Account-V1 `by-riot-id` (the **double-verify** — a 404 rejects), **dedupes
  the `RiotAccount` keyed on that canonical PUUID** (update-in-place, never a
  second row; 409 if it belongs to another user), fetches a **best-effort**
  League-V4 Solo/Duo rank snapshot (failure never fails the link), and publishes
  a `registration_verified` notification so the bot **DMs the user** (fallback:
  guild notify channel). Identity stays **advisory** (the resolve proves the Riot
  ID exists, not ownership — corroboration + admin override is the backstop).
  **Account-V1/Riot key is REQUIRED.** `registration_verified` is the **only**
  notification kind carrying `discord_user_id` (DM-routed; exempt from the
  central-feed `_FORBIDDEN_CENTRAL_KEYS` guard).
- **Desktop installers never touch git or CI** (owner decision 2026-09-09).
  `infra/publish-desktop.sh` on a Mac = `npm run dist:all` + SFTP upload as the
  chrooted `lolupload` account (password in the macOS Keychain, never in the
  repo) of five files whose names carry no version and simply overwrite:
  `AgonCup-Setup.exe`, `AgonCup.dmg`, `AgonCup.zip`,
  `latest.yml`, `latest-mac.yml`. Installers first, feeds last, each via a
  temporary name + rename. Caddy serves the folder at `/downloads/`. See
  `docs/gitlab-cicd.md §9`.
- Commits/PRs are configured to carry **no AI attribution** — do not add
  `Co-Authored-By` trailers.

## Status

All phases (0–7) built out (~369 test files green): scaffold, data model + RLS,
auth (Discord OAuth2 + JWT + refresh + RBAC), code + desktop/LCU account
verification (server-side Account-V1 PUUID resolution + dedupe + best-effort
League-V4 rank snapshot + Discord DM), PII lifecycle, tournament/team/results
CRUD + bracket (single + double elim, 1v1 ARAM, best-of-N series), autonomous LCU
orchestration (state machine + backend + worker watchdog), Discord bot +
federation/central-feed, desktop Electron client, admin frontend, and result
corroboration/dispute pipeline.

The project is now in a **client-first refactor** phase: user actions are moving
bot → desktop client (token-based tournament/team join codes, global presence
bell, self-service unregister), leaving the bot slimmed to admin + onboarding.
See `PLAN.md §7` for the original roadmap.
