# lol-bot

Multi-tenant **Discord bot** for AgonCup (PLAN.md §3.2).

`discord.py` **`AutoShardedBot`** from day one (sharding is transparent as guild
count grows). Runs on the **non-privileged `guilds` intent only** — deliberately
avoiding `members`/`message_content` so the bot stays under the privileged-intent
verification gate for as long as possible (PLAN.md §3.2 / §9).

The bot is a **thin presentation layer**: it calls the backend REST API and
subscribes to the realtime plane for push events. **No game logic lives here.**

## Phase 0 status

Runnable skeleton: an `AutoShardedBot` with a single `/ping` slash command.
Importing the module does **not** connect — the gateway connection only starts
under `python -m app.main` (or the container `CMD`) when `DISCORD_BOT_TOKEN` is
set. This keeps unit tests offline.

## Run

```bash
DISCORD_BOT_TOKEN=... python -m app.main
```
