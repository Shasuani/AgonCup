# LeagueOfLeagues — Detailed Implementation Plan

> Tournament platform for *League of Legends*. Supports up to **16 teams per
> tournament**, **multiple tournaments running in parallel**, user registration
> and tournament status delivered through **Discord**, a **desktop companion
> client** that reads the live League client, and an **admin web frontend**.
> The Discord bot is **multi-tenant**: any guild can invite it and run its own
> tournaments, which can optionally be surfaced in the central *League Of
> Leagues* server.

---

## 1. System Overview

### 1.1 Components

| # | Component | Tech (proposed) | Responsibility |
|---|-----------|-----------------|----------------|
| 1 | **Backend API** | Python · FastAPI · PostgreSQL · Redis | Source of truth. Tournaments, brackets, accounts, auth, Riot integration, business rules. |
| 2 | **Discord Bot** | Python · discord.py (or Pycord) | User registration, team/tournament join, status notifications, multi-guild support. |
| 3 | **Desktop Client** | Electron + TypeScript *or* Python + PySide6 | Reads local League client (LCU + Live Client Data API), validates account ownership, streams live match state. |
| 4 | **Admin Frontend** | React + TypeScript + Vite | CRUD for accounts, tournaments, teams, matches. Role-gated. |
| 5 | **Riot Integration** | Inside backend | Account verification (summoner lookup), match/result validation. |

### 1.2 High-level Topology

```
                       ┌─────────────────────────┐
                       │     Admin Frontend       │ (React SPA)
                       └───────────┬──────────────┘
                                   │ HTTPS / REST
   ┌───────────────┐               │
   │ Discord Bot   │───REST/WS─────┤
   │ (multi-guild) │               │
   └───────────────┘        ┌──────▼───────┐      ┌──────────────┐
                            │  Backend API  │◄────►│  PostgreSQL  │
   ┌───────────────┐        │  (FastAPI)    │      └──────────────┘
   │ Desktop Client│──WS/REST│              │      ┌──────────────┐
   │ (LCU reader)  │         │              │◄────►│    Redis     │ (cache, pub/sub, queues)
   └───────────────┘        └──────┬────────┘      └──────────────┘
                                   │ HTTPS
                            ┌──────▼────────┐
                            │   Riot API    │
                            └───────────────┘
```

### 1.3 Why these choices
- **FastAPI**: async, native WebSocket support (live state), automatic OpenAPI docs the frontend/bot/client all consume.
- **PostgreSQL**: relational data (tournaments → teams → players → matches) with strong constraints; good for brackets.
- **Redis**: caching Riot responses (rate limits), WebSocket pub/sub fan-out, background job queue.
- **One shared OpenAPI contract** keeps the four clients in sync.

### 1.4 Cross-cutting principles
- **Docker-first**: every component (backend, bot, desktop build, frontend, Postgres, Redis) runs in a container. Nothing is "works on my machine" — the whole stack comes up with `docker compose up`. See §6.
- **DRY via shared libraries**: any logic touched by more than one component is extracted into a versioned internal library, never copy-pasted. See §5.
- **Single contract**: the backend's OpenAPI spec is the *one* source of truth; bot, frontend, and desktop clients are **generated** from it, not hand-written. A schema change propagates by regeneration.

---

## 2. Data Model (core entities)

```
Guild            id, discord_guild_id, name, settings, is_central, invited_at
User             id, discord_user_id, display_name, created_at
RiotAccount      id, user_id, puuid, summoner_name, region, verified, verified_at
Team             id, name, guild_id, captain_user_id, created_at
TeamMember       team_id, user_id, role(captain|player|sub)
Tournament       id, guild_id, name, status, format, max_teams(=16), start_at,
                 is_shared_to_central, ruleset_json
Registration     id, tournament_id, team_id, status(pending|approved|checked_in)
Match            id, tournament_id, round, bracket_slot, team_a, team_b,
                 winner_team_id, status, scheduled_at
MatchResult      id, match_id, riot_match_id, raw_payload, validated, source
AuditLog         id, actor_id, action, entity, before, after, ts
```

Key rules:
- `Tournament.max_teams` defaults to **16**, enforced at registration.
- Tournaments scoped by `guild_id`; `is_shared_to_central` exposes them in the central server.
- A `User` may link multiple `RiotAccount`s but only **verified** accounts can compete.

