# Development guide

Detailed host-development notes for AgonCup. For the overview, see the [main README](../README.md).

## Run & Debug in VS Code (host dev)

The app tiers run **on the host** (breakpoints, hot reload, native LCU access);
only **Postgres + Redis** run in Docker. Everything is wired in
[`.vscode/launch.json`](../.vscode/launch.json) +
[`.vscode/tasks.json`](../.vscode/tasks.json).

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
with `socket.gaierror` — [`.vscode/settings.json`](../.vscode/settings.json)
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

## Conventions

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
  declared in [`.env.example`](../.env.example). Shared vars: `DATABASE_URL`,
  `REDIS_URL`, `LOG_LEVEL`, `ENVIRONMENT`, plus `DISCORD_CLIENT_ID` /
  `DISCORD_CLIENT_SECRET` / `DISCORD_BOT_TOKEN`, `RIOT_API_KEY`, `JWT_SECRET`.
- **Observability from day 0:** structured JSON logging, `GET /healthz`,
  `GET /metrics`.
