"""Global live-presence ("who's playing right now") — Redis-backed, cross-guild.

Live presence is ephemeral, cross-guild state, so it lives in Redis (a single
keyspace), NOT the guild-RLS-scoped DB tables. A plain tenant DB read cannot see
across guilds (unset ``app.current_guild`` GUC → fail closed / zero rows), so a
global "who is in-game across every tournament" view is impossible via SQL; the
Redis keyspace sidesteps that by design. Entries carry ONLY display strings
(server + tournament name, player display names) — no PUUID / identifier / secret.
"""

from __future__ import annotations

from app.presence.service import (
    PRESENCE_KEY_PREFIX,
    PresenceEntry,
    clear_presence,
    list_presence,
    publish_presence,
    refresh_presence_for_match,
)

__all__ = [
    "PRESENCE_KEY_PREFIX",
    "PresenceEntry",
    "clear_presence",
    "list_presence",
    "publish_presence",
    "refresh_presence_for_match",
]
