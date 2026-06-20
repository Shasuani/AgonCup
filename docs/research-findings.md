# LeagueOfLeagues — Research Findings

Standalone research backing the hardened implementation plan. Each section
covers a topic with key facts, implications, risks, and open questions, with a
Sources list of the URLs reviewed. Current as of 2026; figures Riot/Discord set
per-application or change over time should be re-verified live before launch.

---

## 1. Riot Tournament API (running many parallel 16-team brackets)

**Key facts**
- Two flavors, both on the **V5** schema (V4→V5 migrated Oct 11, 2023):
  **Tournament-Stub-V5** (testing; works with a free Development/Personal key,
  mocks the real service) and **Tournament-V5** (real; requires an approved
  **Production key with the Tournaments scope**).
- Flow: register a **Provider** (with a callback URL) → create a **Tournament**
  → generate **codes** (up to 1,000/call) → a player starts a custom game with a
  code → on completion Riot **POSTs a callback** to your URL.
- The callback carries **identifiers only** — `gameId`, `shortCode`, `region`,
  your `metaData`, game settings, `startTime`. It contains **no winner/loser,
  scores, or participants**. To get the outcome + participant PUUIDs you must
  call **Match-V5** (by `gameId`/match id) and/or **`games/by-code`**.
- **One code = one match.** Reusing a code breaks the per-match callback.
- V5 uses **PUUIDs** everywhere (`allowedParticipants` whitelist and the
  `games/by-code` response), not Summoner IDs.
- Code params (`TournamentCodeParameters`): `mapType`, `pickType`
  (`TOURNAMENT_DRAFT`), `spectatorType`, `teamSize` (1–5), `allowedParticipants`
  (PUUID whitelist), `enoughPlayers`, free-form `metaData` echoed in the callback.
- `lobby-events/by-code` provides pre-game readiness events; `games/by-code`
  provides post-game data incl. participant PUUIDs.
- Production-key approval needs a registered product, a **working prototype**, a
  **public website**, and **`riot.txt` domain verification**; review runs
  **~10–20 business days**. Tournament API is **unavailable on Personal/Dev keys**.
- Tournament keys have their own (unpublished, per-application) rate limit;
  honor `Retry-After`/`X-Rate-Limit-Type`; abuse can get the key **revoked**.

**Implications**
- Build/demo entirely on **Stub-V5** first; the stub exercises the full
  provider→tournament→code→callback flow before approval.
- Treat the callback as a **trigger, not a source**: route by `metaData`
  (encode `tournament_id/round/match_id`), then fetch results via
  Match-V5/`games/by-code`.
- Hierarchy: few Providers per region → one Tournament per bracket → one fresh
  `TOURNAMENT_DRAFT` code per game, generated lazily.
- Pre-collect roster PUUIDs (Account-V1) for `allowedParticipants`.
- Implement a **reconciliation poller** (`games/by-code`) for every outstanding
  code; treat callbacks as best-effort. Dedupe by `gameId`/`shortCode`.
- **Apply for the production key early** — it is the critical-path dependency.

**Risks**
- Production-key approval is discretionary and slow/rejectable — biggest launch
  risk. Designs that expect winner/stats in the callback fail. Missed callbacks
  without reconciliation = matches never resolve. Code reuse silently corrupts
  results. Many parallel brackets multiply Match-V5/code-gen calls; ignoring
  429s risks key revocation (all tenants down at once).

**Open questions**
- Exact 2026 numeric rate limit for a Tournaments production key (set per-app).
- Whether `games/by-code`/Match-V5 fully cover all needed stats.
- Any per-provider/per-tournament concurrency caps.
- Whether the V5 callback now includes any team-result field (sample is
  identifiers-only).
- Per-region provider registration scaling for a global platform.

**Sources**
- https://www.riotgames.com/en/DevRel/tournament-v5-is-coming
- https://support-developer.riotgames.com/hc/en-us/articles/22698698001939-League-of-Legends
- https://developer.riotgames.com/tournament-api.html
- https://developer.riotgames.com/apis
- https://riot-api-libraries.readthedocs.io/en/latest/applications.html
- https://hextechdocs.dev/getting-started-with-the-riot-games-api/
- https://hextechdocs.dev/rate-limiting/
- https://github.com/Kelmatou/LeagueAPI/wiki/Tournaments
- https://www.riotgames.com/en/DevRel/summoner-names-to-riot-id

---

## 2. Riot Developer API access: tiers, approval, policy, identity/match endpoints

**Key facts**
- Three tiers: **Development** (auto, expires 24h, ~20 req/s & 100 req/2min),
  **Personal** (registered, no verification, non-expiring, **no rate increases**,
  Standard APIs only — **excludes Tournament API**), **Production** (registered +
  verified + DevRel review; non-expiring; ~**500 req/10s & 30,000 req/10min per
  region**, further increases negotiable).
