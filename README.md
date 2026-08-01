# LeagueOfLeagues

A multi-tenant tournament platform for *League of Legends*. It supports up to
**16 teams per tournament**, **multiple tournaments running in parallel**, user
registration and status delivery through **Discord**, a **desktop client** that
**autonomously runs each custom match** via the local League client (**LCU**),
and an **admin web frontend**.

The Discord bot is multi-tenant: any guild can invite it and run its own
tournaments, optionally surfaced (with consent and approval) in the central
*League Of Leagues* server.

> Not endorsed by Riot Games. The desktop client drives the user's own League
> client through an unofficial, unsupported, reverse-engineered API.

---

## The v3 autonomous model (what makes this different)

Earlier designs leaned on the **Riot Tournament-V5 API** (providers, tournament
codes, result callbacks) as the single authoritative result path. **v3 removes
that entirely.** Match-V5 returns 404 for custom games, so that "authoritative
Riot record" never existed for the games we run.

Instead, v3 is **autonomous**:

- **Match execution lives on a player's machine.** A custom lobby can only be
  created by a logged-in League client, so one designated player's **desktop
  client** creates the lobby, invites by PUUID, auto-accepts, and starts the
  game over the local **LCU API**. Host loss triggers a **server-authoritative,
  epoch-fenced failover** state machine (promote-first, else re-create).
- **Result truth is client-side corroboration, not a Riot-signed record.** All
  10 participants' clients report their end-of-game stats
  (`eog-stats-block`) and live kill stream (`:2999`). A result is accepted only
  on a **both-teams-represented quorum** (>=6/10 identity-bound reports agreeing
  on the same `gameId` and winner, with at least one report from each roster),
  screened by an **anomaly pipeline**. **No source is authoritative** — every
  byte is forgeable — so **admin override is the final authority** on disputes.
- **Riot Web API shrinks to Account-V1 only** (Riot ID -> PUUID for one-time
  account linking). A personal/standard key suffices: **no Tournament API, no
  production-key review, no Match-V5.**
- **The desktop client is load-bearing for availability but advisory for
  integrity** — never authoritative for result truth, identity, or anti-smurf.

See [`PLAN.md`](./PLAN.md) for the full design (data model §2, LCU operation
§3.6, repo structure §4, libraries §5, docker §6, phased roadmap §7).

---

## Quickstart: `make up`

**Prerequisites:** Docker (with Compose v2) and GNU Make. `uv`, Python 3.12,
ruff, mypy, and pytest are provided inside the container images, so you do not
need them on the host to run the stack.

```bash
# 1. Create your local env file (secrets stay empty for local dev).
cp .env.example .env

# 2. Build the shared base images, then bring the stack up.
make up
```

`make up` first builds the shared base images (`lol/python-base:dev`,
`lol/node-base:dev`), then runs `docker compose up --build -d` against
`infra/docker-compose.yml`, starting **postgres**, **redis**, **backend**, and
**worker**.

Once it is healthy, the backend exposes:

- `GET /healthz` — liveness probe
- `GET /metrics` — Prometheus-style metrics

Tear it down with:

```bash
make down   # stops containers, keeps named volumes (your dev DB)
```

### Common developer targets

| Command            | What it does                                                      |
|--------------------|------------------------------------------------------------------|
| `make up`          | Build base images, then `docker compose up --build -d`.          |
| `make down`        | Stop the stack and remove containers/networks.                   |
| `make build-bases` | Build `python-base` + `node-base` images only.                   |
| `make test`        | Run `pytest` across every Python lib and service.                |
| `make lint`        | `ruff check` the repo.                                           |
| `make format`      | `ruff format` the repo.                                          |
| `make typecheck`   | `mypy` each package's `src/` tree.                               |
| `make gen-clients` | (placeholder) Regenerate OpenAPI/TS clients + WS JSON-schema.    |
| `make fixtures`    | (placeholder) Validate `lol-lcu` parsers against live fixtures.  |

Use `make -n <target>` to dry-run any target, and `make help` to list them.

---

## Run & Debug in VS Code (host dev)

The app tiers run **on the host** (breakpoints, hot reload, native LCU access);
only **Postgres + Redis** run in Docker. Everything is wired in
[`.vscode/launch.json`](./.vscode/launch.json) +
[`.vscode/tasks.json`](./.vscode/tasks.json).

### First time (three tasks, `Ctrl+Shift+P` → *Tasks: Run Task*)

| # | Task | What it does |
|---|------|--------------|
| 1 | `deps: all` | `uv sync` for backend/worker/bot + `npm install` for frontend/desktop + builds `libs/lol-lcu`. |
| 2 | `infra: up (postgres + redis)` | Starts `lol-postgres-1` / `lol-redis-1` via `infra/docker-compose.yml`. |
| 3 | `db: migrate (alembic upgrade head)` | Applies migrations: schema + FORCE-RLS policies + the `app_rw` role. |

### Then: F5

