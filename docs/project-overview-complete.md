# LeagueOfLeagues — Πλήρης Επισκόπηση Project (απ' την αρχή μέχρι τώρα)

> **Ημερομηνία σύνταξης:** 2026-07-21 · **Branch:** `phase-0-scaffold` · **Commits:** 35
> Αυτό το αρχείο είναι μια ολοκληρωμένη «φωτογραφία» όλων όσων έχουμε φτιάξει —
> από το scaffold μέχρι σήμερα. Deep-search σε git history, docs, services, libs,
> tests. Για το πλήρες design δες `PLAN.md`· για day-to-day κανόνες `CLAUDE.md`·
> για component map `docs/ARCHITECTURE.md`.

---

## 1. Τι είναι το LeagueOfLeagues

Multi-tenant πλατφόρμα **τουρνουά League of Legends**. Βασικά χαρακτηριστικά:

- Έως **16 ομάδες ανά τουρνουά**, **πολλά τουρνουά παράλληλα**.
- Λειτουργεί & αναφέρει μέσω **Discord** (multi-tenant: κάθε guild καλεί το bot και
  τρέχει δικά του τουρνουά, προαιρετικά ορατά στο κεντρικό *League Of Leagues* server).
- **Desktop client** που τρέχει **αυτόνομα κάθε custom match** μέσω του τοπικού
  League client (**LCU API**).
- **Admin web frontend** (React SPA) με CRUD.

### Το «v3 autonomous» μοντέλο — τι το κάνει διαφορετικό

Η θεμελιώδης παραδοχή του σχεδιασμού: **δεν χρησιμοποιούμε Riot Tournament API** —
το Match-V5 επιστρέφει 404 για custom games. Άρα:

- **Η εκτέλεση του match ζει στο μηχάνημα ενός παίκτη.** Ένας ορισμένος host δημιουργεί
  το custom lobby, κάνει invite by PUUID, auto-accept και start μέσω του τοπικού LCU.
  Αν χαθεί ο host → **server-authoritative, epoch-fenced failover** state machine
  (promote-first, αλλιώς re-create).
- **Η αλήθεια του αποτελέσματος είναι client-side corroboration**, όχι Riot-signed record.
  Και οι 10 παίκτες στέλνουν end-of-game stats (`eog-stats-block`) + live kill stream
  (`:2999`). Δεκτό μόνο με **quorum** (≥6/10 συμφωνούντα, με **και τις 2 ομάδες
  εκπροσωπούμενες**), περασμένο από **anomaly pipeline**. **Κανένα source δεν είναι
  authoritative** (όλα forgeable) → **admin override** είναι η τελική αρχή.
- **Το Riot Web API περιορίζεται σε Account-V1 μόνο** (Riot ID → PUUID για one-time
  linking). Personal/standard key αρκεί — καμία production-key review.
- Ο desktop client είναι **load-bearing για availability αλλά advisory για integrity**.

**Split trust model (PLAN §0):** ο client εκτελεί, δεν κρίνει.

---

## 2. Χρονολόγιο ανάπτυξης (από τα git commits)

Το project χτίστηκε σε **φάσεις 0–7** και μετά μπήκε σε **client-first refactor**.