---

## 3. Component Plans

### 3.1 Backend API (Python / FastAPI)

**Modules**
- `auth/` — Discord OAuth2 login, JWT issuance, role-based access (admin / captain / player).
- `accounts/` — user + Riot account linking & verification flow.
- `tournaments/` — CRUD, bracket generation (single/double elim), seeding, check-in.
- `matches/` — scheduling, result submission, validation against Riot data.
- `riot/` — thin client over Riot API with caching + rate-limit handling.
- `realtime/` — WebSocket hub for live match state (desktop client → backend → subscribers).
- `webhooks/` — outbound events the Discord bot subscribes to (registration approved, match ready, etc.).

**Bracket engine**: pure functions, well-unit-tested; supports 2–16 teams, byes, single & double elimination.

**Account verification**: user sets a temporary value (e.g. specific summoner icon / verification code in profile) → backend polls Riot API → marks `verified`. Desktop client can also assert ownership via LCU session.

### 3.2 Discord Bot (multi-tenant)

**Slash commands**
- `/register` — link Discord ↔ Riot account (kicks off verification).
- `/team create|invite|leave`
- `/tournament list|join|leave|status`
- `/tournament create` (guild admins) — create a tournament for *their* guild.
- `/admin ...` — guild-scoped admin actions.

**Multi-guild design**
- On `GUILD_JOIN`, create/activate a `Guild` row.
- All data scoped by `guild_id`; per-guild settings (admin roles, channels).
- `is_shared_to_central` flag lets a guild publish a tournament into the central League Of Leagues server feed.
- Notifications via per-guild configured channels (check-in reminders, match-ready, results).

**Comms**: bot talks to backend over REST + subscribes to a WebSocket/webhook for push events. **No game logic in the bot** — it's a thin presentation layer.

### 3.3 Desktop Client (Windows/Mac)

**Purpose**: collect data from the local League client and validate the player.

**Data sources**
- **LCU API** (`https://127.0.0.1:<port>` + lockfile auth) — logged-in summoner identity, account validation, game flow phase.
- **Live Client Data API** (`https://127.0.0.1:2999/liveclientdata/...`) — in-game live stats (scores, events, players) during matches.

**Responsibilities**
1. Detect running League client; read lockfile for auth.
2. Confirm the logged-in summoner matches the verified `RiotAccount` (anti-smurf / identity check).
3. Stream live match state to backend over authenticated WebSocket.
4. Show tournament/match status to the player locally.

**Tech recommendation**: **Electron + TypeScript** (cross-platform UI, easy packaging, reuses frontend skills) — *or* **Python + PySide6** if we want to keep one language across the stack. Decision flagged in §6.

### 3.4 Admin Frontend (React + TypeScript)

- Auth via Discord OAuth (same identities), admin role required.
- CRUD: accounts, guilds, tournaments, teams, registrations, matches, results.
- Bracket visualizer + manual override (reseed, force result, DQ).
- Audit log viewer.
- Consumes backend OpenAPI via generated TypeScript client.

### 3.5 Riot API Integration

- Centralized in backend; **never** call Riot from bot/client directly (rate limits + key secrecy).
- Endpoints: Account-V1 (puuid by Riot ID), Summoner-V4, Match-V5 (result validation), Spectator if needed.
- Redis-cached responses; respect rate limits with a token-bucket limiter.
- Regional routing handled centrally.

---

## 4. Repository Structure (monorepo)