- Review ~**10 business days** (longer in 2026 reports; limited rejection
  feedback). Reviewers require a working website/prototype, ecosystem/player
  value, and that the product does not "solve" the game.
- **Any product serving players must be registered/audited**, even if it calls
  no documented API; **new features require re-audit**.
- Monetization: **no betting/gambling**; **free player tier mandatory**; paid
  content must be "transformative"; allowed = subscriptions/donations/
  crowdfunding/**tournament entry fees**/non-cash-convertible currency; Approved/
  Acknowledged status required to monetize.
- Integrity/branding: **no de-anonymization**; HTTPS; **never embed keys in
  distributed code**; **one production key per product**; mandatory **"not
  endorsed by Riot Games"** disclaimer; approved assets only.
- Tournament community thresholds: **≥70% of entry fees to prize pool**,
  transparent win conditions, **≥20 participants**, no gambling, sponsor caps
  (~$10k/tournament, ~$100k/yr/org for LoL).
- Identity/match: **Account-V1** (regional routing) `by-riot-id` →
  `puuid`/`gameName`/`tagLine`, `by-puuid`, active-region. **Summoner-V4**
  (platform routing) `by-puuid`; **`by-name` deprecated** post-Riot-ID migration.
  **Match-V5** (regional routing) matchlist-by-puuid, match detail, timeline.
- **CRITICAL:** Riot announced (Oct 2025) it is **deactivating Spectator-V5 for
  LoL** alongside in-game anonymity features to stop de-anonymization. Post-match
  APIs (Match-V5) and leaderboards are unaffected.
- 17 platform IDs → 4 regional clusters (AMERICAS/ASIA/EUROPE/SEA). Rate limits
  have 3 layers (app/method/service) read dynamically from headers.
- **discord.py 2.7.x** (stable) is the standard Discord bot library.

**Implications**
- Two-key path: build on Dev, apply for Production with a live prototype; budget
  ~2+ weeks. **Do not depend on Spectator-V5/live in-game state** — validate from
  Match-V5 only. Make **Account-V1 the onboarding entry point**; persist `puuid`
  as canonical; never store/display summoner names as identity. Correct
  per-cluster vs per-platform routing. Header-driven rate limiter. Policy-
  compliant monetization from day one. Register before serving players; gate new
  features behind re-audit.

**Risks**
- Spectator-V5 removal kills any live-game verification. Approval can exceed the
  nominal window. Monetization missteps (anything gambling-like) are hard
  violations. De-anonymization is strictly enforced. Default limits are far too
  low for scale; method limits can throttle independently. Coupling to deprecated
  fields (summoner-by-name) breaks.

**Open questions**
- Exact effective date/status of the Spectator-V5 deactivation. Whether
  `by-riot-id` behavior changes for anonymized accounts. Verbatim 2026
  production-tier limits and how increases are scoped. Whether real-money entry
  fees need extra Riot approval. Per-method Match-V5 defaults. Whether
  Summoner-V4 `by-name` is fully removed yet.

**Sources**
- https://developer.riotgames.com/docs/portal
- https://support-developer.riotgames.com/hc/en-us/articles/22801383038867-Production-Key-Applications
- https://support-developer.riotgames.com/hc/en-us/articles/22698431229203-Developer-Portal-Overview
- https://developer.riotgames.com/policies/general
- https://support-developer.riotgames.com/hc/en-us/articles/22698591841939-General-Policies
- https://developer.riotgames.com/api-details/account-v1
- https://developer.riotgames.com/apis
- https://darkintaqt.com/blog/routing
- https://github.com/CommunityDragon/HexDocs/blob/master/lol/riotapi/rate-limiting.md
- https://www.riotgames.com/en/DevRel/summoner-names-to-riot-id
- https://x.com/RiotGamesDevRel/status/1979263978787246391
- https://riot-watcher.readthedocs.io/en/latest/riotwatcher/LeagueOfLegends/MatchApiV5.html
- https://discordpy.readthedocs.io/en/stable/

---

## 3. Reading the local League client (LCU + Live Client Data API) & ToS

**Key facts**
- **LCU API:** localhost-only REST/WebSocket from `LeagueClientUx`. Discovered
  via the **lockfile** (`ProcessName:PID:Port:Password:Protocol`); HTTP Basic
  auth `riot:<password>`; **self-signed cert** (trust `riotgames.pem` or scope
  verify-disable to 127.0.0.1). Key endpoints:
  `GET /lol-summoner/v1/current-summoner` (puuid, gameName/tagLine) and
  `GET /lol-gameflow/v1/gameflow-phase` (None/Lobby/ChampSelect/InProgress/…).
- **Live Client Data API:** `https://127.0.0.1:2999/liveclientdata`, **only
  during an active/spectated game**, **no auth**, self-signed cert. Exposes
  live champion stats, gold/level, KDA/CS/wards, items, team/position, isDead,
  game mode/time/map, and an event log — for **all** players incl. enemies.
  No official polling cadence (community: ~1s overlay to ~30s).
