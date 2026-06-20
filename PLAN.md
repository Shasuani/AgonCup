# LeagueOfLeagues — Detailed Implementation Plan (Hardened v3 — Autonomous)

> Tournament platform for *League of Legends*. Supports up to **16 teams per
> tournament**, **multiple tournaments running in parallel**, user registration
> and tournament status delivered through **Discord**, a **desktop client** that
> autonomously runs each custom match via the local League client (**LCU**), and
> an **admin web frontend**. The Discord bot is **multi-tenant**: any guild can
> invite it and run its own tournaments, optionally surfaced (with consent +
> approval) in the central *League Of Leagues* server.

---

## 0. What changed and why (vs v2)

v2 was built around **Riot Tournament-V5** (providers, tournament codes, result
callbacks, Match-V5/`games-by-code`) as the single *authoritative* result path.
That path is **removed entirely** in v3. The load-bearing changes:

1. **No Riot Tournament-V5 / tournament codes / provider callbacks anywhere.**
   Match-V5 returns **404 for custom games** (Riot dev-relations #472), so the
   prior "authoritative Riot record" did not even exist for the games we run.
   v3 creates and runs each 5v5 custom match **autonomously via the desktop
   client's LCU API**, not via Riot server-side tournament infrastructure.
2. **Result truth is now client-side corroboration, not a Riot-signed record.**
   Each of the 10 participants' clients reports its LCU end-of-game stats
   (`eog-stats-block`) + Live Client Data (`:2999`). The backend accepts a result
   on **host + majority agreement with BOTH teams represented**, screened by an
   **anomaly pipeline**; **admin override** resolves all disputes. **No source is
   cryptographically authoritative** — every byte is forgeable/adversarial.
3. **The desktop client is now load-bearing for match EXECUTION** (it creates the
   lobby, invites, auto-accepts, starts the game, captures results) but is
   **still NOT authoritative for result TRUTH, identity, or anti-smurf.** This is
   a *split* trust model: load-bearing for availability, advisory for integrity.
4. **LCU WRITES are permitted, scoped to lobby/setup only** (create lobby, invite
   by PUUID, auto-accept, switch-teams, promote, kick, start-champ-select). v2's
   "strictly read-only, no writes" rule is replaced by a bounded write allow-list
   with named ToS controls. **No in-match gameplay automation, ever.**
5. **Designated-player host + automatic failover** replaces server-side lobby
   creation. There is **no LCU host migration**: host loss tears down the lobby,
   so failover = pre-emptive `promote` (planned) or full re-create + re-invite
   (unplanned), driven by a fenced, phase-gated state machine.
6. **Riot Web API shrinks to Account-V1 only** (Riot ID → PUUID, one-time
   linking; optional minimal Summoner-V4 for the icon nonce). A **personal/
   standard key suffices** — **no Tournament API, no production-key review, no
   "no beta without key" gate.** Match-V5 is **OUT for v1.** This removes the
   4+ week production-key approval from the critical path.
7. **The internet-facing `/riot/callback` ingress is gone.** Riot never calls
   back. It is replaced by an **authenticated desktop result-report ingress**
   (10 clients POST their `eog-stats-block` reports) plus the existing WS plane,
   now carrying lobby-orchestration commands.
8. **A platform "referee bot account" is DEFERRED** (heavy infra + highest ToS
   ban risk). Noted as the primary future integrity upgrade (an independent,
   non-forgeable in-lobby witness), not a v1 component.

**Preserved from v2 (Tournament-V5-independent hardening, unchanged):** Postgres
RLS multi-tenant isolation; the arq worker tier; discord.py `AutoShardedBot` +
`guilds` intent; consented field-whitelisted "share to central" projection;
Docker-first + shared base images; the two-contract story (OpenAPI REST +
`lol-realtime` WS); secrets manager; CI CVE scanning; observability-from-day-0;
backups + RPO/RTO; expand/contract migrations.

---

## 1. System Overview

### 1.1 Components

| # | Component | Tech | Responsibility |
|---|-----------|------|----------------|
| 1 | **Backend API** | Python · FastAPI · PostgreSQL · Redis | Source of truth for tournaments, brackets, accounts, auth, business rules. **Single host-designation authority** (host epoch lease). Ingests the 10 clients' result reports; runs quorum + anomaly adjudication. Hosts the authenticated result-report + WS ingress. |
| 2 | **Worker** | Python · arq (asyncio, Redis-backed) | Retriable/scheduled work: Account-V1 verification polling, the **per-match lobby-orchestration watchdog/state machine**, result corroboration/anomaly scoring, stuck-match alerts, backups. |
| 3 | **Discord Bot** | Python · discord.py 2.6.x (`AutoShardedBot`) | Registration, team/tournament flows, check-in, notifications, no-show DMs. Multi-guild. Thin presentation layer. |
| 4 | **Desktop Client** | Electron + TypeScript | **Autonomous lobby orchestrator + result reporter.** Drives LCU lobby/setup writes on the user's own client (host) and auto-accepts (guests); captures end-of-game reports. **Load-bearing for match execution; advisory (never authoritative) for result truth, identity, anti-smurf.** |
| 5 | **Admin Frontend** | React + TypeScript + Vite | CRUD + bracket viz + **dispute/override queue** (anomaly-ranked). Guild-scoped for operators; cross-tenant only for a distinct platform-operator role. |

### 1.2 High-level Topology

```
                       ┌─────────────────────────┐
                       │     Admin Frontend       │ (React SPA, HTTPS/REST)
                       └───────────┬──────────────┘
   ┌───────────────┐               │
   │ Discord Bot   │──REST─────────┤        ┌──────────────┐
   │ (AutoSharded) │◄─Redis pub/sub─┤        │  PostgreSQL  │ (RLS)
   └───────────────┘               │  ┌─────►└──────────────┘
                            ┌───────▼──┴───┐  ┌──────────────┐
   10× Desktop     WS cmds  │  Backend API  │◄►│    Redis     │ (cache · pub/sub · arq broker · LCU-write n/a)
   Clients (LCU) ◄────────► │  (FastAPI)    │  └──────┬───────┘
   host + 9 guests  REST    │ + report      │         │ broker
        │ reports   ───────►│   ingress     │  ┌──────▼───────┐
        ▼                   └───────┬───────┘   │   Worker      │
   local LCU + :2999                │           │   (arq)       │──► Riot Account-V1
   (create/invite/accept/                       └───────────────┘    (one-time linking
    start/eog capture)                                                 only, light limiter)
```

**No Riot inbound.** Riot never POSTs to us (Tournament-V5 removed). The
internet-facing surface is the **authenticated result-report ingress** (10
clients POST `eog-stats-block` reports) and the **WS plane** carrying targeted,
acknowledged lobby-orchestration commands. The backend **cannot call the LCU
directly** (LCU is local-only, self-signed, per-launch lockfile); it sends
*intents* over WS to each desktop client, which executes the LCU calls locally
and reads state back. Observability is wired into every box from Phase 0.

### 1.3 Why these choices
- **FastAPI**: async, native WebSocket, auto-OpenAPI the REST clients regenerate.
- **PostgreSQL + RLS**: relational bracket data with strong constraints *and*
  DB-enforced tenant isolation; durable per-match orchestration state machine.
- **Redis**: cache, WS pub/sub fan-out, arq broker. Failure mode defined (§9).
- **arq worker tier**: durable, retriable per-match watchdog driving the host
  state machine; survives deploys; does not starve the web event loop.
- **Desktop client as orchestrator**: a lobby can **only** be created by a
  logged-in League client (no server-side creation without the Tournament API),
  so match execution must live on a player's machine.

### 1.4 Cross-cutting principles
- **Docker-first (server-side):** every server-side component runs in a
  container; `docker compose up` brings up the stack. **The desktop client is
  built/packaged in a container but runs natively** (it must reach the local LCU
  lockfile and `127.0.0.1:2999`).
- **DRY via shared libraries** (§5).
- **Two generated/validated contracts:**
  - The **OpenAPI spec** is the source of truth for all **REST** clients
    (`lol-clients-*` regenerated, never hand-written), incl. the result-report
    submission endpoint.
  - The **`lol-realtime` WebSocket envelopes** are the source of truth for
    real-time messages (Pydantic models → exported JSON-schema, CI drift-checked).
    v3 adds **targeted command envelopes** (host_assignment, lobby_credentials,
    create/invite/start intents, failover_signal) and **command ACKs**.
- **Trust boundary invariant (rewritten for v3):** *No source of result/identity
  data is cryptographically authoritative. All desktop-client input (LCU
  end-of-game stats, `:2999` Live Client Data, current-summoner PUUID) is
  forgeable and adversarial. Integrity rests on (a) cross-checking the up-to-10
  independent per-participant reports keyed by a pre-bound gameId with **both
  teams represented**, (b) anomaly detection, and (c) bounded admin override.
  Host-client actions are **authoritative-but-audited**; an admin can override
  anything contested. Results are **provisional until corroborated and always
  admin-revocable.* The host is **not special** for truth — it is one
  participant; its privilege is over lobby *execution*, fully audited.

---

## 2. Data Model (core entities)

```
Guild            id, discord_guild_id, name, settings(jsonb), is_central,
                 status(active|suspended|removed), invited_at, removed_at
User             id, discord_user_id, display_name, created_at            # global
RiotAccount      id, user_id, puuid (UNIQUE, canonical), summoner_id,
                 display_name(gameName,tagLine, refreshable, NON-identity),
                 region, verified, verified_at                            # global
Team             id, name, guild_id, captain_user_id, created_at          # UNIQUE(id,guild_id)
TeamMember       team_id, user_id, role(captain|player|sub)
Tournament       id, guild_id, name, status, format, max_teams(=16),
                 start_at, is_shared_to_central, ruleset_json             # UNIQUE(id,guild_id)
Registration     id, guild_id, tournament_id, team_id,
                 status(pending|approved|checked_in)
                 FK(team_id,guild_id)->Team(id,guild_id)
                 FK(tournament_id,guild_id)->Tournament(id,guild_id)      # same-guild invariant
Match            id, guild_id, tournament_id, round, bracket_slot,
                 team_a, team_b, winner_team_id, status, scheduled_at
MatchLobby       id, guild_id, match_id, host_puuid,
                 host_succession(puuid[]  -- deterministic, server-seeded order),
                 host_epoch(int, monotonic), lobby_name(UNIQUE-per-active),
                 lobby_password, lcu_lobby_id(nullable), lcu_game_id(nullable),
                 state(assigned_host|awaiting_create|lobby_open|filling|ready|
                       starting|in_game|result_pending|ended|aborted|host_lost),
                 last_host_heartbeat_at, created_at, started_at, ended_at
                 FK(match_id,guild_id)->Match(id,guild_id)                # same-guild
ClientSession    id, guild_id, user_id, puuid, match_id, lcu_reachable,
                 gameflow_phase, schema_version, last_heartbeat_at,
                 consent_version, consent_at, host_opt_in(bool)           # liveness/consent
ResultReport     id, guild_id, match_id, lobby_id, lcu_game_id,
                 reporter_puuid, source(eog_stats_block|live_client_data),
                 claimed_winner_team, reported_kda(jsonb), kill_stream(jsonb),
                 schema_version, nonce, raw_payload(jsonb), anomaly_flags(jsonb),
                 received_at
                 UNIQUE(lcu_game_id, reporter_puuid, source)             # no double-report
MatchResult      id, guild_id, match_id, tournament_id, lobby_id,
                 agreed_game_id, winner_team_id,
                 decided_by(quorum|admin_override|walkover|forfeit),
                 quorum_detail(jsonb: reports_counted, both_teams_represented,
                               anomaly_summary), admin_override_by,
                 admin_override_reason, state(provisional|confirmed|disputed),
                 decided_at
                 UNIQUE(match_id)
ResultNonce      id, match_id, puuid, nonce, lcu_game_id(nullable),
                 issued_at, expires_at, used_at                          # single-use freshness
CentralFeedItem  id, source_guild_id, tournament_id, projected_fields(jsonb whitelist),
                 published_by, published_at, approved_by, approved_at, revoked_at
PlayerCentralVisibility  user_id, tournament_id, visibility(team_only|show_riot_id)
VerificationAttempt  id, user_id, puuid, nonce, target_icon_id, status, expires_at
AuditLog         id, actor_id, actor_kind(platform|guild|system), guild_id,
                 action, entity, before, after, ts                       # append-only
```

Key rules and constraints:
- **PUUID is the canonical, immutable identity key** (resolved via Account-V1
  `by-riot-id`). `display_name`/`summoner_name` is stale, refreshable display
  data, **never** matched on for identity. `summoner_id` is stored because LCU
  invites are by `summonerId`.
- **`User`/`RiotAccount` are global**; all other entities are **guild-scoped**
  with `guild_id` for RLS. Per-guild visibility governs cross-guild field
  exposure (no de-anonymization).
- **Same-guild invariant** enforced structurally via composite FKs to
  `UNIQUE(id, guild_id)` keys (`Registration`, `Match`, `MatchLobby`).
- **Host designation is backend-authoritative.** `MatchLobby.host_epoch` is a
  monotonic lease; `host_succession` is a **deterministic, server-seeded order
  bound to the roster** (never "first checked-in", which an attacker can time).
  Re-designation is a conditional update
  (`UPDATE ... WHERE match_id=? AND host_epoch=expected`) — single-flight
  optimistic concurrency; the backend rejects any ack/report bearing a stale
  epoch.
- **Result integrity (rewritten):** there is **no authoritative `riot_match_id`**
  (customs 404 on Match-V5). `lcu_game_id` is a **corroboration value, not a
  trusted unique key** — it is client-reported and cross-checked. A `MatchResult`
  becomes `provisional` only when **≥6/10 identity-bound `ResultReport`s agree on
  the same `agreed_game_id` and winning team AND both teams are represented**, and
  no high-severity anomaly fires; otherwise `disputed` → admin override. Bracket
  advancement stays the idempotent conditional update
  (`SET winner_team_id WHERE winner_team_id IS NULL`).
- **`Tournament.max_teams` defaults to 16**, enforced at registration.

---

## 3. Component Plans

### 3.1 Backend API (FastAPI)

**Modules**
- `auth/` — Discord OAuth2 (auth-code + `state`), short-lived access JWT +
  rotating revocable refresh tokens, per-(user, guild) authorization.
- `accounts/` — Riot account linking (Account-V1) + the hardened verification flow.
- `tournaments/` — CRUD, bracket generation, seeding, check-in, policy-rule
  enforcement.
- `orchestration/` — **host-epoch lease + designation**, the per-match lobby
  state machine API, command issuance over WS, host-heartbeat ingestion,
  failover, no-show/substitute runbook. (Replaces the deleted `riot_callback/`.)
- `results/` — **authenticated result-report ingress**, nonce issuance/validation,
  quorum adjudication, anomaly scoring, dispute escalation, admin override.
- `riot/` — thin Account-V1 client (via `lol-riot`) with a light rate limiter.
- `realtime/` — WS hub with per-topic tenant authz **and** targeted command
  delivery + ACK tracking.

**Bracket engine** (`lol-core`): deterministic, pure, table-driven-tested for
N=3..16 incl. byes; walkover/forfeit is a *result type*, not a bracket shape;
winner advancement is a pure function of `(adjudicated result → next slot)`.

**Account verification (hardened, Account-V1 only):**
1. Resolve Riot ID → `puuid` via Account-V1 `by-riot-id` at link time; persist
   `puuid` (canonical) and `summoner_id`.
2. **Server-side ownership challenge:** single-use, short-TTL nonce bound to
   `(discord_user_id, puuid)` mapped to a random target `profileIconId`; an arq
   job polls Summoner-V4 `by-puuid` and marks verified on match (bounded retries,
   hard expiry). Rate-limit `/register` per user and per guild before any Riot
   call. (LCU `current-summoner` PUUID is an *advisory* secondary signal only.)
3. Support **re-verification and revocation**; `verified=true` is not permanent.

### 3.2 Discord Bot (multi-tenant)

**Slash commands** (`/register`, `/team …`, `/tournament list|join|status|create`,
`/checkin`, `/admin …`). Bot is **thin**: the **backend re-verifies guild-admin
role against Discord** for every privileged action (signed interaction member
data) — never self-asserted.

**Multi-guild design**
- `AutoShardedBot` from day one (sharding only *required* at 2500+ guilds).
- Only the non-privileged **`guilds` intent** + slash commands — stays below the
  June-2026 10,000-user privileged-intent review gate.
- Per-guild config in `Guild.settings`, keyed by `guild_id`.
- **Tenant lifecycle:** `GUILD_JOIN` activates; `GUILD_REMOVE` deactivates and
  schedules retention-windowed deletion of guild-scoped data; re-invite
  reactivates within the window. Global entities are never hard-deleted.
- Command sync in `setup_hook()` + owner-only manual sync.

**Internal push:** ONE channel — Redis pub/sub. The backend is the sole emitter;
the bot consumes and rejects any event whose `guild_id` is outside its guilds.

### 3.3 Desktop Client (Windows/Mac) — autonomous orchestrator + reporter

**Role (v3):** the desktop client is the **autonomous lobby orchestrator** (host)
and **result reporter** (all 10). It is **load-bearing for match execution** but
its data input remains **forgeable/adversarial and never authoritative** for
result truth, identity, or anti-smurf.

**ToS write posture (supersedes v2's "strictly read-only" rule):**
- **Bounded write allow-list — lobby/custom-game SETUP only:** create custom
  lobby (`POST /lol-lobby/v2/lobby`, `gameMode CLASSIC`, `teamSize 5`,
  `mapId 11`), invite by `summonerId`/PUUID (`POST /lol-lobby/v2/lobby/invitations`,
  single batched array), auto-accept (`POST /lol-lobby/v2/received-invitations/
  {id}/accept`), switch-teams, `promote` (failover), `kick`, `start-champ-select`,
  `leave`. **Categorically NO in-match gameplay automation** (no movement /
  targeting / casts / dodge / last-hit) and **no account-value / queue-farming.**
- **No matchmaking writes, ever:** the adapter exposes no method that calls
  `/lol-lobby/v2/lobby/matchmaking/search` or creates a non-custom (`isCustom:false`)
  lobby. Enforced by construction + a guard test.
- **No programmatic browse-and-join in v1:** auto-join is strictly invite-based.
  `GET/POST /lol-lobby/v1/custom-games/{id}/join` is **not implemented** (the
  password-body schema is community-inferred/unverified; extra unattended write
  for zero capability). The **manual fallback = lobby name + password shown in
  the UI**; players join by hand in the native client.
- **Informed consent + presence gate (mandatory before ANY write this session):**
  a **default-OFF, per-session opt-in** the *present* user explicitly enables
  (host: "create & run lobby for this match"; guest: "auto-accept this
  tournament's lobby invite"). Writes fire only while the consenting user has a
  **recent liveness heartbeat** (at-keyboard, client focused) — never on an
  AFK/absent player (the research's single biggest risk multiplier). Persist a
  per-user consent record (version + timestamp); re-prompt on policy-text change.
- **Auto-accept filtering:** event-driven via
  `OnJsonApiEvent_lol-lobby_v2_received-invitations` (poll fallback); accept
  **only** an invite whose inviter PUUID/summonerId == the platform-designated
  host for a match this client is checked into, for the **current host_epoch**;
  hard-exclude party/queue/ranked invites; dedupe `invitationId`s. Small
  randomized human-plausible delay before accept (defense-in-depth, not relied on).
- **Own-client only** via the local lockfile; never proxy or drive another account.
- **Disclosure + registration:** mandatory **"not endorsed by Riot Games"**
  disclaimer; plain-language notice that this drives the user's own client via an
  unofficial, unsupported, reverse-engineered API at residual, uninsurable
  account/hardware-ban risk (ToS §7.1); **register the LCU WRITE endpoints
  (host-side and guest-side, separated by role) on the Riot Developer Portal**;
  **hard-exclude the Korea region** from all write features.
- **TLS/secrets:** bundle and pin Riot's `riotgames.pem`; scope verify-disable
  strictly to local LCU/`:2999`; treat lockfile password + `--remoting-auth-token`
  as full-access secrets (memory-only, re-read each session, never log/persist).

**Honest residual:** benign user-initiated lobby-setup writes are **moderate/
low-tail** ban exposure (no documented ban pattern; a large ecosystem of
published tools — LeagueAutoAccept, Hextech Friends, RuneChanger — does identical
writes) — but it is **uninsurable**: no allow-list, no safe harbor, Riot can
break/sanction at will. This is far below the high near-certain risk of gameplay
scripting or value-farming.

**Single LCU adapter (`lol-lcu`):** every LCU/`:2999` call routes through one
versioned, audited TS adapter enforcing the opt-in/presence check, pacing/jitter,
invite filtering, retry/backoff caps, schema-version stamping, and a
**kill-switch** (per-user / per-guild / global). A schema drift or a Riot policy
shift is then a **single fix/disable point**.

### 3.4 Admin Frontend (React + TypeScript)

- Discord OAuth. **Two tiers:** guild operators (guild-scoped GUC) and platform
  operators (separate role, **audited RLS-bypassing** path).
- CRUD, bracket visualizer, manual override (reseed, force result, DQ).
- **Dispute/override queue:** anomaly-ranked (highest composite anomaly score
  first); shows both teams' raw payloads, the corroborated kill-stream vs claimed
  KDA, missing/late reports, and gameId agreement. **Admin override resolves any
  contested result and is the final authority**; every override is an append-only
  **tamper-evident AuditLog** entry. Per-user and per-guild **dispute rate limits**
  (cost-free anti-abuse — no stake/currency, which would suppress the
  minority-team signal that is the main collusion defense).

### 3.5 Riot Integration — Account-V1 linking only

**The only Riot Web API used.** One-time account linking: Riot ID
(`gameName`+`tagLine`) → PUUID via
`GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}` on a continental
cluster (`americas|asia|europe` — **not** `na1/euw1`); optional
`Summoner-V4 by-puuid` for the profile-icon nonce. **PUUID is canonical and
immutable.** A **personal/standard key** suffices (20 req/s, 100 req/2min) — **no
Tournament API, no production review, no provider, no callback, no Match-V5.**
**Match-V5 is OUT for v1.** A light token-bucket limiter (honor 429 `Retry-After`)
protects the one-time linking + verification polling; this is *not* the
per-match, per-region tournament-scale limiter v2 needed.

> **Public-launch caveat:** a personal key may not legally back a *public* app; a
> public/monetized launch forces a production key (which re-introduces
> Tournament-API eligibility we do not need). v1 stays **free-only/private**, so a
> personal key is the correct long-term tier (ties to §8 decisions 4 & 7).

### 3.6 Autonomous Match Operation (LCU)

The match-operation core of v3. **All LCU calls are community-reverse-engineered
and unsupported** unless marked otherwise; schemas drift between patches; route
everything through the single `lol-lcu` adapter. *(Documented = Riot-official;
RE = community-reverse-engineered/unsupported.)*

**3.6.1 Host designation + failover state machine (fenced, phase-gated)**

Phases: `ASSIGNED_HOST → AWAITING_CREATE → LOBBY_OPEN → FILLING → READY →
STARTING(champ-select) → IN_GAME → RESULT_PENDING`.

- **Host = a DESIGNATED PLAYER** whose desktop client owns the lobby. The
  succession order is **deterministic and server-seeded, bound to the roster**
  (never attacker-steerable "first checked-in"). Only players who gave the
  **host opt-in** at check-in are eligible; non-opted-in players are *skipped*,
  never commandeered.
- **Backend = single host authority via a monotonic `host_epoch` (lease).**
  Re-designation is one conditional DB update
  (`UPDATE ... WHERE match_id=? AND host_epoch=g`); 0 rows ⇒ another worker
  already handled it (idempotency guard against double-create). Every
  backend→client command carries `command_id` (at-most-once) **and** `host_epoch`;
  the client refuses any command with a stale epoch and, before creating, does
  `GET /lol-lobby/v2/lobby` (RE) — if it already owns a lobby for the current
  epoch it returns that handle instead of POSTing a second one. **Create is fenced
  by `(match_id, host_epoch)`,** which is what actually prevents double-lobby
  across a failover.
- **Transitions are driven off `GET /lol-gameflow/v1/gameflow-phase` (RE),** not
  the lobby DELETE event alone — DELETE fires on *both* host crash *and* normal
  game launch and is therefore ambiguous. DELETE is **advisory corroboration** only.
- **Liveness:** host posts a heartbeat `{match_id, host_epoch, phase, lobby_id,
  members_present}` every ~2–3s while pre-game; a per-match **arq watchdog**
  triggers failover after a bounded grace window of missed heartbeats **AND** only
  if the phase has not legitimately advanced. Guests forward the DELETE event as a
  corroborating fast-path signal (9 independent witnesses).
- **Per-phase host-drop rules:**
  - `AWAITING_CREATE / LOBBY_OPEN / FILLING / READY`: **promote-first if the host
    is still connected (the only lossless path), else full re-create** by the next
    opted-in eligible successor at a new epoch + re-invite all 9.
  - `STARTING(champ-select)`: abort champ-select for all; re-create at a new epoch
    — **but first confirm gameflow has not advanced to `GameStart/InProgress` on
    ANY surviving client**; if it has, do **not** re-create (the game launched),
    transition straight to `IN_GAME`.
  - `IN_GAME (GameStart/InProgress/Reconnect)`: **failover is a hard NO-OP.** The
    match runs server-side and survives any client death; the lobby no longer
    exists. A dropped player reconnects as a normal client; results fall to
    corroboration.
- **Split-brain reaping:** a deposed/flapping host, on reconnect, queries the
  backend for the current epoch **before any write**; if no longer host it stops
  all host behavior and best-effort reaps its orphan lobby via
  `DELETE /lol-lobby/v2/lobby` (RE) — treating 404/"not in a lobby" as success.
  The backend designates one canonical lobby if two are observed and surfaces an
  anomaly.
- **Bounded attempts:** cap failover/re-create at ~2–3 epochs; on exhaustion (or
  no opted-in standby remaining) stop all LCU automation and fall back to the
  **manual lobby name + password UI**. Embed `match_id` in the lobby name for
  audit correlation (human clarity, **not** collision prevention — invite-based
  join uses PUUID and is collision-immune). Every failover is a
  **high-attention append-only AuditLog event** surfaced to admins.

**3.6.2 Lobby create**
`POST /lol-lobby/v2/lobby` (RE) with `isCustom:true`,
`customGameLobby.configuration = {gameMode:"CLASSIC", mapId:11, teamSize:5,
mutators:{id:1}, spectatorPolicy:"AllAllowed"}`, `lobbyName`, `lobbyPassword`.
The **immutable lobby spec** (config + exact 10-PUUID/summonerId invitee list +
`host_epoch`) is owned by the backend so any successor re-creates **verbatim**
(`teamSize:5` caps the lobby at 10 — not the invitee list). On (re-)creation the
host reports the live `lcu_lobby_id` back; the backend re-broadcasts it (plus
stable name/password) so the manual fallback can re-resolve the new lobby.

**3.6.3 Invite-based auto-join (manual fallback)**
- Host sends **one batched** `POST /lol-lobby/v2/lobby/invitations` (RE) with the
  full `toSummonerId` array; re-invites of non-joined members use bounded
  exponential backoff + jitter (terminal/auth/404 errors are not retried). Invites
  keyed by `(match_id, toSummonerId, host_epoch)`.
- Guests auto-accept via the WS event (RE) filtered by inviter PUUID == designated
  host for the current epoch (party/queue invites hard-excluded), gated on
  per-session opt-in + presence. If a custom-lobby invite-type discriminator field
  exists, use it as an *additional* guard — **verify its name live first** (open
  research question); never depend on it as the sole filter.
- **Manual fallback (always-functional, first-class, tested):** if a guest has not
  opted into auto-accept, or invites/accepts break, the client surfaces the lobby
  name + password and the player joins by hand in the native League UI.

**3.6.4 In-game live state**
Each client opportunistically reads the **documented** Live Client Data API
`GET https://127.0.0.1:2999/liveclientdata/*` (port open only while
`gameflow-phase = InProgress`; dies on game exit). It captures the **`eventdata`
kill stream** (`KillerName/VictimName/Assisters/EventTime`, `GameStart→GameEnd`)
as a first-class corroboration signal. This is **read-only** and best-effort.

**3.6.5 End-of-game capture**
On `gameflow-phase = EndOfGame` (RE), the client immediately reads
`GET /lol-end-of-game/v1/eog-stats-block` (RE: root `gameId`, per-team
`isWinningTeam`, per-player `CHAMPIONS_KILLED/NUM_DEATHS/ASSISTS`) and POSTs its
report — **within a seconds-to-minutes window** (the block clears on next
champ-select/game; `:2999` is already gone). The report carries the
**server-issued nonce**, the linked-PUUID **session token**, the `schema_version`,
the eog scoreboard, and the captured kill stream, keyed by `(lcu_game_id,
reporter_puuid)`. Capture is **background/read-only** (no foregrounding, no writes).

**3.6.6 Result corroboration + admin-override integrity scheme**

*Threat model (documented in code):* every report is unsigned, user-attestable
JSON over a self-signed local cert with user-controlled credentials — **fully
forgeable**. The scheme assumes **at most one side (≤5 clients) colludes** on the
winner bit; the hard part it leverages is fabricating a **self-consistent
cross-client kill-stream + winner + KDA spanning BOTH teams**. A both-team
collusion (≥1 cooperating client on each side) defeats it **by design** and is
resolved by admin override. No local source is a trust anchor.

**Freshness + authentication layer (hardening beneath corroboration, NOT an
integrity guarantee):** the backend issues a **single-use, short-TTL nonce per
`(match_id, puuid)`** when the lobby is created/locked, bound server-side to the
platform-established gameId. Each report must present the authenticated
linked-PUUID **session token** AND echo a live nonce; reject missing/expired/
reused/wrong-PUUID nonces. TTL covers the full capture window + retry/backoff.
This defeats **naive replay and unauthenticated single-machine fabrication** and
guarantees each accepted report carries a distinct, live, per-PUUID credential —
but it does **not** defeat collusion (colluders share their own tokens) and does
**not** make content true.

**Anti-forgery mitigations (named):**
1. **Both-teams-represented quorum (primary collusion control):** auto-accept a
   result **only when ≥6/10 distinct identity-bound reports agree on the same
   `agreed_game_id` and winning team AND ≥1 agreeing report comes from each
   registered roster.** A one-sided majority can **never** auto-resolve — a single
   5-stack can never out-represent both sides. Modest floor (≥6/10, both teams),
   **not** 8/10, to tolerate ~4 honest-missing reports (capture race) without
   defeating autonomy.
2. **gameId binding hardening:** capture `gameId` from **multiple** clients at game
   start; require **unanimous gameId agreement** among reporters; reject if reports
   split across >1 gameId or if the reported gameId was used by any prior match
   (anti-substitution / anti-two-games). Residual gap (no out-of-band trusted
   observer) is a **named, accepted risk** pending the deferred referee-bot.
3. **Kill-stream cross-correlation:** the independently submitted `:2999`
   `eventdata` timelines are cross-correlated and weighted into the quorum; a
   result whose host+majority KDA/winner **materially contradicts** the
   corroborated kill stream is **HELD as disputed → admin override** (never
   auto-rejected — best-effort capture is lossy). Forging a fully self-consistent
   multi-witness kill timeline is far harder than forging one final scoreboard.
4. **Anomaly pipeline → priority-ranked admin queue:** mismatched/duplicate
   gameId; asymmetric/contradictory winner claims; missing/late reports;
   impossible/internally-inconsistent stats; **byte-identical/near-identical
   payloads** (collusion/replay); KDA contradicting the kill stream;
   **schema-version disagreement** across reports. Each signal feeds a composite
   anomaly score with documented **accept / flag / reject** thresholds, validated
   against an **adversarial test corpus** (forged eog JSON, 5-stack fabrications,
   replayed/byte-identical payloads, dropped-report scenarios).
5. **Schema-drift defense (no Riot fallback exists):** pin a validated client
   patch; CI-validate the `eog-stats-block`/`eventdata` parsers against captured
   live fixtures (primary) + a community Swagger mirror (secondary, lags);
   **continuously monitor fleet parse-success rate and alert on any drop** (the
   primary live-regression detector). **Fail closed but scoped:** fleet-wide low
   parse confidence ⇒ escalate to admin (never auto-accept); a **single** client's
   low-confidence parse ⇒ treat as a missing/anomalous report (its vote degrades;
   quorum proceeds) so one malicious client cannot grief every match to manual
   review.

**Below-floor / conflict consequences (deterministic; admin override is the final
escape hatch on every branch):**
- **Insufficient reports (capture-race, BENIGN default):** re-request within the
  persistence window; only then escalate to admin with a **replay-or-no-advance**
  default — **do not advance, do not penalize** the missing reporters.
- **Asymmetric/contradictory winner claims (adversarial signal):** mark contested,
  freeze advancement, escalate with both teams' raw payloads + anomaly scores.
- **Reliability signals (advisory, deferred — NOT a v1 auto-penalty):** record
  per-user report-success and asymmetric-claim history **as admin-review inputs
  only**; keep non-reporting (capture-race victims) and adversarial claiming as
  **separate** signals so honest technical failures are never bucketed with
  forgers; any future reputation mechanism thresholds on **forged/asymmetric
  claims, not absence of a report**, and is appealable via admin override.

**Future upgrade (deferred):** a platform **referee-bot account** in the lobby
would be the one report not under any player's control — the report pipeline
(keyed by `gameId` + reporter PUUID, weighted by source) is designed so a
referee-bot witness can slot in later as a **high-weight, near-authoritative
corroborator.** Deferred for v1 (heavy infra + highest ToS ban risk).

---

## 4. Repository Structure (monorepo)

```
LeagueOfLeagues/
├── libs/
│   ├── lol-core/        # domain models, enums, business rules, bracket engine,
│   │                    #   quorum/anomaly adjudication logic
│   ├── lol-schemas/     # Pydantic DTOs = the ONE canonical Python type source
│   ├── lol-riot/        # thin Account-V1 client (+ light token-bucket limiter)
│   ├── lol-lcu/         # (TS) single audited LCU/:2999 adapter — lobby lifecycle,
│   │                    #   invite/accept, eog + kill-stream capture, lockfile auth,
│   │                    #   pacing/opt-in/kill-switch, schema-version stamping
│   ├── lol-realtime/    # WS envelope Pydantic models (+ exported JSON-schema);
│   │                    #   targeted command + ACK envelopes
│   ├── lol-discord/     # shared Discord helpers
│   └── lol-clients-ts/  # GENERATED TS client (from OpenAPI)
├── services/
│   ├── backend/   # FastAPI: auth/ accounts/ tournaments/ orchestration/ results/ realtime/
│   ├── worker/    # arq: verification poll, per-match lobby watchdog/state machine,
│   │              #   corroboration/anomaly scoring, stuck-match alerts, backups
│   ├── bot/       # discord.py AutoShardedBot
│   ├── desktop/   # Electron orchestrator+reporter (consumes lol-lcu; signed per-OS in CI)
│   └── frontend/  # React admin SPA (+ dispute/override queue)
├── infra/         # docker-compose, shared base images, CI
├── docs/          # research-findings.md, privacy-policy, compliance checklist
├── Makefile
└── README.md
```

> Libs are consumed via **path deps** (committed lockfiles; baked into images in
> CI — one distribution model, no drift).

---

## 5. Shared Libraries (DRY)

| Library | Owns | Consumed by | Why shared |
|---------|------|-------------|------------|
| **lol-schemas** | Pydantic DTOs (single canonical Python types) | backend, worker, bot, (TS via codegen) | One definition of every payload. |
| **lol-core** | Domain models, bracket engine, **quorum/anomaly adjudication**, policy rules | backend, worker, bot | Tournament/bracket/integrity logic once, unit-tested. |
| **lol-riot** | **Account-V1 client + light token-bucket limiter** | backend, worker | One isolated, rate-limited one-time-linking client. (No Tournament-V5 helpers.) |
| **lol-lcu** (TS) | **Single audited LCU/:2999 adapter** (lobby lifecycle, invite/accept, eog + kill-stream capture, lockfile auth, pacing/opt-in/kill-switch, schema-version) | desktop only | The one fix-point for reverse-engineered, drifting LCU calls; load-bearing for the autonomous flow. |
| **lol-realtime** | WS envelope models + JSON-schema; targeted command + ACK envelopes; `origin_guild_id`/audience on every envelope | backend, bot, desktop | One real-time wire contract; drift-checked in CI. |
| **lol-discord** | Embed builders, command scaffolding, error formatting | bot | Consistent UX, no boilerplate. |
| **lol-clients-ts** | **Generated** TS API client from OpenAPI | frontend, desktop | Hand-written HTTP eliminated for TS. |

**Rules of thumb:** extract on second occurrence; libs are framework-light; WS
JSON-schema exported from lol-realtime models in `make gen-clients`. Do **not**
merge lol-riot, lol-lcu, or lol-realtime — each is a distinct isolation/fix point.

---

## 6. Dockerization

**Local dev — `infra/docker-compose.yml`**

```
services:
  postgres   → official image, named volume, healthcheck
  redis      → official image, AOF (appendfsync everysec) for queue state
  backend    → depends_on: postgres+redis (healthy)
  worker     → arq, FROM python-base, depends_on: postgres+redis (healthy)
  bot        → depends_on: backend
  frontend   → vite (dev) / nginx (prod)
  migrate    → expand/contract Alembic; backward-compatible, no blocking gate
  otel/logs  → metrics+logs collector (wired from Phase 0)
```

**Image strategy:** shared `python-base`/`node-base` images (toolchain pinned
once); multi-stage builds; libs baked in; non-root runtime; healthchecks on every
long-running service incl. `worker`.

**Desktop packaging:** per-OS native CI runners — a **macOS runner** that
codesigns + notarizes the `.app`/`.dmg`, a **Windows runner** that
Authenticode-signs the installer. **Auto-update is a critical-path item, not a
late add** (see §7): LCU schema drift on League's ~2-week patch cadence means a
broken client must be patched, re-signed, and force-updated fleet-wide fast.
Apple Developer Program (~$99/yr) + Windows cert are recurring costs (§8.4).

**Secrets:** a **secrets manager** (not just `.env`), key rotation, **one Riot
personal key per product** (Account-V1 only) separated from dev keys per
environment, the **Discord bot token** protected. **No Riot key or OAuth
`client_secret` ever ships in the Electron bundle** (asar is not encryption).

**CI/CD:** build → test (in containers) → **CVE scan (Trivy/Grype) gating images**
→ publish images tagged by git SHA → deploy to **staging** → smoke/integration →
promote the **same SHA** to prod. Scheduled base-image rebuild cadence; per-SHA
retention/GC; documented rollback (expand/contract + forward-fix). **CI also
validates the `lol-lcu` parsers against pinned live-client fixtures + a Swagger
mirror per League patch.**

---

## 7. Phased Roadmap

> **Assumptions:** team size = *N* engineers (state the real number). Add a
> ~20–30% buffer. External lead times (Discord verification, code-signing certs)
> are the schedule backbone. **The Riot production-key gate is GONE** — a personal
> Account-V1 key auto-generates; this removes ~4+ weeks from the critical path.
> The new critical path is the **desktop client + LCU automation + corroboration
> pipeline**, gated on the Phase-0/1 **LCU-write ToS spike**.

### Phase 0 — Foundations + critical-path kickoff (Week 1)
- Monorepo scaffolding, shared base images, `docker-compose` (Postgres, Redis,
  backend, worker), Makefile, CI (lint/type/test, **CVE scan**, **lol-lcu fixture
  validation harness**).
- Cross-cutting observability: structured JSON logging, `/healthz` + `/metrics`,
  DB-pool gauges.
- **Close §8.4 hosting** with a cost model (Postgres, Redis, web+worker, result
  ingress, registry+GC, observability, CI minutes, Apple/Windows signing certs).
- **Re-scoped LCU-write ToS spike (headline question to Riot DevRel):** *"Is a
  distributed desktop client that, on community members' own logged-in clients,
  auto-accepts host invitations and (host-side) auto-starts a custom lobby at
  tournament cadence, potentially while the player is not actively clicking,
  within ToS?"* Call out the three named risk multipliers — cross-account
  distribution (9 guests, not self-use), unattended action, action rate. Register
  the LCU **write** endpoints (host-side + guest-side) on the Developer Portal.
  **Hard-exclude Korea.** Beta is gated on this spike (as the read-only client was
  in v2).
- Register the Developer Portal product (Account-V1 personal key), draft the
  public **privacy policy**, host `riot.txt`.

### Phase 1 — Backend core + Accounts + Isolation (Weeks 2–3)
- Data models + migrations; **RLS landed with the first guild-scoped migration**
  (FORCE RLS, non-BYPASSRLS app role, `SET LOCAL app.current_guild`; same GUC
  discipline in worker sessions).
- **Cross-guild isolation test matrix in CI** (REST + WS + worker; stale-GUC
  negatives; pooled-connection no-leak).
- Auth: OAuth2 (auth-code+`state`), short-lived JWT + revocable refresh,
  per-(user, guild) roles.
- **Account-V1 linking + nonce/icon verification** on the worker.
- **Privacy/PII:** encrypt OAuth tokens + verification state at rest;
  user/guild deletion + `on_guild_remove` purge. **Automated Postgres backups +
  RPO/RTO** the moment real data exists.
- **Auto-update pipeline spike** (per-OS signed installer + forced-update gate) —
  pulled forward because the desktop client is now load-bearing.

### Phase 2 — Tournaments & Brackets + LCU lobby orchestration (Weeks 3–6)
- Tournament/team/registration/check-in CRUD with composite-FK same-guild invariant.
- **Single-elim** bracket engine + scheduling first; double-elim as a gated slice.
- **LCU lobby-orchestration build-out** behind `lol-lcu` pinned to a known patch:
  create lobby (CLASSIC/teamSize 5/mapId 11), batched invite, event-driven
  auto-accept, **host-epoch lease + designation + heartbeat watchdog**,
  **promote-based + re-create failover state machine** (phase-gated), manual
  name+password fallback, two-stage readiness gate.

### Phase 3 — Discord Bot + Federation (Weeks 5–8)
- Slash commands, `AutoShardedBot`, multi-guild onboarding + lifecycle, check-in.
- Redis pub/sub notifications; per-topic tenant authz.
- **Consented central feed:** publish-request + central-staff approval →
  field-whitelisted projection; per-player visibility consent;
  `AllowedMentions(everyone=False)` + markdown escaping. Federation isolation tests.
- Alert set: stuck-match-in-`AWAITING_CREATE/LOBBY_OPEN` count, failover rate,
  queue depth.

### Phase 4 — Desktop Client (Weeks 8–10) — *gated on the Phase-0/1 LCU-write spike*
- `lol-lcu` adapter: full lobby lifecycle WRITES (host) + auto-accept (guest) +
  eog/kill-stream capture; pinned `riotgames.pem`; **per-session opt-in + presence
  gate + consent record**; kill-switch.
- WS command/ACK plane + result-report POST; lockfile-rotation re-read; "WS up but
  LCU gone" detection; pre-flight self-test gating host-eligibility.
- **Per-OS signed packaging + forced auto-update** (refuses to orchestrate on a
  known-broken client version).

### Phase 5 — Admin Frontend (Weeks 10–12)
- Guild-operator vs platform-operator panes.
- CRUD, bracket visualizer, manual overrides, **anomaly-ranked dispute/override
  queue**, append-only audit viewer.

### Phase 6 — Result Corroboration & Hardening (Weeks 12–14)
- **10-client corroboration/quorum/anomaly pipeline** (keyed on LCU gameId; ≥6/10
  with BOTH teams represented; kill-stream cross-correlation; nonce freshness;
  full anomaly signal set) feeding the admin queue.
- **Adversarial test corpus** (forged eog JSON, 5-stack fabrications,
  replayed/byte-identical payloads, dropped reports); **capture-race timing tests**.
- **Autonomy SLO instrumentation:** gauges for `admin_override_rate`,
  `auto_resolved_rate`, `quorum_fail_rate`, `missing_report_rate`,
  `capture_window_miss_rate`; SLO defines "autonomous" (e.g. ≥90% auto-resolve);
  alert on breach — makes "autonomous, not secretly manual" a **measured** claim.
- Multi-replica load test; Redis failure-mode test; pgbouncer + `SET LOCAL` test.

### Phase 7 — Beta & Launch (Week 14+)
- Closed beta (few guilds). **Beta gate = the LCU-write ToS spike outcome + the
  `lol-lcu` adapter passing the live-client validation suite + the corroboration
  pipeline clearing the collusion/missed-report test matrix.** (No Riot-key gate.)
- Restore drill (RPO/RTO); on-call/triage; runbooks (host-failover, missing-client,
  LCU-outage kill-switch).
- Submit **Discord app verification** at the ~75-server notice (growth gate).

---

## 8. Open Decisions (resolved per locked decisions)

1. **Desktop stack — DECIDED: Electron + TypeScript** (cross-platform; now
   load-bearing for execution, so TS reuse and the single `lol-lcu` adapter matter).
2. **Discord library — DECIDED: discord.py 2.6.x** (`app_commands`,
   `AutoShardedBot`).
3. **Tournament formats — single-elim at launch; double-elim as a gated slice.**
4. **Hosting — must be closed before Phase 0** with a cost model. The
   **always-on HTTPS *callback* requirement is gone**; an **always-on
   authenticated result-report + WS ingress** persists (the 10 clients POST to
   it), so the internet-facing requirement is **not** removed.
5. **Riot key — DECIDED: personal/standard key; Account-V1 (Riot ID → PUUID)
   only.** No Tournament API, no production application, no production review.
   v1 runs custom games autonomously via the LCU; no Riot Web API result path is
   required. Continental routing (`americas|asia|europe`, not `na1/euw1`).
   *Caveat:* a public/monetized launch would force a production key (re-introducing
   unwanted Tournament-API eligibility) — so v1 stays free-only/private.
6. **Auth model — DECIDED: Discord OAuth as sole IdP**, per-(user,guild) roles +
   a distinct platform-operator role.
7. **Monetization — DECIDED: free-only v1.** Keeps a personal key legally
   sufficient and avoids tournament-policy enforcement code.
8. **Referee bot — DECIDED: DEFERRED** (heavy infra + highest ToS ban risk);
   future integrity upgrade only.

---

## 9. Key Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **No independent in-lobby witness (v1)** — every result/identity/stat report comes from a participant's own LCU + unauthenticated `:2999`; all 10 reporters are adversarial and all bytes forgeable. NO neutral observer, NO Riot-signed record. | Forged/colluded results; a ≤5-player side can submit internally-consistent fakes | (a) Quorum requires agreement on a pre-bound gameId with **both teams represented** (host is NOT special — it is a participant); (b) nonce+session-token freshness defeats naive replay/unauth fabrication; (c) kill-stream cross-correlation + anomaly pipeline (missing/late, mismatched gameId, asymmetric winner, byte-identical payloads, KDA-vs-kill-stream) feed (d) bounded admin override (final). Report pipeline keyed by gameId+reporter-PUUID, source-weighted, so a future **referee-bot witness** slots in as a high-weight corroborator — the primary integrity UPGRADE path. |
| **Both-team collusion (≥1 client per side cooperating)** | Defeats quorum + kill-stream checks | Accepted residual: resolved only by admin override; surfaced via dispute queue + rate limits; future referee-bot. |
| **gameId substitution / two-games / wrong-game** | Result attributed to a different custom game | Multi-client gameId capture at start; unanimous gameId agreement; reject duplicate/prior-used gameId; named accepted residual pending referee-bot. |
| **Host = single point of failure; NO LCU custom-lobby host migration** | Host disconnect tears down the lobby, drops all 9; match stalls | Deterministic server-seeded host succession (opted-in only); promote-before-planned-shutdown; full re-create on unplanned DELETE; phase-gated, **epoch-fenced** state machine; heartbeat watchdog; bounded attempts → manual fallback; failover audited. |
| **Split-brain / double-lobby under partition** | Two live lobbies, players split | `host_epoch` lease (conditional-update single-flight); create fenced by `(match_id, host_epoch)`; client self-aborts on stale epoch; grace window before promote; orphan-lobby reaping on reconnect; canonical-lobby reconciliation + anomaly. |
| **Dead-lobby stall** (host hung, no DELETE reached guests; backend unaware) | Players stuck on play screen; match silently never resolves | Host heartbeat + per-match arq watchdog + guest DELETE-event corroboration; SLO/alert on "match stuck in AWAITING_CREATE/LOBBY_OPEN past T". |
| **Failover firing after game start** | Wrongly tears down / duplicates a live match | Phase-gated: `IN_GAME` failover is a hard NO-OP; confirm gameflow not advanced before any re-create. |
| **10-clients-online dependency** (start probability ≈ p^10; capture also needs ~10 clients) | Large fraction of matches stall on a missing client; missed captures lower quorum | Two-stage readiness gate (10/10 green precondition; host green hard-required); missing-client state machine (grace → DM nudge → pre-registered sub → walkover/forfeit → admin); capture-race tolerance (≥6/10, both teams); autonomy SLO + alerts. |
| **LCU undocumented / schema drift each patch** | Silent breakage of create/invite/start/eog parsing; no Riot fallback to catch it | Single `lol-lcu` adapter pinned per patch; CI fixture validation + Swagger mirror; schema-version stamp on every report (disagreement = anomaly); **runtime parse-success-rate monitor + alert**; fail-closed scoped (fleet-wide → admin; single client → degraded vote); forced auto-update + global kill-switch to manual lobbies. |
| **LCU write-automation ToS exposure** (create/invite/auto-accept/start on real accounts) | End-user account/hardware-ban risk (moderate/low-tail, uninsurable) | Writes scoped to lobby/setup only (NO gameplay/value automation, NO matchmaking); per-session default-off opt-in + presence gate (no AFK action); human-paced/rate-capped, event-driven; own-client-only via lockfile; Developer-Portal registration of write endpoints; consent record + "not endorsed by Riot" + residual-risk disclosure; Korea excluded; referee bot deferred (would put writes on a sacrificial account). |
| **Multi-tenant isolation bugs** | Cross-guild data/PII leak | RLS (FORCE, GUC-scoped, shared-to-central exception) + query scoping + CI cross-guild & pooled-connection negative tests. |
| **RLS + pooled-connection (pgbouncer) leak** | Tenant context bleeds across requests | `SET LOCAL` inside the request transaction; non-BYPASSRLS app role; separate privileged migration/federation role; explicit test. |
| **Central-feed inbound abuse** | Spam/impersonation in flagship server | Publish-request + staff approval; field-whitelist projection; `AllowedMentions(false)` + markdown escaping; per-guild publish rate limit. |
| **Redis outage** | Stalled orchestration; lost watchdog timers | AOF persistence; arq durability; degraded-mode rules; managed/HA Redis at scale (deferred). |
| **Smurfing / identity fraud** | Unfair tournaments | PUUID-bound Account-V1 linking (one-time) + invite-by-verified-PUUID **soft** lobby gate + post-game PUUID-vs-roster cross-check across the corroborated reports; mismatches are anomaly-flagged for admin override. **No client-side roster control is authoritative.** |
| **WebSocket desync on deploy/reconnect** | Stale live state / lost commands | Telemetry stays best-effort (rehydrate from Postgres); **commands get a reliable lane** (targeted delivery, per-command ACK, idempotency keys, timeouts/backoff) with a durable Postgres+arq per-match state machine surviving reconnects/redeploys. |
| **Discord 100-server verification gate** | Bot can't join new guilds | Privacy policy + Stripe identity submitted at ~75 servers. |
| **Residential control-plane fragility** (sleep, lockfile rotation on patch, AV/firewall blocking localhost HTTPS) | Support tickets; mid-match drops | WS reconnect/resume with state recovery; detect+re-read lockfile rotation; pin `riotgames.pem`, scope verify-disable to 127.0.0.1; desktop pre-flight self-test with remediation path. |

---

## 10. Immediate Next Steps

1. State **team size** and confirm **§8.4 hosting** with a cost model (always-on
   result-report + WS ingress; **no** Riot callback ingress).
2. Register the **Riot Developer Portal product** for a **personal Account-V1
   key** (no production-key clock); draft the **privacy policy**, host `riot.txt`.
3. Scaffold the monorepo incl. the **worker** service, shared base images, and the
   **`lol-lcu` adapter skeleton + CI fixture-validation harness**; stand up
   backend + worker + Postgres (RLS) + Redis via `docker compose up`.
4. Run the **re-scoped LCU-write ToS spike** with Riot DevRel (distributed,
   cross-account, unattended auto-accept/auto-start at tournament cadence; Korea
   excluded) and register the write endpoints — **gate beta on the outcome.**
5. Build the **LCU lobby-orchestration prototype** (create → batched invite →
   event-driven auto-accept → epoch-fenced promote/re-create failover → manual
   fallback) and the **10-client corroboration/anomaly prototype** against an
   adversarial fixture corpus.
