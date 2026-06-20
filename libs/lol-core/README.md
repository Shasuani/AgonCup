# lol-core

Domain enums, business rules, the **single-elimination bracket engine**, and the
**result quorum / anomaly adjudication** logic for LeagueOfLeagues.

**No web framework imports.** Pure domain logic, unit-tested. Depends on
`lol-schemas` (via a path dependency) for the canonical DTO types.

Consumed by `backend`, `worker`, and `bot` (PLAN.md §5).

## What's here (Phase 0)

- `bracket.py` — a REAL, unit-tested single-elimination bracket engine for
  **2..16 teams with byes** (next power of two, seed pairing 1-vs-N, byes to the
  top seeds). Returns rounds/matches.
- `adjudication.py` — a documented **stub** for the result quorum/anomaly
  adjudication function (the `>=6/10` both-teams-represented rule). Signature +
  docstring only; the full pipeline lands in Phase 6.
- `enums.py` — re-exports the domain enums from `lol_schemas` so domain callers
  have one import surface.

## Develop

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy
```