- **ToS §7.1** prohibits unauthorized third-party programs that interact with
  Riot Services, intercept/redirect comms, or read memory; penalties incl.
  account and **hardware bans**. Both APIs are **"not officially supported for
  third-party applications"** but tolerated for permitted use cases.
- DevRel **permits**: stat/match-history displays, LFG, tournament tools,
  personal training, static-data overlays. **Prohibits**: session-specific info
  previously unknown to the player, **enemy ult cooldown tracking** (enforced
  with key deactivation since **March 13, 2025**), automation/botting,
  unauthorized MMR/ELO, **de-anonymizing** players, custom match history w/o
  consent.

**Implications**
- Identity validation via `current-summoner` `puuid` is permitted (validate the
  local user's own identity). Watch the lockfile (fallback: `LeagueClientUx`
  args `--app-port`/`--remoting-auth-token`). Pin `riotgames.pem` for 127.0.0.1;
  never globally disable TLS. Drive UI state off `gameflow-phase`; switch to the
  `:2999` API only when `InProgress`/`WatchInProgress`. Keep the client
  **strictly read-only/display-only**; relay only the **local user's own** data;
  never surface enemy-advantage/session-specific data.

**Risks**
- Both APIs are unsupported/undocumented — paths/fields/lockfile format can
  change per patch (defensive parsing needed). `:2999` is down outside a match
  (handle connection-refused). ToS §7.1 is broad — tolerance hinges on staying
  in permitted categories; policy is **tightening** (ult-alert enforcement;
  summoner-spell/jungle timers under review). Broad TLS disable or leaking the
  lockfile token is a user-machine security risk. Exposing others'
  identities/historic Riot IDs violates the de-anonymization rule.

**Open questions**
- Whether showing **all** players' live stats during the user's own game is
  permitted vs only own-team. No published rate/polling guidance for the local
  APIs. Exact 2026 Mac lockfile path / Riot-Client-vs-LCU split. Whether
  registering the product is required for a purely local LCU+Live-Client tool.

**Sources**
- https://hextechdocs.dev/getting-started-with-the-lcu-api/
- https://riot-api-libraries.readthedocs.io/en/latest/lcu.html
- https://github.com/KebsCS/lcu-and-riotclient-api
- https://www.mingweisamuel.com/lcu-schema/tool/
- https://github.com/Plutokekz/LeagueClientLiveDataApi
- https://github.com/oracle-devrel/leagueoflegends-optimizer/blob/main/articles/article5.md
- https://www.riotgames.com/en/terms-of-service
- https://support-developer.riotgames.com/hc/en-us/articles/22698698001939-League-of-Legends
- https://developer.riotgames.com/policies/general
- https://esports-news.co.uk/2025/05/30/captainflowers-riot-games-lol-third-party-apps/

---

## 4. Multi-guild (multi-tenant) Discord bot in Python (mid-2026)

**Key facts**
- **discord.py 2.6.x** (actively maintained by Rapptz), `app_commands.CommandTree`.
- **Privileged-intents** access now gated by a **10,000-unique-user** threshold
  (effective **June 10, 2026**) — not the old 100-server rule. Below that, toggle
  in the portal. Once notified, 90 days to apply; granted access re-applied
  annually. The three privileged intents (MEMBERS/PRESENCE/MESSAGE CONTENT)
  require both the portal toggle and code. Guild lifecycle uses the
  **non-privileged `guilds` intent**.
- **Slash command sync:** global commands propagate up to ~1h; guild commands
  near-instant. Sync in `setup_hook()`, **not on every boot** (≤~200 global
  updates/day); use an owner-only manual sync.
- **Sharding** required at **2500+ guilds**; ~1000/shard recommended;
  `AutoShardedClient/Bot` auto-derives shard count from Get Gateway Bot.
- **REST limits:** 50 req/s global + per-route buckets (handled by the library);
  Cloudflare invalid-request ban at **10,000 invalid/10min/IP**.
- **OAuth2:** authorization-code grant; scopes `identify` (+`guilds`/
  `guilds.members.read`/`email`); always use the **`state`** CSRF param; refresh
  via `refresh_token`.
- **Verification** (separate from intents): required past **100 servers**
  (hard-stop); notice ~75 servers; Stripe identity check + **public privacy
  policy** describing collection/retention/deletion; takes a few days.

**Implications**
- `AutoShardedBot` from day one (free; covers well past threshold; no clustering
  in v1). Run on the **`guilds` intent only** + slash commands to avoid the
  privileged-intent review entirely. Per-guild config in **Postgres** keyed by
  `guild_id`; clean up on `on_guild_remove`. Rely on the library's bucketed rate
  limiting. OAuth2 auth-code + `state`; encrypt/refresh tokens; minimal scopes.
  Publish a privacy policy + prepare Stripe verification at the ~75-server notice.

