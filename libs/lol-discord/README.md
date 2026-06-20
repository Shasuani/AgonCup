# lol-discord

Shared Discord presentation helpers for the (thin) bot (PLAN.md §3.2 / §5):
consistent embeds and error formatting, no boilerplate.

Phase-0 skeleton: framework-light. The embed builder returns a plain `dict`
matching Discord's embed JSON shape, so it has **no hard `discord.py` dependency**
and stays trivially testable. The bot adapts it to `discord.Embed` at the edge.

## Helpers

- `build_embed(...) -> dict` — title/description/fields/color/footer embed dict.
- `format_error(exc | str) -> str` — safe, user-facing one-line error string
  (mentions suppressed, length-bounded).

## Dev

```sh
uv sync --extra dev
uv run ruff check .
uv run mypy
uv run pytest
```