```
LeagueOfLeagues/
├── libs/                       # Reusable, versioned internal libraries (DRY core)
│   ├── lol-core/               #   domain models, enums, business rules, bracket engine
│   ├── lol-schemas/            #   Pydantic schemas = single source of truth for DTOs
│   ├── lol-riot/               #   Riot API client (rate-limit, caching, regional routing)
│   ├── lol-realtime/           #   WebSocket message contracts + client/hub helpers
│   ├── lol-discord/            #   shared Discord helpers (embeds, command scaffolding)
│   ├── lol-clients-py/         #   generated Python API client (from OpenAPI)
│   └── lol-clients-ts/         #   generated TypeScript API client (from OpenAPI)
├── services/
│   ├── backend/                # FastAPI service  → depends on lol-core, lol-schemas, lol-riot
│   │   ├── app/
│   │   │   ├── auth/ accounts/ tournaments/ matches/ realtime/ webhooks/
│   │   │   └── main.py
│   │   ├── alembic/            # DB migrations
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── bot/                    # Discord bot → lol-clients-py, lol-discord, lol-realtime
│   │   ├── cogs/ main.py
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── desktop/                # Electron/PySide6 client → lol-clients-*, lol-realtime
│   │   └── Dockerfile          #   (build/packaging container; app runs natively)
│   └── frontend/               # React admin SPA → lol-clients-ts
│       └── Dockerfile          #   multi-stage build → nginx static serve
├── infra/                      # compose files, base images, CI, deployment manifests
│   ├── docker-compose.yml          # full stack for local dev
│   ├── docker-compose.override.yml # hot-reload, mounted volumes
│   ├── images/                     # shared base images (python-base, node-base)
│   └── ci/
├── docs/
├── Makefile                    # one-liners: make up / test / lint / gen-clients
└── README.md
```

> Libraries are consumed as local path/editable installs in dev and as built
> wheels/packages in CI — so the same code that runs locally ships in the image.

---

## 5. Shared Libraries (DRY)

Anything used by **more than one** component lives in `libs/` as a small,
independently-tested, versioned package. Components depend on libraries —
libraries never depend on components.

| Library | Owns | Consumed by | Why it's shared |
|---------|------|-------------|-----------------|
| **lol-schemas** | Pydantic request/response/event DTOs | backend, bot, desktop, (TS via codegen) | One definition of every payload — no drift between API and clients. |
| **lol-core** | Domain models, enums (status, format, region), **bracket engine**, business rules | backend, bot | Tournament/bracket logic written once, unit-tested in isolation. |
| **lol-riot** | Riot API client: auth, token-bucket rate limiter, Redis caching, regional routing | backend (only) | Single, hardened Riot integration; impossible to bypass rate limits. |
| **lol-realtime** | WebSocket message envelopes + hub/client helpers | backend, bot, desktop | Live-state contract identical on both ends of the socket. |
| **lol-discord** | Embed builders, command registration scaffolding, error formatting | bot (extensible to others) | Consistent UX, no copy-pasted boilerplate per cog/guild. |
| **lol-clients-py / -ts** | **Generated** API clients from OpenAPI | bot/desktop (py), frontend/desktop (ts) | Hand-written HTTP code eliminated; regenerate on contract change. |

**Rules of thumb**
- "Written twice" → extract to a library on the **second** occurrence.
- Libraries are framework-light (no FastAPI/discord.py imports in `lol-core`/`lol-schemas`) so they stay reusable.
- Each library has its own `pyproject.toml`, tests, and semver; CI publishes them to an internal index (or installs as path deps in the monorepo).
- The OpenAPI spec + JSON-schema for WebSocket events are checked in; `make gen-clients` regenerates `lol-clients-*` — generated code is never edited by hand.

---

## 6. Dockerization (everything containerized)

**Principle**: a fresh clone runs the entire platform with a single command; no
local Python/Node/Postgres install required beyond Docker.

**Local dev — `infra/docker-compose.yml`**

```
services:
  postgres   → official image, named volume, healthcheck
  redis      → official image
  backend    → ./services/backend/Dockerfile, depends_on: postgres+redis (healthy)
  bot        → ./services/bot/Dockerfile, depends_on: backend
  frontend   → ./services/frontend/Dockerfile (dev: vite server; prod: nginx)
  migrate    → one-shot alembic upgrade job, runs before backend
```
`docker-compose.override.yml` adds source-mounted volumes + hot reload for dev;
the base compose file stays production-shaped.

**Image strategy (DRY for Docker too)**
- **Shared base images** in `infra/images/`: `python-base` (locked Python + uv/poetry + common deps) and `node-base`. Service Dockerfiles `FROM` these — Python/Node toolchain pinned once.
- **Multi-stage builds**: `builder` stage installs deps + libs and compiles; slim `runtime` stage copies only artifacts → small, fast images.
- **Libraries baked in**: the `builder` stage installs `libs/*` so the exact code from `libs/` ships in the image (no drift between dev and prod).
- **Non-root runtime user**, healthchecks on every long-running service, `.dockerignore` to keep contexts small.
- **Desktop client**: containerized **build/packaging** (produces Windows/Mac installers in CI); the app itself runs natively on the player's machine since it must reach the local League client.