| # | Commit | Τι έγινε |
|---|---|---|
| 1 | `77b1583` init | Πρώτο commit |
| 2 | `8eb2ee1` **Phase 0** | Scaffold monorepo + autonomous (v3) plan (123 files) |
| 3 | `02f9534` **Phase 1** | v3 data model + RLS guild isolation |
| 4 | `a3e9b45` **Phase 1** | auth (Discord OAuth2 + JWT), Account-V1 verification, PII lifecycle, CI RLS |
| 5 | `a72b879` **Phase 2** | tournament CRUD, registration/check-in, bracket start |
| 6 | `cbb37f1` **Phase 2** | autonomous LCU lobby orchestration + double-elim, teams, results |
| 7 | `ced1050` | CLAUDE.md: architecture, conventions, RLS invariant |
| 8 | `91a6e35` **Phase 3** | Discord bot, multi-guild onboarding, notifications, federation |
| 9 | `7783a2b` **Phase 4** | desktop orchestration controller (LCU client brain) |
| 10 | `d1f6ed5` **Phase 4** | realtime WebSocket (backend hub + desktop transport) |
| 11 | `f2b54bf` | wire orchestration command publisher → realtime WS |
| 12 | `39c9252` **Phase 5** | admin frontend (React SPA) + typed API client |
| 13 | `7b64df8` | dev-login (χωρίς Discord) για local dev |
| 14 | `4415850` | deploy-ready admin SPA + dev workflow fixes |
| 15 | `bb20f46` | host-run targets, bot startup reconcile, env docs |
| 16 | `320df5d` | **accounts:** code + desktop/LCU verification με server-side Account-V1 resolve |
| 17 | `233695f` | admin player roster; adjudication kill-stream anomaly flags |
| 18 | `3f1e4c6` | persist eog reports ως `result_reports` rows; vite IPv4 proxy; arch docs |
| 19 | `aef552b` | **desktop-auth:** bot pairing-code session + reauth + LCU status badge |
| 20 | `62cf0ef` | self-service `/accounts` list + `/unlink` |
| 21 | `38cc0e7` | per-game match-history view στο SPA |
| 22 | `06e5533` | **results:** auto-adjudicate match reports σε decided winner |
| 23 | `a21cd12` | JWT-tamper test deterministic |
| 24 | `1fd61f3` | **desktop:** multi-account self-service + hextech UI redesign |
| 25 | `dc0ff01` | **results:** dispute queue + audit trail backends |
| 26 | `482a449` | teams: join by name αντί για raw id |
| 27 | `08f46e9` | **tournaments:** 1v1 ARAM game mode + best-of-N series |
| 28 | `c6e599d` | **client-first:** μεταφορά user actions bot→desktop, join codes, presence bell |
| 29 | `77b58ba` | teams+tournaments: approval flow, rosters, size cap, smart join, bracket |
| 30 | `84ed98e` | scheduled tournaments (timed registration + auto-announce) |
| 31 | `1e64bc2` | match-ready gate + auto-lobby + live announce |
| 32 | `8b142b5` | docs: πρόταση pivot σε Riot Tournament Codes |
| 33 | `7309e34` | docs: **owner decision — custom lobbies only** (Tournament Codes ❌ rejected) |
| 34 | `d352878` | teams: transfer captaincy + audit edge cases §1–§7 |
| 35 | `18916f5` | docs: session summary 2026-07-20 |

**Κατάσταση σήμερα:** και οι 8 φάσεις (0–7) χτισμένες· τώρα σε **client-first
refactor** (user actions bot → desktop· το bot μένει σε admin + onboarding + notifications).

---

## 3. Monorepo & tooling

```
libs/            # versioned, framework-light shared libraries (path deps)
  lol-schemas    # Pydantic DTOs + StrEnums — η ΜΙΑ canonical Python type source
  lol-core       # pure domain logic: bracket engine, lobby state machine, adjudication
  lol-riot       # Account-V1 client (Riot ID→PUUID) + token-bucket limiter
  lol-realtime   # WS envelope models + JSON-schema export
  lol-discord    # Discord embed/notification helpers
  lol-lcu  (TS)  # το single audited LCU/:2999 adapter (write-gated)
  lol-clients-ts # generated TS API client (από backend OpenAPI)
services/
  backend        # FastAPI (auth/accounts/teams/tournaments/results/orchestration/…)
  worker         # arq: lobby_watchdog, account_verification_poll, score_corroboration
  bot            # discord.py AutoShardedBot
  frontend       # React + Vite admin SPA
  desktop        # Electron client (τρέχει native· consumes lol-lcu)
infra/           # docker base images, docker-compose, CI
docs/            # research, compliance, privacy, decisions, edge-cases, sessions
```

- **Python 3.12**, `src/` layout, full type hints, `from __future__ import annotations`.
  Dist names hyphenated (`lol-core`), import names underscored (`lol_core`).
- **Package manager:** `uv` (path deps, no central workspace). **Lint/format:** ruff
  (line-length 100). **Types:** mypy (strict). **Tests:** pytest (+`pytest-asyncio`).
- **Stack:** Postgres 16, Redis 7, FastAPI/uvicorn, arq, discord.py. TS (Node 22) για
  desktop/lcu/frontend (`tsc` + `node --test`).
- **Docker-first server-side**· ο desktop χτίζεται σε container αλλά **τρέχει native**
  (πρέπει να φτάνει το τοπικό LCU lockfile + `127.0.0.1:2999`).
- **Δύο `.env` επίτηδες:** repo-root (docker hostnames `postgres`/`redis`) vs
  `services/<svc>/.env` (localhost για host runs).
