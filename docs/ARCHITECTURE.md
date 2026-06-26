# LeagueOfLeagues — Architecture & Component Documentation

> Scope: this document covers **project infrastructure** + the **backend** and
> **desktop client** components in depth. Worker, bot, and frontend are described
> only where they touch those two. The authoritative design is `PLAN.md`
> (Hardened v3); day-to-day rules live in `CLAUDE.md`. This file is the
> component-level map.

---

## 1. What the platform is

A multi-tenant *League of Legends* tournament platform. Up to 16 teams per
tournament, many tournaments in parallel, run and reported through **Discord**.
There is **no Riot Tournament API** for custom games (Match-V5 404s on customs),
so each match is executed **autonomously by a desktop client driving the local
League client (LCU)**, and results are **corroborated across the 10 players'
forgeable clients** with admin override. PUUID (resolved server-side via Riot
Account-V1) is the canonical identity key.

**Split trust model (PLAN.md §0):** the desktop client is *load-bearing for match
execution* but *advisory for result truth*. No single client is ever authoritative.

---

## 2. Infrastructure / monorepo layout

```
libs/                  # versioned, framework-light shared libraries (path deps)
  lol-schemas    # Pydantic DTOs + StrEnums — the ONE canonical Python type source
  lol-core       # pure domain logic: bracket engine, orchestration state machine,
                 #   quorum/anomaly adjudication (no DB, no IO, no framework)
  lol-riot       # Account-V1 client (Riot ID -> PUUID) + token-bucket limiter
  lol-realtime   # WS envelope Pydantic models + JSON-schema export
  lol-discord    # Discord embed/command helpers
  lol-lcu  (TS)  # the single audited LCU/:2999 adapter (write-gated)
  lol-clients-ts # generated TS API client (from backend OpenAPI)
services/
  backend   # FastAPI app  (this document, §3)
  worker    # arq jobs: lobby_watchdog, account_verification_poll, score_corroboration
  bot       # discord.py AutoShardedBot
  frontend  # React + Vite admin SPA
  desktop   # Electron client (this document, §4)
infra/      # docker base images, docker-compose, CI
docs/       # this file + research-findings, compliance-checklist, privacy-policy
```

**Dependency model.** Shared libs are consumed as **path deps**
(`[tool.uv.sources]` in each Python `pyproject`; `file:../../libs/lol-lcu` for TS)
and baked into images in CI — one distribution model, no drift.

**Tooling.** Python 3.12, **uv** (package manager), **pytest** (+`pytest-asyncio`,
`asyncio_mode=auto`), **ruff** (lint+format, line-length 100), **mypy** (strict).
TS desktop/lcu use `tsc` + `node --test`.

**Two `.env`s on purpose.** Repo-root `.env` uses docker-compose hostnames
(`postgres`/`redis`); each `services/<svc>/.env` uses `localhost` for host runs.
See `CLAUDE.md` "Gotchas".

**Run targets (Makefile).** `make up` (docker), `make dev` (backend+frontend on
host), `make dev-backend` / `make dev-frontend` / `make dev-bot`,
`make test` / `make lint` / `make typecheck`.

---

## 3. Backend (`services/backend`)

### 3.1 Stack

- **FastAPI** app (`app/main.py`); all routers mounted under `/api/v1`.
- **SQLAlchemy 2.0 async** (`Mapped` / `mapped_column`), **asyncpg** driver.
- **Pydantic v2** (DTOs imported from `lol-schemas`; never redefined locally).
- **Alembic** migrations (`alembic/versions/`).
- **Redis** for the realtime command pub/sub + arq job queue.
- Structured logging via `app.logging.get_logger`.

### 3.2 Module map (`app/`)