**Risks**
- Hitting **100 servers unverified** hard-stops growth. Crossing 10,000 users
  with privileged intents starts a 90-day clock (generic justifications
  rejected). Over-requesting intents inflates review burden. Auto-syncing risks
  the ~200/day limit. Single-process AutoSharded doesn't scale past one machine
  (clustering eventually needed). In-memory/flat-file config breaks under
  sharding. OAuth token storage is sensitive PII. Annual intent re-apply is easy
  to forget.

**Open questions**
- Whether the bot needs any privileged intent (likely **no** with slash-only +
  `guilds`). Expected scale (toward 2500 guilds/10,000 users?). DB/hosting
  choice and whether clustering/IPC is in v1 scope. Exact OAuth scope set.

**Sources**
- https://docs.discord.com/developers/topics/oauth2
- https://docs.discord.com/developers/topics/gateway
- https://docs.discord.com/developers/topics/rate-limits
- https://docs.discord.com/developers/gateway/getting-started-with-privileged-intent-review
- https://support-dev.discord.com/hc/en-us/articles/40281523410967-Changes-to-Privileged-Intent-Access-for-Discord-Apps
- https://support-dev.discord.com/hc/en-us/articles/23926564536471-How-Do-I-Get-My-App-Verified
- https://discordpy.readthedocs.io/en/latest/intents.html
- https://discordpy.readthedocs.io/en/stable/api.html
- https://github.com/Rapptz/discord.py/blob/master/examples/app_commands/basic.py
- https://github.com/Rapptz/discord.py/discussions/9549
- https://about.abstractumbra.dev/discord.py/2023/01/29/sync-command-example.html
- https://libraries.io/pypi/discord.py
- https://interactions-py.github.io/interactions.py/changelog/
- https://interactions-py.github.io/interactions.py/Guides/80%20Sharding/

---

## 5. Bracket engine + async Python architecture (FastAPI + Postgres + Redis + WS)

**Key facts**
- **Bracket engine:** pad to next power of two; `byes = next_pow2 − N`, awarded
  to top seeds via **mirror/fold** seeding; model **walkover/forfeit as a match
  result**, not a bracket shape. **brackets-manager.js** (MIT) is the best
  open-source reference (single/double elim, round-robin; byes at creation,
  forfeits at update; match states locked→waiting→ready→running→completed). For
  N≤16 a small deterministic Python generator suffices.
- **Background work:** FastAPI's own docs say **BackgroundTasks are for small
  in-process work**; heavier jobs (retries/persistence/multiple workers) need a
  Celery-class tool. **arq** (asyncio, Redis-backed, ~700 LOC) fits: custom
  **`_job_id`** uniqueness (idempotency/dedupe), `@cron`, deferred jobs,
  exponential-backoff `Retry`, **at-least-once ("pessimistic") execution** — jobs
  **will** re-run, so they must be re-execution-safe.
- **Idempotency:** deterministic `_job_id` on `(tournament_id, riot_match_id)`;
  `UNIQUE(tournament_id, riot_match_id)`; **UPSERT/reconcile**, not blind insert;
  dedupe Riot callbacks by `gameId`/`shortCode`. Run a **`@cron` reconciliation**
  that re-polls and converges.
- **Multi-tenancy:** for the first few hundred tenants, **single-schema +
  Postgres Row-Level Security** keyed by `guild_id` is the optimal balance —
  RLS makes every statement tenant-aware at the DB level; schema-per-tenant adds
  migration/operational complexity with no benefit. Set the tenant via a
  per-request GUC; **pgbouncer transaction mode + `SET LOCAL`** must be matched
  or tenant context leaks. RLS is easy to get subtly wrong (forgotten policy,
  BYPASSRLS roles, unset GUC).
- **Riot rate limits** reaffirmed: dev 20 req/s & 100/2min; prod ~500/10s &
  30,000/10min per region; 3 tiers; `Retry-After`/`X-Rate-Limit-Type`; blacklist
  risk on persistent 429s / non-existent-resource requests. **Tournament API
  requires a production key.**
- **discord.py 2.x:** interactions must respond within 3s (else defer); token
  valid 15min; manual sync; ~200 command updates/day.

**Implications**
- Build a deterministic N≤16 generator; persist a match graph with explicit
  next-match links; model results as `{normal, walkover, double-forfeit}`.
- **Adopt arq** for all Riot polling/validation on the existing Redis as a
  **separate process/container**; deterministic `_job_id`; UNIQUE + UPSERT;
  `@cron` reconciliation; shared Redis token-bucket honoring `Retry-After`.
- **Single-schema RLS** with a per-request guild GUC set in a DB-session
  dependency; same discipline in worker sessions; route durable state through
  the DB and broadcast via **Redis pub/sub** so reconnecting clients rehydrate.