- **Makefile targets:** `make up`, `make dev` / `make dev-backend` / `make dev-frontend`
  / `make dev-bot`, `make test` / `make lint` / `make typecheck` / `make gen-clients`.

---

## 4. Shared libraries (libs/)

### 4.1 `lol-schemas` — canonical types

**Enums (`enums.py`, όλα `StrEnum`):**
- `GuildStatus`: ACTIVE, SUSPENDED, REMOVED
- `TeamMemberRole`: CAPTAIN, PLAYER, SUB
- `TeamMemberStatus`: PENDING, ACTIVE
- `UserGuildRoleName` (privilege-ordered): PLATFORM_OPERATOR, GUILD_ADMIN, CAPTAIN, PLAYER
- `TournamentStatus`: DRAFT, OPEN, CHECK_IN, RUNNING, COMPLETED, CANCELLED
- `TournamentFormat`: SINGLE_ELIMINATION, DOUBLE_ELIMINATION
- `GameMode`: SUMMONERS_RIFT_5V5, ARAM_1V1 — με properties `team_size` (5/1),
  `total_participants` (10/2), `quorum_threshold` (6/2)
- `RegistrationStatus`: PENDING, APPROVED, CHECKED_IN
- `MatchStatus`: PENDING, SCHEDULED, IN_PROGRESS, COMPLETED, WALKOVER, FORFEIT
- `LobbyPhase` (11): ASSIGNED_HOST, AWAITING_CREATE, LOBBY_OPEN, FILLING, READY,
  STARTING, IN_GAME, RESULT_PENDING, ENDED, ABORTED, HOST_LOST
- `ResultSource`: EOG_STATS_BLOCK, LIVE_CLIENT_DATA
- `ResultDecidedBy`: QUORUM, ADMIN_OVERRIDE, WALKOVER, FORFEIT
- `MatchResultState`: PROVISIONAL, CONFIRMED, DISPUTED
- `RiotRegion` (continental): AMERICAS, ASIA, EUROPE

**DTOs (`dtos.py`, Pydantic v2, `MAX_TEAMS=16`):** UserDTO, RiotAccountDTO,
DesktopRegistrationDTO/VerifyRequest/VerifyResponse, VerifyLcuRequest,
DesktopPair* (session bootstrap), GuildDTO, TeamDTO/TeamMemberDTO, TournamentDTO,
MatchDTO, MatchLobbyDTO, PlayerKDA, ResultReportDTO, QuorumDetail, MatchResultDTO.

### 4.2 `lol-core` — pure domain logic (no DB/IO/framework)

- **`bracket.py`** — single + double elimination engine (deterministic, pure).
  - `generate_single_elimination(seeds)` → `Bracket` (next power-of-two, canonical
    seed table, byes auto-advance, later rounds placeholders).
  - `generate_double_elimination(seeds)` → winners + `2*(k-1)`-round losers ladder +
    grand final· minimum-rematch invariant, bracket-reset ως GF replay.
  - Constants `MIN_TEAMS=2`, `MAX_TEAMS=16`.
- **`orchestration.py`** — pure lobby state machine:
  - **Host succession:** `seed_host_succession()` (deterministic SHA-256 server-seeded
    shuffle πάνω στα host-opt-in members), `next_successor()`.
  - **Phase-gated transitions:** `HAPPY_PATH` (ASSIGNED_HOST → … → RESULT_PENDING),
    `TERMINAL_PHASES` = {ENDED, ABORTED, HOST_LOST}, `decide_transition()` πετάει
    `IllegalTransitionError` σε illegal edges.
  - **Failover:** `FailoverAction` = PROMOTE / RECREATE / NOOP_INGAME / ABORT_TO_MANUAL·
    `decide_failover()` (grace gate → IN_GAME no-op → pre-game promote/recreate/abort)·
    epoch fencing (`is_epoch_current`, `reject_stale_ack`)· heartbeat helpers.
- **`adjudication.py`** — quorum/anomaly (`QUORUM_THRESHOLD=6`, `TOTAL=10`).
  `adjudicate_result(reports, roster_by_team)` → ACCEPT / DISPUTE / INSUFFICIENT +
  anomaly audit trail. Pipeline: roster preconditions → identity dedupe → bad-KDA/stale-
  nonce disqualify → game-id binding → winner clustering + anomaly detection →
  verdict. **18 anomaly flags** (π.χ. `game_id_split`, `reused_game_id`,
  `asymmetric_winner_claim`, `byte_identical_payload`, `killstream_contradiction`,
  `one_sided_quorum`, `missing_reports`).