| Package | Responsibility |
|---|---|
| `main.py` | App factory; mounts all routers under `/api/v1`. |
| `config.py` | Pydantic-settings; env-driven (`DATABASE_URL`, `APP_DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, service tokens). |
| `db.py` | Async engine/session factories; the `app_rw` (non-BYPASSRLS) request-path role wiring. |
| `models/` | `base.py` (Base, `GuildScoped`, mixins), `tables.py` (all ORM tables), `__init__.py` (exports). |
| `security/` | `crypto.py` — token hashing / secret helpers. |
| `auth/` | Discord OAuth2 login, JWT access tokens, refresh-token rotation, RBAC deps (`deps.py`: `get_tenant_session`, `require_tenant_role`, `Principal`). |
| `accounts/` | Riot account verification: `/register` code mint + `verify-desktop` (server-side Account-V1 PUUID resolve, dedupe, League-V4 rank snapshot). |
| `teams/` | Team CRUD + membership (captain/admin gated). |
| `tournaments/` | Tournament CRUD, registration, bracket seeding/generation. |
| `orchestration/` | Backend half of the autonomous-lobby state machine (`service.py`); REST control surface (`router.py`); publishes commands to Redis. |
| `realtime/` | WebSocket hub (`hub.py`) — auth, subscription authz, command relay, inbound ack/heartbeat/report routing; `redis_client.py`; thin `router.py`. |
| `results/` | `record_winner` + bracket advancement (`service.py`); admin-override winner endpoint (`router.py`). |
| `lifecycle/` | PII lifecycle (guild purge on bot removal, data export/erasure). |
| `notifications/` | Backend→bot notification fan-out (Redis); `registration_verified` DM routing. |
| `integrations/discord/` | Discord bridge (bot↔backend service-token endpoints). |
| `federation/` | Cross-guild / central-feed visibility surface. |
| `guilds/` | Guild settings + onboarding helpers. |

### 3.3 Multi-tenant RLS — the load-bearing invariant

Every guild-scoped table has Postgres **`FORCE ROW LEVEL SECURITY`** with a
`guild_isolation` policy keyed on `current_setting('app.current_guild', true)`.

- Request path connects as the **non-`BYPASSRLS` `app_rw`** role (via
  `APP_DATABASE_URL`) so the policy binds. **Migrations** use the owner DSN
  (`DATABASE_URL`); the app must NOT use the superuser.
- The tenant GUC is set per-transaction by `app.auth.deps.get_tenant_session`
  (and `require_tenant_role`). **Every guild-scoped query goes through it.**
- Unset GUC → policy matches nothing → **fail closed** (zero rows).
- **Same-guild invariant is structural:** children reference parents by a
  composite `(id, guild_id)` FK to a `UNIQUE(id, guild_id)` — a row physically
  cannot point at another guild's parent.
- Inserts read the acting guild back from the GUC, never from request input.

### 3.4 Data model (`models/tables.py`)

| Table | Scope | Purpose |
|---|---|---|
| `guilds` | global | Tenant (Discord guild) root. |
| `users` | global | Discord users. |
| `riot_accounts` | global | Canonical PUUID identity; `lcu_puuid` advisory; Solo/Duo rank snapshot. |
| `refresh_tokens`, `oauth_credentials` | global | Auth/session material. |
| `user_guild_roles` | guild | RBAC grants per (user, guild). |
| `teams`, `team_members` | guild | Rosters. |
| `tournaments`, `registrations` | guild | Tournament + team entries. |
| `matches` | guild | Bracket nodes (team_a/team_b, round, slot, status, winner). |
| `match_lobbies` | guild | Autonomous-lobby state (phase, host_epoch, host_puuid, heartbeat). |
| `client_sessions` | guild | Per-participant desktop session / match check-in roster. |
| `result_reports` | guild | The ≤10 forgeable per-reporter eog reports (corroboration input). |
| `match_results` | guild | Adjudicated/decided result (provisional/confirmed/disputed). |
| `result_nonces` | guild | Single-use freshness nonces for reports. |
| `central_feed_items`, `player_central_visibility` | global | Cross-guild feed + opt-in visibility. |
| `verification_attempts` | global | Account-V1 verification attempt log. |
| `audit_log` | global | Append-only high-attention events (e.g. lobby failover). |

**Migrations:** chain `0001 → 0002 → 0003 → 0004 → 0005`
(`0004` desktop-code columns, `0005` `riot_accounts.lcu_puuid` + rank snapshot).
Use `sa.false()` for boolean server defaults, never `func.false()`.

### 3.5 API surface (mounted under `/api/v1`)

| Prefix | Notable routes |
|---|---|
| `/auth` | `GET /login`, `GET /callback`, `POST /refresh`, `POST /logout`, `POST /register` (mint desktop code). |
| `/accounts` | `POST /verify-desktop` (unauth, rate-limited; Account-V1 resolve), admin roster reads. |
| `/teams` | `POST ""`, `GET ""`, `GET /{team_id}`, members add/remove, delete. |
| `/tournaments` | `POST ""`, `GET ""`, `GET /{tournament_id}`, register / seed / generate-bracket. |
| `/orchestration` | Lobby-lifecycle control endpoints (start, designate host, abort, …). |
| `/results` | `POST /matches/{match_id}/winner` (admin-override / adjudicated winner record). |
| `/realtime` | WebSocket endpoint (see §3.6) + `_ping`. |
| `/lifecycle` | Guild purge / data export / erasure (service-token). |
| `/integrations/discord` | Bot↔backend bridge (service-token). |
| `/notifications`, `/federation` | Fan-out + cross-guild feed. |

### 3.6 Realtime hub — the desktop wire contract (`realtime/hub.py`)

Behaviour is factored behind I/O seams (`WebSocketLike` + `PubSubLike`) so it is
fully unit-testable offline with fakes.

- **AUTH** — client connects with the access JWT as `?token=<jwt>`; bad/missing →
  close `4401` before any data.
- **SUBSCRIBE** — `{"type":"subscribe","matchId":…}`; backend sets the
  `app.current_guild` GUC and verifies the principal is a **participant**
  (`client_sessions` row under RLS) before subscribing to the match's Redis
  command channel.
- **SERVER→CLIENT** — orchestration publishes `lol_realtime.Envelope` commands;
  the hub relays each as `{"type":"command","command":…}`.
- **CLIENT→SERVER** — `{"type":"ack"|"heartbeat"|"report",…}` route to the
  orchestration ingestion functions (epoch-fenced server-side; never reimplemented).
- **Framing** — JSON text frames; a malformed frame is logged + skipped, never fatal.

### 3.7 Result pipeline (PLAN.md §3.6.5 / §3.6.6)

End-to-end: each desktop client captures an `EogReport` and sends it as a
`report` frame. `realtime/hub.py` `_route_report` does two **independent** things:

1. **Phase advance** — `ingest_lobby_state(GAME_ENDED)` (epoch-fenced).
2. **Persistence** — `_persist_result_report` parses the `EogReport`
   (`body["report"]`), flattens `teams[].players[]` into
   `{puuid: {kills,deaths,assists}}`, and writes one **tenant-scoped**
   `result_reports` row. **Idempotent first-write-wins** on
   `UNIQUE(lcu_game_id, reporter_puuid, source)` via a SELECT-existing check + a
   `begin_nested()` SAVEPOINT that catches `IntegrityError` (so a unique-key race
   never poisons the phase-advance commit). `claimed_winner_team` is left NULL —
   mapping the eog winner (a player set) to a team UUID is the adjudicator's job.

**Adjudication** (`lol_core.adjudication.adjudicate_result`) is pure and fully
implemented: it takes the ≤10 `ResultReportDTO`s + `roster_by_team` and returns
`ACCEPT` / `DISPUTE` / `INSUFFICIENT` with an anomaly audit trail (≥6/10 agreeing
with **both teams represented**, game-id binding, kill-stream cross-check,
byte-identical-payload + asymmetric-claim + schema-disagreement detection).

**Status / gap:** report **persistence is implemented and tested**. The
**corroboration trigger** that loads the rows, calls `adjudicate_result`, writes
`match_results`, and advances the bracket is **not yet wired** — the worker
`score_corroboration` job is still a stub. Admin override
(`POST /results/matches/{id}/winner`) is the working backstop today.

### 3.8 Testing

- **Unit tests run offline** — pure logic + FastAPI handlers with a fake/mock
  session; all external HTTP mocked.
- **Postgres integration tests** (`tests/test_rls_isolation.py`,
  `tests/test_integration_pg.py`) are **gated on `LOL_DB_TESTS=1`** (CI), proving
  RLS isolation, cross-guild escalation, and cascades a fake session cannot.
- Run directly: `cd services/backend && PYTHONPATH=src python -m pytest -q`.

---

## 4. Desktop client (`services/desktop`)

### 4.1 Stack & posture

- **Electron** main process (`src/main.ts`, entry `dist/main.js`), TypeScript via
  `tsc`, tests via `node --test` (`tsx`).
- Depends on **`@lol/lcu`** (the audited LCU adapter) + **`ws`** (WebSocket).
- **Runs natively** (must reach `127.0.0.1` LCU + `:2999`); its Dockerfile is a
  build/packaging container only.
- **ToS posture:** every LCU **write** (create lobby, invite, auto-accept, start)
  is gated behind a per-session default-OFF opt-in + presence gate + human-paced
  rate limits + recorded consent + a global kill-switch. **Korea excluded. No
  in-match gameplay automation, ever.**
- **Import-safety invariant:** importing `main.ts` performs NO IO (no socket, no
  lockfile read, no window). All IO lives behind `bootstrap()` / `runApp()`,
  guarded by `isMainEntry()`. Constructors are pure (keeps it unit-testable).

### 4.2 Module map (`src/`)

| Path | Responsibility |
|---|---|
| `main.ts` | Electron main process. `DesktopRuntime` orchestrates everything; `createLcuAdapter()`; IPC handlers (`lol:read-summoner`, verify, …); `bootstrap()`/`runApp()` real entry. |
| `preload.cts` | Context-isolated preload bridge (`contextBridge`) exposing the safe IPC surface to the renderer. |
| `lcu/readSummoner.ts` | READ-ONLY `current-summoner` Riot ID + region read (`CurrentSummonerOk` / `…Err`). |
| `lcu/verify.ts` | `verifyDesktop()` — POSTs `{code, game_name, tag_line, region}` to `/accounts/verify-desktop`. |
| `lcu/realRuntime.ts` | `createRealLcuRuntime()` — the real lockfile/HTTP runtime injected into the adapter. |
| `identity/identityCheck.ts` | `checkLcuIdentity()` — compares local LCU PUUID to the backend's verified PUUID; **advisory** wrong-account warning (never gates auth). |
| `orchestration/protocol.ts` | TS mirror of the `lol-realtime` envelopes: `LobbyCommand`s, `LobbyAck`, `HostHeartbeat`, `ResultReportMessage`, and the `BackendTransport` seam. |
| `orchestration/controller.ts` | `OrchestrationController` — heartbeat tick loop; drives host/guest LCU writes from inbound commands; captures live events + eog and reports once. |
| `transport/wsTransport.ts` | `WebSocketLike` impl with reconnect + a **durable FIFO queue** for acks/reports (corroboration-critical, not dropped while disconnected). |

### 4.3 The LCU adapter (`libs/lol-lcu`)

The **single audited fix-point** for all reverse-engineered, per-patch-drifting
LCU / `:2999` calls. Community-RE and unsupported — every **write** is gated
(opt-in + kill-switch + presence + pacing). Key surface (`LcuAdapter`):

- `readLockfileAuth()`, `getCurrentSummoner()` (advisory identity),
  `getGameflowPhase()` — reads.
- `createCustomLobby()`, `invite()`, `autoAcceptInvite()` — **gated writes**.
- `captureLiveClientEvents()` — Documented `:2999` kill stream (in-game only).
- `captureEndOfGame()` — RE `eog-stats-block` → builds the `EogReport`
  (per-player KDA + winner + bundled kill stream).

Two impls: `LcuAdapterSkeleton` (every method throws "not implemented") and
`LcuAdapterImpl` (real HTTP). The desktop uses `LcuAdapterImpl`.

### 4.4 Key flows

**Account verification (PLAN.md §3.1).** `/register` mints a one-time code →
desktop reads the local LCU `current-summoner` Riot ID + region READ-ONLY →
`verifyDesktop()` POSTs `{code, game_name, tag_line, region}` to the unauth,
rate-limited `verify-desktop` → backend resolves the canonical PUUID via
Account-V1 (the double-verify), dedupes the `RiotAccount`, snapshots rank, and
DMs the user via the bot. The **code is the credential**; identity stays advisory.

**Autonomous match execution (PLAN.md §3.6).** Backend designates a host and
publishes commands over the realtime WS. The `OrchestrationController` consumes
them: host creates the lobby + invites by PUUID; guests auto-accept invites from
the designated host. A `HostHeartbeat` (~2–3s) feeds the worker failover watchdog;
host loss triggers lossless promotion / verbatim re-create at a new epoch.

**Stats capture & report (PLAN.md §3.6.4/§3.6.5).** While `InProgress`, the
controller polls the `:2999` live kill stream (soft-fail, best-effort). On
`EndOfGame` it calls `captureEndOfGame()`, bundles the live events into one
`EogReport`, and sends it via `transport.sendReport({matchId, hostEpoch, report})`.
The transport's durable queue guarantees the report survives reconnects (it
degrades to "honest missing" in the quorum, never a crash). The backend persists
it as a `result_reports` row (§3.7).

### 4.5 Build & run

- `npm run build` (`tsc`) → `npm start` (`electron .`), or `make dev-desktop`.
- `npm test` — `node --test` over `src/**/*.test.ts` (offline; fake adapter +
  fake transport, no live socket/LCU).
- `npm run dist` — `electron-builder` packaging.

---

## 5. Quick reference — where things live

| Concern | Location |
|---|---|
| Canonical Python types (DTOs/enums) | `libs/lol-schemas` |
| Pure domain logic (bracket / orchestration / adjudication) | `libs/lol-core` |
| WS envelope contract | `libs/lol-realtime` (Python) + `services/desktop/src/orchestration/protocol.ts` (TS mirror) |
| LCU adapter (writes gated) | `libs/lol-lcu` |
| Backend RLS / tenant session | `app/auth/deps.py` (`get_tenant_session`) |
| Realtime WS hub | `app/realtime/hub.py` |
| Result persistence | `app/realtime/hub.py` `_persist_result_report` |
| Result adjudication (pure) | `lol_core/adjudication.py` (`adjudicate_result`) |
| Corroboration trigger (NOT wired) | `services/worker/src/app/main.py` `score_corroboration` (stub) |
| Desktop runtime + IPC | `services/desktop/src/main.ts` |