- Defer interactions; push live state over WS/Redis pub-sub; manual `/sync`.
- **Acquire the production key early** (scheduling dependency).

**Risks**
- Production-key approval blocks launch. Persistent 429s/non-existent-resource
  requests → progressive then permanent blacklisting. arq at-least-once →
  non-idempotent result writes double-advance brackets. RLS misconfig (forgotten
  policy, BYPASSRLS, unset/leaked GUC on pooled connections) crosses tenants. WS
  state desyncs from the DB if pushed from workers without a single source of
  truth. Porting brackets-manager's double-elim loser-bracket routing is the
  most error-prone part (cover with N=3..16 table-driven tests).

**Open questions**
- Tournament API vs Match-V5-by-puuid for validation (former authoritative but
  production-key-gated). Expected guild/tenant + concurrent-tournament scale.
  Whether double-elim is required at launch. Which regions simultaneously
  (per-region limits/sharding). Human-confirmable results + conflict
  reconciliation. Single vs multi-host deployment.

**Sources**
- https://github.com/Drarig29/brackets-manager.js/
- https://www.npmjs.com/package/brackets-manager
- https://bracketdraw.com/single-elimination
- https://miniwebtool.com/random-tournament-bracket-generator/
- https://www.bracketsninja.com/types/single-elimination-bracket
- https://developer.riotgames.com/docs/portal
- https://developer.riotgames.com/apis
- https://hextechdocs.dev/rate-limiting/
- https://fastapi.tiangolo.com/tutorial/background-tasks/
- https://arq-docs.helpmanual.io/
- https://www.midnytecity.com.au/blogs/multi-tenant-databases-with-postgres-row-level-security
- https://propelius.tech/blogs/multi-tenant-database-isolation-postgresql-rls-schema/
- https://www.pythondiscord.com/pages/guides/python-guides/app-commands/
- https://gist.github.com/AbstractUmbra/a9c188797ae194e592efe05fa129c57f

---

## Cross-cutting takeaways

1. **Result authority lives on Riot's servers** (Tournament-V5 callback trigger →
   Match-V5/`games-by-code`). No client-supplied data is authoritative.
2. **The production key (Tournaments scope) is the critical path** — apply in
   parallel from Phase 0/1; build on Stub-V5 meanwhile.
3. **Spectator-V5 is gone for LoL**; de-anonymization is prohibited.
4. **A durable worker tier (arq) + idempotency + reconciliation** are mandatory,
   not optional.
5. **Postgres RLS** (with the pgbouncer/`SET LOCAL` discipline and a consented,
   projected central feed) is the tenant-isolation backstop.
6. **Discord verification (100-server) and privileged-intent review (10,000-user)
   are independent gates**; the bot can avoid the latter with `guilds`-only intent.



---

## Autonomous LCU Model — Research (v3)