| Launch config | Runs |
|---------------|------|
| `Backend: FastAPI :8000` | uvicorn under debugpy — reliable breakpoints. |
| `Backend: FastAPI :8000 (--reload)` | Same + autoreload (the reloader forks, so breakpoints get flaky). |
| `Frontend: Vite dev :5173` | Vite dev server; opens the SPA in your browser when ready. |
| `Desktop: Electron (main)` | Builds `dist/` + `dist-renderer/`, then launches Electron. |
| `Desktop: attach renderer (:9223)` | Attach to the renderer to breakpoint in `renderer/*.tsx`. |
| `Worker: arq` / `Bot: Discord` | The background tiers (the bot needs a real `DISCORD_BOT_TOKEN`). |
| `Dev: simulate tournament` | Plays a whole tournament with fake players (dev-only). |

Compounds (top of the Run & Debug dropdown): **▶ Dev: Backend + Frontend**,
**▶ Dev: Backend + Desktop client**, **▶ Dev: Backend + Frontend + Desktop**,
**▶ Dev: Everything**.

Log into the SPA with the **Dev login** button (`POST /auth/dev-login`,
development only) — no Discord OAuth app needed.

### Ports

The dev DSNs use **non-default ports** because the usual ones are taken by other
stacks on the same Docker daemon. They must match `services/<svc>/.env`.

| Port | Used by | Note |
|------|---------|------|
| `5442` | this project's Postgres | `:5432` is taken (`ewhms-postgres`), `:5451` by `miniwms`. |
| `6389` | this project's Redis | `:6379` system redis, `:6380` `ewhms`, `:6398` `miniwms`. |
| `8000` | backend (host) | Also the Vite proxy target and the desktop's `BACKEND_URL`. |
| `5173` | frontend (host) | Vite falls forward to `5174…` if busy. |
| `9223` | Electron remote debugging | Renderer attach. |

### Env files — two of them, on purpose

`services/<svc>/.env` carries **localhost** DSNs (host dev, git-ignored, created
for you with dev-only secrets). The repo-root `.env` carries **docker-compose
hostnames** and is for containers only. VS Code's Python extension auto-loads
the root one by default, which would send host runs at `postgres:5432` and fail
with `socket.gaierror` — [`.vscode/settings.json`](./.vscode/settings.json)
repins `python.envFile`, and every launch config sets its own `envFile`.

Fill in `RIOT_API_KEY` (required for account verification) and the `DISCORD_*`
values when you need those flows.

### Notes

- **Desktop + LCU under WSL:** the client must reach the League client on
  `127.0.0.1` — inside WSL that is WSL's loopback, not Windows'. Pairing,
  tournaments and teams work anywhere; for real LCU work run the desktop
  natively on the machine running League (or enable WSL2 mirrored networking).
- `libs/lol-lcu` ships no `dist/`, so `build: lcu lib` must run before the
  desktop build (`deps: all` and `build: desktop` both handle it).

---

## Repository map

```
LeagueOfLeagues/
├── libs/
│   ├── lol-core/        # domain models, bracket engine, quorum/anomaly adjudication
│   ├── lol-schemas/     # Pydantic DTOs — the one canonical Python type source
│   ├── lol-riot/        # thin Account-V1 client (+ light token-bucket limiter)
│   ├── lol-lcu/         # (TS) single audited LCU/:2999 adapter
│   ├── lol-realtime/    # WS envelope models (+ exported JSON-schema), command/ACK
│   ├── lol-discord/     # shared Discord helpers
│   └── lol-clients-ts/  # GENERATED TS client (from OpenAPI)
├── services/
│   ├── backend/         # FastAPI: auth/ accounts/ tournaments/ orchestration/ results/ realtime/
│   ├── worker/          # arq: verification poll, lobby watchdog/state machine, corroboration
│   ├── bot/             # discord.py AutoShardedBot (multi-tenant)
│   ├── desktop/         # Electron orchestrator + reporter (consumes lol-lcu)
│   └── frontend/        # React admin SPA (+ dispute/override queue)
├── infra/               # docker-compose, shared base images, CI
├── docs/                # research findings, privacy policy, compliance
├── Makefile             # developer entrypoints (see Quickstart)
└── README.md
```

### Conventions

- **Python 3.12.** Each Python package is **standalone** with its own
  `pyproject.toml` (PEP 621, hatchling) using a `src/` layout. Distribution
  names are hyphenated (`lol-core`); import names are underscored (`lol_core`).
  Inter-package deps use **uv path sources** — there is no central workspace.
- **Package/runtime manager: `uv`.** Lint/format: **ruff**. Types: **mypy**.
  Tests: **pytest**. (Config in `ruff.toml`, `mypy.ini`.)
- **Stack:** Postgres 16, Redis 7, FastAPI on uvicorn (backend), arq (worker),
  discord.py (bot). Desktop / `lol-lcu` / frontend are TypeScript on Node 22.
- **Docker-first (server-side).** Service Dockerfiles are multi-stage and build
  `FROM` the local base images. The desktop client is built in a container but
  **runs natively** (it must reach the local LCU lockfile and `127.0.0.1:2999`).
- **Config via env (12-factor).** Every variable referenced by Compose is
  declared in [`.env.example`](./.env.example). Shared vars: `DATABASE_URL`,
  `REDIS_URL`, `LOG_LEVEL`, `ENVIRONMENT`, plus `DISCORD_CLIENT_ID` /
  `DISCORD_CLIENT_SECRET` / `DISCORD_BOT_TOKEN`, `RIOT_API_KEY`, `JWT_SECRET`.
- **Observability from day 0:** structured JSON logging, `GET /healthz`,
  `GET /metrics`.
