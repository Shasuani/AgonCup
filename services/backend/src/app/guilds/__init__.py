"""Guild onboarding + per-guild settings (PLAN.md §3.2 multi-tenant bot).

The :class:`~app.guilds.settings.GuildSettings` model is the typed view over the
``guilds.settings`` JSONB column (``admin_role_ids``, ``notify_channel_id``,
``central_feed_enabled``); :mod:`app.guilds.service` owns onboarding/upsert of the
:class:`~app.models.Guild` anchor (seeding the inviting admin's ``guild_admin``
role) and reading/merging those settings.
"""

from __future__ import annotations

from app.guilds.settings import GuildSettings

__all__ = ["GuildSettings"]