### 4.3 `lol-riot` — Account-V1-first Riot client

- **`account.py`** — `RiotAccountClient` (httpx, shared limiter, 429 retry): 
  `get_account_by_riot_id` (canonical PUUID), `get_summoner_by_puuid`,
  `get_league_entries_by_puuid` (Solo/Duo rank).
- **`limiter.py`** — `TokenBucketLimiter` (continuous refill, injectable clock).
- **`routing.py`** — `ContinentalRoute` (Account-V1: AMERICAS/ASIA/EUROPE) +
  `PlatformRoute` (Summoner-V4/League-V4: NA1, EUW1, EUN1, KR, JP1, …).

### 4.4 `lol-realtime` — WS wire contract

`envelopes.py` (`SCHEMA_VERSION="0"`, frozen models). Κάθε μήνυμα είναι `Envelope`
(origin_guild_id + AudienceDescriptor + discriminated payload).
- **Backend→client:** `LobbyCommand` (kinds: CREATE_LOBBY, INVITE, AUTO_ACCEPT,
  PROMOTE, START_CHAMP_SELECT, ABORT), CreateLobbyCommand (με `LobbySpec`), κ.λπ.
- **Client→backend:** ReportAck, LobbyAck, HostHeartbeat, ResultReportEnvelope.
- Κάθε command φέρει `command_id` (at-most-once) + `host_epoch` fence.
- `export_json_schema()` για `make gen-clients` / CI drift-check.

### 4.5 `lol-discord` — embeds & notifications

- `embeds.py` — `build_embed()` με Discord hard-limit truncation, `EmbedColor`.
- `notifications.py` — δύο audiences με privacy boundary:
  - **Guild-local:** registration_code/verified/approved, tournament_announced,
    team_created/joined, tournament_*, match_ready, match_result.
  - **Central feed:** `build_central_feed_item()` δέχεται μόνο whitelisted projection·
    πετάει ValueError αν εμφανιστεί κάποιο `_FORBIDDEN_CENTRAL_KEYS` (puuid,
    discord_id, user_id, summoner_id/name) — privacy defence-in-depth.

### 4.6 `lol-lcu` (TypeScript) — το audited LCU adapter

Το single fix-point για ό,τι reverse-engineered/patch-fragile LCU + `:2999`.
- **`lockfile.ts`** — `parseLockfile()` + `deriveAuth()` (loopback base URL +
  `Basic riot:<pw>`· password rotates κάθε launch).
- **`transport.ts`** — injected IO seams (HttpTransport, FileSystemReader, Clock, …)
  για testability· scoped self-signed TLS (ποτέ global).
- **`writeGate.ts`** — `WriteGate.acquire()` gates ΚΑΘΕ write με σειρά: kill-switch →
  per-session opt-in (default-off) → presence gate → rolling 60s rate-cap →
  human-pacing delay + jitter. `WriteRefusedError` για manual-fallback degrade.
- **`index.ts`** — το `LcuAdapter` interface: reads (`readLockfileAuth`,
  `getCurrentSummoner` advisory, `getGameflowPhase`), **gated writes**
  (`createCustomLobby` idempotent per `(matchId,hostEpoch)`, `invite`,
  `autoAcceptInvite`), `captureEndOfGame` (→ EogReport), `captureLiveClientEvents`
  (`:2999` kill stream). Δύο impls: `LcuAdapterSkeleton` (stub) + `LcuAdapterImpl` (real).

---

## 5. Backend (services/backend — FastAPI, `/api/v1`)

### 5.1 HTTP routes ανά module

**auth/ (`/auth`)** — `GET /login`, `GET /callback`, `POST /refresh`, `POST /logout`,
`POST /desktop-pair` (anon· one-time code → session), `POST /dev-login` (DEV only).

**accounts/ (`/accounts`)** — `GET /me` (linked accounts), `DELETE /me/{id}` (unlink),
`GET /accounts` (admin roster), `POST /link`, `GET /verify/{attempt_id}/status`,
`POST /verify-desktop` (DEPRECATED), `POST /verify-lcu` (JWT-authed desktop link).

**teams/ (`/teams`)** — create, list, `join-by-code`, `join-requests/mine`, `mine`,
get/disband, `{id}/join-code`, `{id}/members` (list/add/remove), `{id}/leave`,
`{id}/transfer-captain`, `{id}/join-requests` (list/approve/reject).

