# lol-desktop

LeagueOfLeagues **desktop client** (Electron + TypeScript) — PLAN.md §3.3 / §3.6.

Autonomously drives the local League client over the **LCU** (host: create lobby
+ invite by PUUID; guest: auto-accept) and captures live (`:2999`) + end-of-game
stats to report to the backend. It is **load-bearing for match execution** but
**advisory for result truth** (results are corroborated across all 10 clients).

All LCU access goes through the single audited **`@lol/lcu`** adapter. Every
**write** is gated: per-session default-OFF opt-in + presence gate + human-paced
rate limits + recorded consent + global kill-switch; **Korea excluded**; no
in-match gameplay automation (PLAN.md §3.3 / §9).

## Runs natively (not in a container)

The app must reach `127.0.0.1` (LCU + `:2999`) on the player's machine, so it
**runs natively**. The `Dockerfile` here is a **build/packaging container only**.
Production installers are built and **signed/notarized per-OS in CI** — a macOS
runner (Apple Developer ID + notarization) and a Windows runner (Authenticode);
forced auto-update is critical-path because LCU schemas drift on League's ~2-week
patch cadence (PLAN.md §6 / §7).

## Dev

```bash
npm install        # pulls electron + builds the @lol/lcu path dep
npm run typecheck
npm start          # launches Electron locally
```