**Config & secrets**: 12-factor — all config via env vars, `.env.example` checked in, real secrets never in images. Same images across dev/staging/prod, behavior differs only by env.

**CI/CD**: build → test (in containers) → publish images to a registry, tagged by git SHA. Deploy via the same compose/manifests.

---

## 7. Phased Roadmap

### Phase 0 — Foundations (Week 1)
- Monorepo scaffolding (`libs/` + `services/`), tooling (ruff, mypy, pytest, pre-commit).
- **Shared base images** (`python-base`, `node-base`) + `docker-compose.yml` bringing up Postgres + Redis + backend.
- Stub the core libraries (`lol-schemas`, `lol-core`) so services depend on them from day one.
- Backend skeleton + health check + Alembic baseline, all running **in containers**.
- `Makefile` targets: `up`, `test`, `lint`, `gen-clients`.
- CI pipeline (lint, type-check, test — run inside containers).

### Phase 1 — Backend core + Accounts (Weeks 2–3)
- Data models + migrations.
- Discord OAuth login + JWT + roles.
- Riot account linking & verification flow.
- OpenAPI published to `shared/`.

### Phase 2 — Tournaments & Brackets (Weeks 3–5)
- Tournament/team/registration CRUD.
- Bracket engine (single + double elim, up to 16 teams, byes).
- Parallel tournaments per guild.
- Match scheduling.

### Phase 3 — Discord Bot (Weeks 5–7)
- Slash commands: register, team, tournament join/status.
- Multi-guild onboarding (`GUILD_JOIN`), per-guild settings.
- Webhook/WebSocket push notifications.
- `is_shared_to_central` feed into central server.

### Phase 4 — Desktop Client (Weeks 7–9)
- LCU lockfile auth + summoner identity validation.
- Live Client Data polling + WebSocket stream to backend.
- Packaging for Windows & Mac.

### Phase 5 — Admin Frontend (Weeks 9–11)
- Auth + role gate.
- CRUD screens, bracket visualizer, manual overrides, audit log.

### Phase 6 — Result Validation & Hardening (Weeks 11–13)
- Match-V5 result validation cross-check vs live client data.
- Anti-abuse (smurf detection, identity mismatch alerts).
- Load test parallel tournaments, observability (logs/metrics/traces).

### Phase 7 — Beta & Launch (Week 13+)
- Closed beta with a few guilds, feedback loop.
- Riot API production key application.
- Deployment hardening, backups, runbooks.

---

## 8. Open Decisions (need confirmation)

1. **Desktop client stack** — Electron/TypeScript (cross-platform, UI-rich) vs Python/PySide6 (single-language stack). *Recommendation: Electron.*
2. **Discord library** — discord.py vs Pycord vs interactions.py. *Recommendation: discord.py.*
3. **Tournament formats** at launch — single elim only, or single + double + round-robin? *Recommendation: single + double elim first.*
4. **Hosting** — self-host vs cloud (AWS/GCP/Fly.io). Affects infra in Phase 0.
5. **Riot API key** — personal/dev key for build-out; production key application timing (long lead time — start early).
6. **Auth model** — Discord OAuth as the *only* identity provider? *Recommendation: yes.*

---

## 9. Key Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Riot production API key approval delay | Blocks live validation | Apply early; build against dev key with mocks. |
| LCU/Live Client API undocumented changes | Breaks desktop client | Abstract behind an adapter; version-pin; graceful degradation. |
| Rate limits under many parallel tournaments | Validation failures | Central caching + queueing in backend, never client-side calls. |
| Multi-tenant data isolation bugs | Cross-guild data leaks | `guild_id` scoping enforced at query layer + tests. |
| Smurfing / identity fraud | Unfair tournaments | Dual verification (Riot API + LCU session) + audit alerts. |

---

## 10. Immediate Next Steps

1. Confirm the **Open Decisions** in §8 (esp. desktop stack & hosting).
2. Scaffold the monorepo — `libs/` + `services/` (§4) and Phase 0 base images.
3. Stand up the containerized backend + Postgres/Redis via `docker compose up`.
4. Begin Riot dev API key + Discord application registration in parallel.
