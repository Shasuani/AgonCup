# lol-realtime

The **WebSocket wire contract** for AgonCup: Pydantic envelope models
plus an exported JSON schema (PLAN.md §1.4, §5).

Every envelope carries:

- `origin_guild_id` — tenant of origin (RLS / multi-tenant authz).
- an **audience descriptor** — who the message is targeted at.
- a discriminated `payload` — a `command` or `ack` variant
  (e.g. `LobbyCommand`, `ReportAck`).

`export_json_schema()` dumps the canonical JSON schema; `make gen-clients`
(later) consumes it and CI drift-checks it. This Phase-0 skeleton ships a small
set of command/ack variants — more land as orchestration is built out (§3.6).

## Dev

```sh
uv sync --extra dev
uv run ruff check .
uv run mypy
uv run pytest
uv run lol-realtime-schema   # prints the envelope JSON schema
```
