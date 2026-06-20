# lol-riot

Thin **Riot Account-V1** client skeleton plus a tiny, real, async **token-bucket
rate limiter**.

Scope (v3 autonomous model):

- **Account-V1 only** — `gameName` + `tagLine` -> PUUID via the continental
  cluster (`americas | asia | europe`, *not* `na1`/`euw1`).
- A light token-bucket limiter honoring 20 req/s style personal-key budgets and
  `Retry-After` (limiter is real and tested; HTTP wiring is a Phase-0 stub).
- **No Tournament-V5**, no provider/callback, no Match-V5.

This is a Phase-0 skeleton: `get_account_by_riot_id` is a stub that raises
`NotImplementedError`. The limiter is fully implemented.

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
