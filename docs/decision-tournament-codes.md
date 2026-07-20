# Decision: Riot **Tournament Codes** are the sanctioned match path

**Date:** 2026-07-20 · **Status:** ❌ REJECTED by owner (2026-07-20) — **custom
lobbies only** (stay on the autonomous-LCU path). Kept as a research record; do NOT
re-raise the pivot unless the owner reopens it.
**Author's note:** research-driven decision requested by the project owner
("κάνε έρευνα και αποφάσισε"). Owner reviewed and chose to **keep custom-only**.

---

## 1. TL;DR

The project's match-running core was built on the premise **"there is no Riot
Tournament API — Match-V5 404s on customs, so we must run lobbies autonomously via
the local LCU and corroborate forgeable results."** Research shows that premise is
**too broad and partly mistaken.**

- **Riot's Tournament API (Tournament-V5) exists and is the path Battlefy /
  Challengermode / Toornament use.** A **tournament code**, when a player pastes it
  into the LoL client, **auto-creates the custom lobby** with preset settings; at
  game end Riot's servers **POST the authoritative result to our callback URL**;
  and tournament-code games **do get valid Match-V5 records** (ordinary customs do
  not — that is the part the old premise got right).
- **The autonomous LCU-write approach we built is the ToS-risky path** (the
  project's own "beta gate"). Riot's LCU policy requires pre-release approval + a
  **whitelisted endpoint list**, **excludes Korea**, and third-party client
  automation (auto-accept / auto-invite) is widely treated as bannable; Riot
  tightened enforcement in **March 2025**.

**Recommendation:** make **Tournament Codes the PRIMARY match path** and demote the
autonomous-LCU pipeline to an optional fallback (pickup games / no-API contexts).
This **removes the beta gate entirely** and replaces forgeable-result corroboration
with an authoritative server callback.

## 2. What the old premise conflated

| Claim (CLAUDE.md / PLAN) | Reality |
|---|---|
| "No Riot Tournament API; that path never existed." | **False.** Tournament-V5 is live (providers → tournaments → codes → results callback). |
| "Match-V5 404s on customs." | **True for *ordinary* customs**, **false for tournament-code games** — those are tracked + queryable. |
| "Results are forgeable → need quorum/anomaly/admin override." | Only true **without** tournament codes. With them, the **winner comes from Riot's callback** — not the players. |

The one true nugget ("regular customs aren't in Match-V5") was over-generalized
into "no official tournament path at all," which drove the whole autonomous-LCU +
corroboration design.

## 3. How the sanctioned flow works (Tournament-V5)

1. **Register provider** — `POST /lol/tournament/v5/providers` with a **callback
   URL** (HTTP/HTTPS on 80/443, approved TLD) → `providerId`.
2. **Create tournament** — `POST /lol/tournament/v5/tournaments` (providerId) →
   `tournamentId`.
3. **Generate codes** — `POST /lol/tournament/v5/codes` (tournamentId + settings:
   map, pick type, team size, spectator, allowed participants, `metaData`) → one
   **tournament code per match**.
4. **Players redeem the code** in-client → the **custom lobby auto-creates** with
   the exact settings; no LCU-write automation, no invites, no auto-accept.
5. **Result callback** — at game end Riot **POSTs** `{ gameId, tournamentCode,
   metaData, ... }` to our provider URL → we look up full stats via Match-V5 and
   **advance the bracket authoritatively.**

> Use **one code per match** (a reused code doesn't return per-match stats).

## 4. Why this is better for us

- **Kills the beta gate.** No LCU writes → the "LCU-write ToS spike" in
  `compliance-checklist.md` is no longer on the critical path.
- **Authoritative results.** The corroboration/quorum/anomaly machinery becomes a
  *fallback*, not the trust backbone — huge reduction in "forgeable client" risk
  (see the `result-trust-model` memory).
- **No Korea exclusion, no client-automation ban surface.**
- **Less code to keep alive.** The community-RE'd, patch-fragile `lol-lcu` write
  path (create lobby / invite / auto-accept / failover / epoch fencing) stops being
  load-bearing.

## 5. Costs / caveats (honest)

- **Requires Tournament API *production* access** — a registered product + approved
  callback domain + Riot approval. Not instant; Riot is stricter post-March-2025.
  (A dev-portal **TOURNAMENT_STUB** for mock loop-testing should be confirmed on the
  portal — the public docs page did not restate it.)
- **Needs a public callback endpoint** (HTTPS, approved TLD) the backend exposes.
- **Players must have the two teams' accounts allowed on the code** — our existing
  PUUID identity (Account-V1) already gives us that.
- **Keep** the LCU **read-only** bits (identity verify, presence) — those are low
  risk and still useful.

## 6. Decision & the one action only the owner can take

**Adopt Tournament Codes as the primary path.** Concretely:

1. **[OWNER ACTION]** Apply for **Tournament API production access** on the Riot
   Developer Portal (registered product + callback URL). This is the real-world
   unblocker; everything else is code we control.
2. Build a `lol-riot` **Tournament-V5 client** (providers/tournaments/codes) +
   verify the STUB loop.
3. Add a backend **callback endpoint** (`POST /integrations/riot/tournament-callback`)
   → map `tournamentCode`→match → `record_winner(decided_by=RIOT_CALLBACK)` →
   advance (reuses the bracket engine we already have).
4. Issue **one code per match**; hand it to players via the desktop/Discord flows
   we already built (the match-ready gate stays — it just gates *code delivery*
   instead of LCU lobby init).
5. Keep autonomous-LCU as an **optional fallback** behind a flag; keep corroboration
   as the fallback adjudicator when no code/callback is available.

Until the owner secures API access, **nothing built so far is wasted**: the ready
gate, bracket engine, advancement, scheduling, teams, identity, and Discord feed
are all path-agnostic and feed straight into the Tournament-Code flow.

## 7. Sources

- Riot — *Changes to the LCU API Policy* (approval + whitelist + Korea).
  <https://www.riotgames.com/en/DevRel/changes-to-the-lcu-api-policy>
- Riot Developer Portal — Tournament API & LoL docs (providers → codes → callback,
  Match-V5). <https://developer.riotgames.com/docs/lol> ·
  <https://developer.riotgames.com/tournament-api.html>
- Riot — *Tournament V5 Is Here*.
  <https://www.riotgames.com/en/DevRel/tournament-v5-is-coming>
- Riot — *Developer Policies* (enforcement).
  <https://developer.riotgames.com/policies/general>
- Battlefy / Challengermode / Toornament tournament-code docs (real-world use of
  the sanctioned path).