This section documents the research underpinning the v3 pivot **away from Riot
Tournament-V5** to an **autonomous, desktop-client-driven (LCU) custom-game
model**. It supersedes the v2 "Cross-cutting takeaway #1" (*"Result authority
lives on Riot's servers"*) and #2 (*"the production key is the critical path"*),
both of which are now **false** under the locked autonomous direction.

### Why Tournament-V5 was removed (the load-bearing finding)

- **Custom games 404 on Match-V5.** `GET /lol/match/v5/matches/{matchId}` returns
  **404 for custom game IDs** (Riot dev-relations issue #472; v4 returned them, v5
  does not), even though they remain visible in the official match-history UI. A
  no-Tournament-V5 design therefore has **no server-side authoritative result at
  all** — only player attestations. Tournament-V5's `games-by-code` was the only
  supported route to custom results, and it is explicitly out of scope. **Feasibility of a Riot-signed result path under the locked model: not feasible.**
- **Consequence:** the v2 integrity model ("only Riot data is authoritative") is
  **structurally impossible** in v3. Integrity must rest on collusion-resistant
  cross-corroboration + anomaly detection + bounded admin review.

### Account-V1 linking only (identity) — feasibility: **likely**

- **PUUID is the stable, canonical, region-independent identity** (encrypted, ≤78
  chars, tracks across region changes). Summoner name is **deprecated/mutable**
  (stale since 2023-11-20; new accounts get a random uuidv4 internal name) and
  **must not** be an identity key.
- **Riot ID → PUUID:** `GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}`
  on a **continental cluster only** (`americas|asia|europe`, **not** `na1/euw1`);
  one cluster suffices (global data).
- **A personal/dev key is sufficient** (20 req/s, 100 req/2min) for Account-V1 +
  minimal Summoner-V4. **A production key is only needed to unlock the Tournament
  API** (deliberately avoided) — so **no production review is required.** Caveat:
  a personal key may **not** legally back a *public* app; a public/monetized
  launch forces a production key (re-introducing unwanted Tournament eligibility),
  so v1 stays free-only/private.
- **Ownership proof:** server-issued nonce challenge — generate a nonce bound to
  the claimed PUUID, have the user display it (a designated `profileIconId` read
  back server-side via Summoner-V4 `by-puuid`), then bind the verified PUUID. LCU
  `current-summoner` PUUID / `PUT /lol-chat/v1/me` status are **advisory secondary
  signals only** — client-asserted PUUIDs are spoofable.

### LCU custom-game lobby lifecycle (autonomous run) — feasibility: **likely**

- **Transport:** local REST+WebSocket at `https://127.0.0.1:{port}`; HTTP Basic
  auth (`riot`:`<lockfile password>`), self-signed cert (TLS verify relaxed),
  per-launch lockfile (port+password rotate every client start). Local-only ("not
  even on your home network").
- **Full lifecycle exists and can drive a 5v5 end to end** (all **community-RE,
  unsupported** unless noted):
  - Create: `POST /lol-lobby/v2/lobby` with `isCustom:true`,
    `customGameLobby.configuration` = `{gameMode:"CLASSIC", mapId:11, teamSize:5,
    mutators:{id:1}, spectatorPolicy:"AllAllowed"}`, `lobbyName`, `lobbyPassword`.
    (CLASSIC, not PRACTICETOOL; mapId 11 = Summoner's Rift; teamSize 5.)
  - Invite (host): `POST /lol-lobby/v2/lobby/invitations` body `[{toSummonerId}]`
    — accepts an **array** (batch all 9 in one call).
  - Auto-accept (guest): subscribe `OnJsonApiEvent_lol-lobby_v2_received-invitations`
    (poll `GET /lol-lobby/v2/received-invitations` fallback) → `POST .../{id}/accept`.
  - Teams/bots: `POST /lol-lobby/v1/lobby/custom/switch-teams`, `.../bots`
    (teamId 100/200).
  - Host control: `.../members/{summonerId}/kick`, `.../promote` (host transfer),
    `.../grant-invite|revoke-invite`.
  - Start: `POST /lol-lobby/v1/lobby/custom/start-champ-select`.
  - Programmatic join exists (`GET /lol-lobby/v1/custom-games` → `POST
    .../{id}/join` with password in body) but join is **by id-from-listing, not by
    free-text name**, and the **password body schema is community-inferred/
    unverified**. **Decision: not used in v1** — invite-based only; manual fallback
    = human joins by name+password in the native UI.
- **No host migration for custom lobbies (the biggest reliability gap).** Host
  loss **destroys the lobby**; all members get a `DELETE` lobby event and are
  dropped to the play screen. Ownership transfers **only** via an explicit
  `promote` call **while the host is still connected**. Therefore: planned shutdown
  = pre-emptive `promote`; **unplanned crash = full re-create + re-invite** (not
  recoverable). **Feasibility of true automatic failover: NOT achievable — only re-creation is.**
- **DELETE is ambiguous** (fires on host crash AND normal game launch) → drive
  transitions off `GET /lol-gameflow/v1/gameflow-phase`, treat DELETE as advisory.
- **Schema fragility:** Riot removed the built-in OpenAPI/help spec; endpoint
  **names** are stable across years but request/response **schemas drift between
  patches**; community Swagger mirrors **lag** the live client. Centralize behind
  one adapter; pin a patch; validate in CI.

### Result capture + corroboration — feasibility: **likely (but weak integrity)**

- **Two local sources:** (1) **Live Client Data API** `:2999/liveclientdata/*`
  (**Riot-official, documented**, local-only, unauthenticated; port open only
  while `InProgress`, dies on game exit) — `eventdata` carries a per-kill stream
  (`KillerName/VictimName/Assisters/EventTime`, `GameStart→GameEnd`); win/lose is
  per-active-player and the `GameEnd` Result field is community-RE. (2) **LCU**
  `GET /lol-end-of-game/v1/eog-stats-block` (**RE/unsupported**) — root `gameId`,
  per-team `isWinningTeam` (full-game, not per-perspective), per-player
  `CHAMPIONS_KILLED/NUM_DEATHS/ASSISTS`.
- **Forgeability (HIGH):** both run on the user's machine behind a self-signed
  cert with user-controlled credentials. Every byte is **unsigned and trivially
  spoofable** (local proxy/MITM, patched lockfile, fabricated JSON). **No Riot
  signature exists anywhere.**
- **Collusion (HIGH):** a 5-player side can submit internally-consistent fakes;
  naive 10-way majority is gamed by ≥6 colluders. **The both-teams-represented
  rule is the primary control** (max single-side faction = 5, can never represent
  both sides). A both-team collusion defeats the scheme by design → admin override.
- **Capture race (MEDIUM):** `eog-stats-block` clears on next champ-select/game and
  is not retained across restart; `:2999` dies on game exit. Reports must be POSTed
  in a **seconds-to-minutes window**. Missing reports lower quorum.
- **gameId binding (MEDIUM):** the same numeric `gameId` appears across all 10
  clients, enabling correlation — **but the gameId→tournament-match binding must be
  established out-of-band**; the local client cannot prove which tournament a game
  belongs to. Residual gap (no trusted observer) is an **accepted, named risk**
  pending the deferred referee-bot. Hardening: multi-client gameId capture,
  unanimous agreement, reject duplicate/prior-used gameId.
- **Corroboration scheme (locked):** auto-accept on **≥6/10 identity-bound reports
  agreeing on gameId + winner with both teams represented and no high-severity
  anomaly**; tolerate ~4 honest-missing; below floor / on conflict → **admin
  override**. Kill-stream cross-correlation is a first-class corroboration signal
  (held-as-disputed, never auto-rejected, on contradiction). A **single-use,
  short-TTL nonce per (match_id, puuid) + authenticated PUUID session token**
  hardens freshness/auth (defeats naive replay + unauthenticated single-machine
  fabrication) but **does not defeat collusion or make content true.**

### LCU **write**-automation ToS / ban exposure — feasibility: **likely (moderate/low-tail, uninsurable)**

- **Read vs. write line maps onto Riot's prohibition.** Passive reads sit in a
  tolerated-but-unsupported grey zone. Riot's **conduct-based** prohibition targets
  "taking actions on your behalf through botting or scripting" (scripting =
  "automated actions… on their behalf"; botting = generating account value).
- **Lobby-setup writes (create/invite/auto-accept/start) fit neither definition
  cleanly** when user-initiated and conferring no in-match/MMR advantage — **far
  less exposed** than aim/dodge scripts or value-farming bots. A large ecosystem of
  published tools (LeagueAutoAccept, Hextech Friends mass-invite, RuneChanger,
  KBotExt) performs identical writes with **no visible ban pattern**.
- **But:** no allow-list ("absolutely no allow list for Vanguard"), no safe harbor,
  Riot can revoke/break **at will**. Penalties for confirmed scripting/botting reach
  **permanent suspension and can extend across linked accounts / toward hardware
  bans** under Vanguard. **Risk scales with: acting without the human present, high
  action rates, anything touching ranked/matchmaking value, gameplay-assist
  resemblance.**
- **Distributed + cross-account is the dimension precedent tools do NOT cover:** v3
  drives writes on up to **10 community members' own accounts per match**, and
  **auto-accept fired while AFK** is the closest match to "acting on your behalf."
  Mitigations the research mandates: **human-in-the-loop posture** (per-session,
  default-off opt-in + presence gate — not autopilot-while-absent), human-paced
  rate-limited event-driven writes, own-client-only via lockfile, Developer-Portal
  registration of the **write** endpoints, explicit consent + disclosure, and a
  **hard Korea exclusion**. Vanguard (kernel anti-cheat) primarily targets memory
  reading/injection; **local HTTP LCU calls "should continue functioning"** but are
  unsupported.

### Feasibility summary

| Capability | Rating | Key caveat |
|---|---|---|
| Account-V1 one-time linking (no Tournament API) | **likely** | personal key only; public launch forces production key |
| LCU custom-lobby lifecycle (create/invite/start) | **likely** | all RE/unsupported; schema drift each patch |
| Automatic host failover | **not achievable as "migration"** | no host migration; only `promote` (planned) or full re-create (crash) |
| Local result capture (eog + Live Client Data) | **likely** | tight capture race; `:2999` documented, eog RE |
| Result *integrity* from local sources | **weak** | all forgeable; integrity = quorum + both-teams + anomaly + admin override |
| LCU write-automation within ToS | **likely, moderate/low-tail** | uninsurable; distributed/cross-account/unattended raise risk |

### Risks (net-new under the autonomous model)

1. No independent in-lobby witness; 100% of result evidence is adversarial.
2. Both-team collusion defeats quorum + kill-stream (admin-override only).
3. gameId substitution / two-games / wrong-game (no out-of-band observer).
4. Host = single point of failure; no LCU host migration; lobby teardown on drop.
5. Split-brain double-lobby under partition (fence with `host_epoch`).
6. 10-clients-online dependency (start ≈ p^10; capture race lowers quorum).
7. LCU schema drift every patch with **no Riot fallback** to catch a silent
   parser regression (monitor parse-success rate; fail closed scoped).
8. LCU write-automation account/hardware-ban exposure (moderate/low-tail).

### Open questions to verify live before relying on them

- Exact `eog-stats-block` clear trigger (next champ-select? next game? restart?).
- 2026 `:2999` `GameEnd` JSON — does it carry `Result:'Win'/'Lose'` for all 10 or
  only the active player?
- Whether `received-invitations` exposes a custom-lobby vs party/queue discriminator
  field (auto-accept currently filters on inviter PUUID == designated host).
- `POST /lol-lobby/v1/custom-games/{id}/join` password body field (not used in v1).
- Current Riot stance on **distributed, registered LCU write-automation** of
  custom-lobby setup (the Phase-0/1 DevRel spike).

### Sources

**Tournament-V5 / Match-V5 non-viability for customs**
- Riot dev-relations #472 — customs 404 on Match-V5 `/lol/match/v5/matches/{id}` — https://github.com/RiotGames/developer-relations/issues/472

**Account-V1 linking (identity, no Tournament API)**
- Riot DevRel — Summoner Names to Riot ID (PUUID canonical, name deprecation, by-riot-id) — https://www.riotgames.com/en/DevRel/summoner-names-to-riot-id
- Riot Developer Portal — Summoner Name to Riot ID FAQ — https://developer.riotgames.com/docs/summoner-name-to-riot-id-faq
- Riot Developer Portal — Portal/Policies (key types, rate limits) — https://developer.riotgames.com/docs/portal
- auxermen/lol-messaging — PUT /lol-chat/v1/me {statusMessage} — https://github.com/auxermen/lol-messaging
- Hextechdocs — Rate Limiting — https://hextechdocs.dev/rate-limiting/

**LCU custom-game lobby lifecycle**
- League Client APIs — Riot Developer Portal (unsupported; register usage) — https://developer.riotgames.com/league-client-apis.html
- New League Client API Policy | Riot Games — https://www.riotgames.com/en/DevRel/new-league-client-api-policy
- LCU — The League Client (Hextech Docs) — custom lobby create JSON — https://hextechdocs.dev/lcu-the-league-client/
- LeagueCustomLobby wiki: lol-lobby endpoints (invitations, switch-teams, start-champ-select, kick, promote, custom-games) — https://github.com/XHXIAIEIN/LeagueCustomLobby/wiki/lol-lobby
- lcu-driver create_custom_lobby.py example — https://github.com/sousa-andre/lcu-driver/blob/master/examples/create_custom_lobby.py
- Mimic web/src/components/lobby/lobby.ts (promote, kick, grant/revoke-invite, leave) — https://github.com/molenzwiebel/Mimic/blob/master/web/src/components/lobby/lobby.ts
- League of Legends LCU Docs (vivide.re) — received-invitations accept/decline, custom-games/{id}/join, eligibility — https://lcu.vivide.re/
- CommunityDragon awesome-league (LCU tooling, Swagger mirrors) — https://github.com/CommunityDragon/awesome-league
- Riot API Libraries — LCU — https://riot-api-libraries.readthedocs.io/en/latest/lcu.html

**Result capture + corroboration (LCU + Live Client Data)**
- Riot Developer Portal — League of Legends docs (Live Client Data API, :2999, self-signed cert) — https://developer.riotgames.com/docs/lol
- Live Client Data API allgamedata sample JSON — https://static.developer.riotgames.com/docs/lol/liveclientdata_sample.json
- Jonchun/lige liveclientdata Go package — Event struct — https://pkg.go.dev/github.com/Jonchun/lige/liveclientdata
- Overwolf LoL game events — matchEnd deprecated; use VICTORY/DEFEAT or live_client_data — https://overwolf.github.io/api/games/events/league-of-legends
- GitHub gist (xadamxk) — eog-stats-block response (gameId, isWinningTeam, KDA) — https://gist.github.com/xadamxk/8b3c795d98773d0879eb1b02742bee0e
- Hextechdocs — Getting started with the LCU API (lockfile, Basic auth, unsupported, local-only) — https://hextechdocs.dev/getting-started-with-the-lcu-api/
- KebsCS/lcu-and-riotclient-api — community LCU + Riot Client API docs index — https://github.com/KebsCS/lcu-and-riotclient-api
- leagueclientlivedata (PyPI) — async :2999 wrapper — https://pypi.org/project/leagueclientlivedata/

**LCU write-automation ToS / ban exposure**
- Riot Developer Portal — API Terms and Conditions — https://developer.riotgames.com/terms
- Riot Developer Portal — General Policies (no unfair advantage; register LCU products) — https://developer.riotgames.com/policies/general
- Riot Games — Changes to the LCU API Policy (notify Riot; approved-endpoint list; Korea restriction) — https://www.riotgames.com/en/DevRel/changes-to-the-lcu-api-policy
- Riot Games — Vanguard FAQ for Third Party Applications — https://www.riotgames.com/en/DevRel/vanguard-faq
- League of Legends — /dev: Vanguard x LoL — https://www.leagueoflegends.com/en-us/news/dev/dev-vanguard-x-lol/
- Riot Support — Third Party Applications (scripting/botting definitions; permanent suspension) — https://support.riotgames.com/en-us/riot/events/third-party-applications
