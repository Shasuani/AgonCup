# lol-schemas

Canonical **Pydantic v2** DTOs for LeagueOfLeagues — the single canonical Python
type source consumed by `backend`, `worker`, `bot`, and (via codegen) the TS
clients.

Framework-light: depends on `pydantic` only. No web framework, no DB driver.

These DTOs reflect the **v3 autonomous data model** (PLAN.md §2): PUUID is the
canonical immutable identity key, all non-`User`/`RiotAccount` entities are
guild-scoped, tournaments cap at 16 teams, and result truth is client-side
corroboration (no Riot Tournament API).

## Layout

```
src/lol_schemas/   # importable package (lol_schemas)
tests/             # pytest smoke tests
```

## Develop

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy
```
