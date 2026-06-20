"""User-facing error formatting.

Produces a single, safe line for surfacing in Discord: mentions are neutralized
(``@everyone`` abuse, PLAN.md §3.4 / §9 central-feed abuse) and length is
bounded.
"""

from __future__ import annotations

_MAX_LEN = 1900  # below Discord's 2000-char message limit, leaves headroom


def _neutralize_mentions(text: str) -> str:
    # Break @everyone/@here and raw mention syntax without dropping information.
    return text.replace("@everyone", "@​everyone").replace("@here", "@​here").replace("<@", "<​@")


def format_error(error: BaseException | str, *, prefix: str = "Error") -> str:
    """Format an exception or message as a safe one-line user-facing string."""
    if isinstance(error, BaseException):
        message = str(error) or error.__class__.__name__
    else:
        message = error

    message = " ".join(message.split())  # collapse whitespace/newlines
    message = _neutralize_mentions(message)
    line = f"{prefix}: {message}" if message else prefix

    if len(line) > _MAX_LEN:
        line = line[: _MAX_LEN - 1] + "…"
    return line


__all__ = ["format_error"]