**tournaments/ (`/tournaments`)** — create (guild_admin), list, `mine`, `join-by-code`,
get, `{id}/bracket`, `matches/{id}/ready` (captain), `{id}/registrations`,
`{id}/check-in`, `{id}/start` (guild_admin).

**results/ (`/results`)** — `matches/{id}/winner` (admin override → advance),
`history` (per-game scoreboards), `disputes` (frozen-disputed list).

**orchestration/ (`/orchestration`)** — `matches/{id}/lobby` (create+init host),
`.../heartbeat` (epoch-fenced), `.../lobby-state` (ingest+ack), `.../failover`.

**realtime/ (`/realtime`)** — `WS /ws` (auth via `?token=`, subscription authz,
command relay).

**lifecycle/ (`/lifecycle`)** — `guilds/{id}/purge`, `users/{id}/delete`,
`internal/guild-remove` (service-auth).

**federation/ (`/federation`)** — `share/request` / `approve` / `revoke`, `GET /feed`.

**presence/ (`/presence`)** — `GET /` (ποιος παίζει τώρα, global).

**audit/ (`/audit`)** — `GET /events` (platform_operator).

**integrations/discord/** — 16 service-auth (`X-Service-Token`) bridge endpoints:
guilds onboard/settings/resolve, accounts link/list/unlink, teams create/join/leave,
tournaments list/create/join/check-in/start/status.

### 5.2 Data model (`models/tables.py`)

**Global (εκτός per-guild RLS):** `guilds` (tenant anchor), `users`, `riot_accounts`
(canonical `puuid`, `lcu_puuid` advisory, rank snapshot), `refresh_tokens` (rotating,
family reuse-detection), `oauth_credentials` (Fernet-encrypted), `central_feed_items`,
`player_central_visibility`, `verification_attempts` (icon + desktop-code flows),
`audit_log` (append-only).

**Guild-scoped (RLS `guild_isolation`):** `user_guild_roles` (+`self_role_read` policy),
`teams` (unique `join_code`), `team_members` (PK `(team_id,user_id)`, status
pending/active), `tournaments` (game_mode, best_of, scheduling, `join_code`),
`registrations`, `tournament_participants` (join-code pool· solo ή team),
`matches` (round, bracket_slot, team_a/b, winner, best-of series state,
readiness gate cols), `match_lobbies` (host_puuid, host_succession, host_epoch,
LobbyPhase), `client_sessions` (liveness, consent, host_opt_in), `result_reports`
(forgeable per-participant· unique `(lcu_game_id, reporter_puuid, source)`),
`match_results` (decided· default DISPUTED = fail-closed), `result_nonces`
(single-use freshness).

### 5.3 Migrations (linear chain 0001 → 0012)

| Migration | Τι προσθέτει |
|---|---|
| 0001 baseline | minimal `guilds` + RLS posture marker + app role |
| 0002 core_schema_rls | πλήρες v3 schema + FORCE RLS `guild_isolation` παντού |
| 0003 auth | `user_guild_roles`, `refresh_tokens`, `oauth_credentials` + `self_role_read` |
| 0004 desktop_code_verification | desktop one-time-code cols στο `verification_attempts` |
| 0005 desktop_rank_snapshot | `lcu_puuid` + rank snapshot cols στο `riot_accounts` |
| 0006 refresh_token_device | `device_label`/`device_id` (desktop pairing) |
| 0007 tournament_game_mode | `tournaments.game_mode` (5v5 / 1v1 ARAM) |
| 0008 best_of_series | `best_of` + match series state (`wins_a/b`, `scored_game_ids`) |
| 0009 join_codes_participants | `join_code` σε tournaments+teams + `tournament_participants` |
| 0010 team_member_status | `team_members.status` (pending/active) |
| 0011 tournament_schedule | `registration_opens_at`, `start_at`, `start_announced_at` |
| 0012 match_readiness | `ready_deadline_at`, `team_a/b_ready_at` |

### 5.4 Domain logic (κύρια αρχεία)

- **accounts/service.py** — Account-V1 linking + ownership verification, rank snapshot.
- **auth/** — `jwt.py` (HS256 access), `tokens.py` (rotating refresh + family reuse),
  `discord_oauth.py`, `deps.py` (`get_current_principal`, `require_role`, tenant session).
- **teams/service.py** — CRUD + membership + approval flow + transfer-captain.
- **tournaments/** — `service.py` (CRUD, join-by-code, bracket planning),
  `scheduler.py` (DRAFT→OPEN ticker + announcements), `readiness.py` (5-min match-ready
  gate + walkover).
- **results/** — `service.py` (record winner + bracket advancement + bye chains),
  `corroboration.py` (γεφυρώνει `result_reports` → `adjudicate_result` inline).
- **orchestration/service.py** — autonomous LCU lobby lifecycle (delegates policy στο
  pure `lol_core.orchestration`· CommandPublisher seam → realtime).
- **realtime/hub.py** — WS hub πίσω από I/O seams (fully testable offline).
- **lifecycle/service.py** — guild + user purge / PII deletion.
- **notifications/** — `events.py` (envelope contract), `service.py` (sole emitter).
- **federation/service.py** — consented one-way central-feed projection.
- **presence/service.py** — ephemeral global presence σε Redis (short TTL, non-PII).
- **security/crypto.py** — Fernet encryption για PII at rest.
- Top-level: `main.py` (app factory + lifespan loops), `db.py` (`get_tenant_session`
  θέτει το `app.current_guild` GUC), `joincodes.py`, `ratelimit.py`.

### 5.5 Multi-tenant RLS — το load-bearing invariant

Κάθε guild-scoped table έχει Postgres **FORCE ROW LEVEL SECURITY** με policy
`guild_isolation` πάνω στο `current_setting('app.current_guild', true)`.
- Request path συνδέεται ως **non-BYPASSRLS `app_rw`** (μέσω `APP_DATABASE_URL`)·
  migrations ως owner (`DATABASE_URL`). Ο app **δεν** χρησιμοποιεί superuser.
- Το GUC τίθεται per-transaction από `get_tenant_session` / `require_tenant_role`.
- Unset GUC → **fail closed** (zero rows).
- **Same-guild invariant είναι δομικό:** τα children δείχνουν parents με composite
  `(id, guild_id)` FK σε `UNIQUE(id, guild_id)` — φυσικά αδύνατο cross-guild pointer.
- Inserts διαβάζουν το acting guild πίσω από το GUC, ποτέ από request input.

---

## 6. Worker (services/worker — arq)

`WorkerSettings` (`arq app.main.WorkerSettings`). Jobs:
- **`lobby_watchdog(match_id)`** — host-drop failover: missed heartbeats → `decide_failover`
  → PROMOTE (epoch-fenced) ή ABORT_TO_MANUAL· in-game host loss → NOOP· γράφει audit log.
- **`account_verification_poll(attempt_id)`** — polls verification attempts
  (`_evaluate_attempt`): mark_verified / mark_expired / reenqueue.
- **`score_corroboration(match_id)`** — **stub** (η inline corroboration τρέχει στο
  backend report ingest· το batch worker path δεν είναι πλήρως wired).

---

## 7. Bot (services/bot — discord.py AutoShardedBot)

Thin presentation layer πάνω στο `BackendClient`, renders `lol_discord` embeds.
**Cogs:**
- **RegisterCog** — `/register` (+ alias `/link`): mints one-time sign-in/pairing code
  (ephemeral) που ο user επικολλά στο desktop app. Δεν παίρνει Riot ID — ο desktop
  διαβάζει το PUUID από το LCU.
- **TournamentAdminCog** — admin-only (Manage-Guild + backend `guild_admin` re-check):
  `/tournament-create` (name, max_teams 2–16, mode, best-of, optional times → μιντάρει
  join code), `/tournament-start` (generates bracket).

**Events (app/main.py):** `setup_hook` (adds cogs + notification subscriber), `on_ready`
(command sync + reconcile guilds που μπήκαν offline), `on_guild_join` (onboard),
`on_guild_remove` → `lifecycle.purge_guild`. `notifications.py`: single Redis subscriber
(`lol:notifications`) → renders embeds στο notify channel· failures logged & swallowed.

**Client-first slimming:** το bot είναι πλέον **admin + onboarding + notifications μόνο**.
Κάθε user action (accounts, teams, tournaments join/check-in/leave/status, unregister)
μετακόμισε στον desktop client.

---

## 8. Desktop client (services/desktop — Electron + TypeScript)

Τρέχει native (φτάνει τοπικό LCU + `:2999`). Injectable/never-throw modules
(`{ok:false,status,detail}` pattern). **src/ modules:**
- **auth/pairClient.ts** — passwordless pairing: bot mints code → user paste → exchange
  σε `POST /auth/desktop-pair` για session (access JWT + rotating refresh, encrypted
  via SecureStore) → silent refresh.
- **lcu/** — READ-ONLY LCU: `statusPoller` (online/loading/offline badge), `readSummoner`
  (Riot ID + PUUID + region), `verify` (→ `POST /accounts/verify-lcu`), `realRuntime`.
- **identity/identityCheck.ts** — advisory σύγκριση local LCU PUUID vs backend verified
  PUUID (`match|mismatch|unknown`)· ποτέ δεν μπλοκάρει auth.
- **accounts/** — `listAccounts` (`GET /accounts/me`), `unlinkAccount` (`DELETE /me/{id}`).
- **teams/** — create (captain + join code), joinByCode, getJoinCode, listMembers.
- **tournaments/** — listMine, joinByCode.
- **presence/** — global presence read (`GET /presence`) → header bell badge/panel.
- **orchestration/** — `controller.ts` (drives local client από backend commands,
  fences stale host-epochs, fails soft μέσω write-gate, στέλνει ένα eog report),
  `protocol.ts` (TS mirror των lol-realtime envelopes).
- **transport/wsTransport.ts** — WS client (`GET /realtime/ws`): subscribe handshake,
  reconnect με exp backoff + jitter, durable FIFO queue (bounded 256) για acks/reports
  (corroboration-critical), heartbeats last-write-wins.

---

## 9. Frontend (services/frontend — React + Vite admin SPA)

React 18 + react-router-dom 6 + `@lol/clients-ts`. Role-gating (`RequireRole`/`RoleGate`).
**Screens:**
- **HomeScreen** — operations dashboard (real-data KPI cards· ποτέ fabricated metric →
  em-dash σε missing source).
- **accounts/AccountsScreen** — admin player roster (read-only).
- **audit/AuditLogScreen** — append-only audit stream (platform_operator).
- **auth/** — LoginScreen (Discord OAuth + dev local-admin), AuthCallback, NotAuthorized.
- **guilds/GuildsScreen** — onboarded-guilds mgmt (platform_operator) + PURGE behind confirm.
- **results/** — MatchHistoryScreen (per-game scoreboards), DisputeQueueScreen (admin
  override via `results.recordWinner`).
- **teams/TeamsScreen** — team + roster mgmt.
- **tournaments/** — TournamentsScreen (list + create), TournamentDetail (registrations,
  check-in, Start → bracket, visualizer), CreateTournamentForm.

**Components:** BracketView (single-elim visualizer + Force-winner override),
CommandPalette (Cmd/Ctrl+K, ARIA combobox), StatCard/StatCardGrid, EmptyState/ErrorState,
Toast, Skeleton, + primitives. **Theme:** "Cobalt Slate" (dark default + full light
override, WCAG AA/AAA, `prefers-reduced-motion`/`prefers-color-scheme`). Όλα τα server
strings ως escaped React children (no `dangerouslySetInnerHTML`).

---

## 10. Κρίσιμες αποφάσεις (design decisions)

1. **Tournament Codes ❌ REJECTED (2026-07-20).** Έρευνα βρήκε ότι το Riot
   Tournament-V5 API υπάρχει (auto-lobby + authoritative result callback — ο δρόμος
   που χρησιμοποιούν Battlefy/Challengermode). **Ο owner αποφάσισε custom lobbies μόνο**
   — μένουμε 100% στο autonomous-LCU. Το `docs/decision-tournament-codes.md` κρατήθηκε
   ως research record· **δεν το ξανανοίγουμε**.
2. **Result trust model:** τα αποτελέσματα είναι forgeable (no Riot API for customs).
   Security = quorum + anomaly + admin override, **ΟΧΙ** tamper-proof client.
3. **Identity:** PUUID (Account-V1) είναι το canonical key & **REQUIRED**· το LCU 36-char
   UUID αποθηκεύεται μόνο ως advisory `lcu_puuid`. Verification = code + desktop/LCU +
   server-side Account-V1 resolve (double-verify). Identity μένει advisory.
4. **Desktop auth:** bot pairing-code (no OAuth screen) + rotating refresh στο keychain.
5. **Commits/PRs:** χωρίς AI attribution (no `Co-Authored-By`).

---

## 11. Edge cases — κατάσταση (docs/edge-cases.md)

Όλα τα cases είναι **🟢 done** ή **🟡 decided** (κανένα ανοιχτό blocker). Κύρια:
- **Team readiness lifecycle:** πριν το ready roster ρευστό· μετά το ready **5-min grace
  window** → walkover στην έτοιμη ομάδα (match-ready gate, migration 0012).
- **Teams:** full-team 409, idempotent join, cascade disband, **transfer captaincy**
  (λύνει το single-leader dead-end).
- **Tournaments:** full → 409, schedule validation → 400, <2 teams → 400, admin manual
  start πάντα επιτρεπτός.
- **Identity:** dedupe σε canonical PUUID (409 αν άλλου user)· 15-min single-use code·
  Account-V1 required, rank best-effort.
- **Lobby:** host offline → watchdog promote/abort· no-accept → μένει FILLING· epoch
  fencing κόβει stale clients.
- **Results:** conflicting → adjudicate (quorum+anomaly)· <quorum → δεν κλείνει (admin).
- **Notifications:** missing channel / bot offline → drop + log (best-effort, ποτέ crash).
- **RLS:** cross-guild join code → 404 + δομικά αδύνατο.
- **In-game leave (§0.3):** βασιζόμαστε στο built-in `/pause` του League (30') — 🔵 μοναδικό
  εναπομείναν open (αν χρειαστεί δικό μας forfeit timer).

---

## 12. Compliance & beta gate (docs/compliance-checklist.md)

- **Riot API:** personal/standard Account-V1 key (no Tournament API / production review).
  `riot.txt`, public privacy policy, disclaimer «not endorsed by Riot», free-only v1.
- **[BETA-GATE] LCU-write ToS spike → Riot DevRel.** Ο distributed desktop client που
  σε ξένα clients κάνει auto-accept / (host) auto-create/start custom lobby είναι το
  headline risk (cross-account distribution, unattended action, action rate). **Αυτό
  είναι το beta gate.** Korea **hard-excluded**. **No in-match gameplay automation ever.**
- **Account verification = READ-ONLY LCU** (✅ εκτός write-gate εντελώς).
- **Desktop signing:** Apple Developer ID + Windows Authenticode (CI παράγει unsigned
  dev installers μέχρι να υπάρχουν τα certs).
- **Discord:** non-privileged `guilds` intent, OAuth2 (+PKCE desktop), app verification plan.

---

## 13. Testing

- **Unit tests offline** — pure logic (bracket, orchestration, adjudication) + FastAPI
  handlers με fake/mock session· όλα τα external HTTP mocked.
- **Postgres integration tests** (`test_rls_isolation.py`, `test_integration_pg.py`)
  **gated σε `LOL_DB_TESTS=1`** (τρέχουν σε CI)· αποδεικνύουν RLS isolation, cross-guild
  escalation, cascades.
- **Test file counts (σήμερα):** backend 99, worker 69, bot 45, lol-core 42, lol-riot 39,
  lol-discord 39, lol-schemas 39, lol-realtime 1 · desktop 14 TS, frontend 18 TS.
- **Τελευταία γνωστά αποτελέσματα (session 2026-07-20):** backend **309 passed** (5
  skipped DB/CI), desktop **103 passed**, ruff/tsc/mypy καθαρά.

---

## 14. Τι μένει / open items

- **`score_corroboration` worker job** = stub. Η inline corroboration δουλεύει στο report
  ingest· το batch worker path που φορτώνει rows → `adjudicate_result` → `match_results`
  → advance δεν είναι πλήρως wired. **Admin override είναι το working backstop σήμερα.**
- **In-game leave forfeit timer** (edge §0.3) — αποφασισμένο να βασιστούμε στο League
  `/pause`· δικό μας timer προαιρετικό.
- **Beta gate:** LCU-write ToS spike στη Riot DevRel — owner action, μπλοκάρει το signed
  desktop ship.
- **Riot key / desktop signing certs / riot.txt / privacy site** — owner/ops actions.
- **In-lobby roster lock λεπτομέρειες** (ποιος μπαίνει στο 5-min window) — 🟡 decided,
  λεπτομέρειες open.

---

*Πηγές: git log (35 commits), `PLAN.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`,
`docs/edge-cases.md`, `docs/decision-tournament-codes.md`, `docs/compliance-checklist.md`,
`docs/session-2026-07-20.md`, deep code scan σε libs/ + services/ (routes, models,
migrations, enums, state machines, tests).*
