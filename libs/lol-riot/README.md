# lol-riot

Thin **Riot Account-V1** client skeleton plus a tiny, real, async **token-bucket
rate limiter**.

Scope (v3 autonomous model):

- **Account-V1** — `gameName` + `tagLine` -> PUUID via the continental cluster
  (`americas | asia | europe`, *not* `na1`/`euw1`). `RiotAccountClient.
  get_account_by_riot_id` is fully implemented (real `httpx` call,
  URL-encoded path segments, `X-Riot-Token` header).
- **Minimal Summoner-V4 `by-puuid`** (`get_summoner_by_puuid`) for the
  profile-icon verification nonce only. **Platform-routed** (`na1`/`euw1`/...
  via `PlatformRoute`) — Summoner-V4 is per-platform, Account-V1 is continental.
- A light token-bucket limiter honoring 20 req/s style personal-key budgets;
  bounded 429 retry honoring `Retry-After`.
- Typed errors: `NotFound` (404), `RateLimited` (429, budget exhausted),
  `RiotApiError` (other / transport).
- **No Tournament-V5**, no provider/callback, no Match-V5.

## Layout

- `src/lol_riot/` — import package (`import lol_riot`)
- `tests/` — pytest

## Dev

```sh
uv sync --extra dev
uv run ruff check .
uv run mypy
uv run pytest
```
