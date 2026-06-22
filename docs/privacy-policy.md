conti# Privacy Policy — LeagueOfLeagues (DRAFT / STUB)

> **Placeholder.** A public, hosted privacy policy is required for Riot product
> registration and Discord app verification. Replace this with a legally reviewed
> version before beta. PLAN.md §7 Phase 0/1.

## Data we collect
- **Discord identity:** Discord user ID + display name (via OAuth2). OAuth tokens
  are encrypted at rest.
- **Riot identity:** Riot ID and the resolved **PUUID** (canonical), region, and
  verification state. We call **Account-V1 only**, at account-linking time.
- **Match operation data:** lobby/host state and **end-of-game reports** captured
  by the desktop client from the local League client (LCU + Live Client Data).

## How we use it
- Running tournaments (registration, brackets, match orchestration, result
  corroboration) and delivering status via Discord.

## What we do NOT do
- We do not use Riot's Tournament API. We do not de-anonymize players. We do not
  automate gameplay. The desktop client's LCU **write** automation is opt-in,
  presence-gated, rate-limited, and kill-switchable.

## Your rights
- Account/guild deletion purges associated data (incl. `on_guild_remove`).

## Disclaimer
LeagueOfLeagues isn't endorsed by Riot Games and doesn't reflect the views or
opinions of Riot Games or anyone officially involved in producing or managing
Riot Games properties.

_Contact: TODO._
