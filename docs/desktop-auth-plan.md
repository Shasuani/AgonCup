# Desktop Auth & Onboarding — Implementation Plan

Bot-pairing auth (no OAuth consent screen) + persistent reauth + 2-step linking
with an LCU status indicator. Supersedes the anonymous, code-as-link `verify-desktop`
flow by **splitting it into two clean steps**.

## Design (agreed)

The trust anchor is the **Discord bot**, not OAuth — Discord already authenticates
the user; a slash command proves their `discord_user_id`. The user copies a
one-time **pairing code** into the desktop (device-grant style), which the desktop
exchanges for our **own** session (JWT + rotating refresh token). No `discord.com`
permission screen.

Three concerns, kept separate:

| Concern | Mechanism |
|---|---|
| **Authentication / register** | Bot pairing code → backend mints JWT + refresh |
| **Session / reauth** | Rotating refresh token in OS keychain → silent refresh |
| **Account linking (Riot)** | LCU read → Account-V1 resolve, as an **authenticated** call |

### End-user flows

- **New user (register):** open app → "Sign in" → run `/register` in Discord → bot
  DMs a code → paste in app → **paired + registered** (User row already created by
  the bot). Then step 2 below.
- **Link (step 2):** app shows LCU status 🟢 → "Verify with League" → reads local
  Riot ID READ-ONLY → authenticated verify → `RiotAccount` linked.
- **Reopen:** boot → silent refresh → straight in. No action.
- **Access JWT expired (~15m):** silent `/auth/refresh` (proactive on `exp`, or
  reactive on 401 / WS close `4401`).
- **Refresh expired/revoked (30d+ idle):** one "Sign in" → new code. **Never**
  re-register; Riot/PUUID link stays.

## What already exists (reuse, don't rebuild)

- `RefreshToken` model + `issue_refresh_token` / `rotate_refresh_token` /
  `revoke_refresh_token` / `revoke_family` (`auth/tokens.py`); 30d TTL, hashed,
  rotation + reuse/theft detection. `mint_access_token` (`auth/jwt.py`), 15m.
- `POST /auth/refresh` (`auth/router.py`) → `TokenPair`.
- One-time code mint + store: bot `cogs/register.py` → `integrations/discord/router.py`
  → `accounts/service.py` (`DesktopRegistrationResult`), stored in
  `VerificationAttempt` (code hashed, TTL, single-use, carries `user_id` +
  `origin_guild`).
- PUUID resolve + dedupe + rank snapshot logic in `accounts/service.py`
  (`verify_desktop_code`).
- `ClientSession.lcu_reachable` / `gameflow_phase` / heartbeat fields.
- Desktop: `lcu/readSummoner.ts`, `lcu/verify.ts`, `lockfile.ts`, WS token query
  param (`transport/wsTransport.ts`), IPC bridge (`main.ts`, `preload.cts`).

## Build order

### Phase A — Backend: split pairing from linking

1. **Pairing endpoint** — `POST /auth/desktop-pair` (or `/accounts/pair`), anonymous,
   rate-limited. Body `{ code, device_label? }`. Reuse the `VerificationAttempt`
   code validation (pending + unexpired + single-use → mark consumed), read its
   `user_id`, then `mint_access_token` + `issue_refresh_token`. Return a `TokenPair`
   (+ minimal user/account summary so the app can show state). The code's role is
   now **session bootstrap**, not Riot link.
2. **Authenticated LCU verify** — convert `verify-desktop` → `POST /accounts/verify-lcu`,
   **JWT-authenticated** (principal = user). Body `{ game_name, tag_line, region,
   lcu_puuid?, summoner_id? }` (no `code`). Reuse the existing PUUID-resolve +
   dedupe-on-PUUID + best-effort rank-snapshot path, taking the user from the
   principal instead of the code. Returns `DesktopVerifyResponse`. (Keep the old
   code-based endpoint temporarily for back-compat, then remove.)
3. **Token model** — migration `0006`: add `device_label` (+ optional `device_id`)
   to `refresh_tokens` so a user can see/revoke "this desktop" in the SPA.
4. **Refresh hardening** — per-token/IP rate-limit on `/auth/refresh` (currently
   none). PII lifecycle: ensure user-delete / `on_guild_remove` purge revokes the
   user's refresh tokens (`revoke_family`).
5. **Schemas** — `DesktopPairRequest`/`DesktopPairResponse`, authed verify DTO, in
   `lol-schemas`.

**Tests:** pair with valid/expired/reused code (mints tokens / 401); authed verify
links + dedupes on PUUID (409 cross-user); refresh rate-limit; lifecycle revocation.

### Phase B — Desktop: persistent session

6. **Secure storage** — `safeStorage` wrapper (encrypt → file under `userData`;
   decrypt at boot). Never plaintext/localStorage. (Today: zero persistence.)
7. **Boot decision** — replace the `dev-login` stub (`main.ts:~220`): stored refresh
   → `POST /auth/refresh` (silent) → connect; else → pairing screen. Persist the
   **rotated** refresh on every refresh.
8. **Pairing UI** — code input → `POST /auth/desktop-pair` → store tokens → signed in.
9. **Silent access refresh** — on HTTP 401 / WS close `4401`, refresh once and retry;
   on refresh-401 → drop to pairing screen.

**Tests:** storage round-trip (mock `safeStorage`); boot decision (stored→refresh vs
none→pair); silent-refresh-on-401; rotated-token persisted.

### Phase C — LCU status badge + step-2 verification

10. **LCU reachability poller** — every 3–5s: lockfile present + `current-summoner`
    responds → state `online | loading | offline`. Reuse `lockfile.ts` +
    `readSummoner.ts`; this is the same signal already sent in heartbeats
    (`ClientSession.lcu_reachable`). IPC → renderer.
11. **Status icon** — persistent badge: 🟢 online (logged in) / 🟡 loading (lockfile
    but no summoner) / ⚪️ offline (no client). Reusable for host-eligibility
    pre-flight and "WS up but LCU gone".
12. **Verify step** — "Verify with League" enabled **only** on 🟢: read Riot ID →
    `POST /accounts/verify-lcu` (authenticated) → linked; render result + rank.

**Tests:** poller state transitions (lockfile/summoner combinations); verify button
gated on reachability; authed verify wiring (mock backend).

### Phase D — Hardening / polish

13. SPA: "Devices" view listing `device_label` refresh tokens with revoke.
14. Bot `cogs/register.py`: copy update — "paste this code in the desktop app to
    sign in" (it already mints + DMs the code; minimal change). Optional `/link` alias.
15. (Later, Proposal 2) Bot button + deep-link `leagueofleagues://pair?code=…` to
    drop the copy-paste.

## Open item to lock before coding

**Guild scope of the desktop JWT.** `_roles_for` embeds the *first* guild's roles
(`gid`). A player in multiple tournaments needs an **identity-bearing** token while
the WS hub authorizes participation **per-match** via `ClientSession`/RLS,
independent of `gid`. Decide: identity-only desktop token + per-match authz, or
re-mint with the match's guild on subscribe.

## Net effect

Register once via the bot (no OAuth fear) → paste code → link League → done.
Every reopen is silent. The only re-prompt is a new code after 30+ days idle or an
explicit device revoke.
