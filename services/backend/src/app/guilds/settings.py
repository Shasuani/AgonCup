"""Typed view over the ``guilds.settings`` JSONB column (PLAN.md §3.2).

Per-guild settings are stored as a single JSONB blob on the :class:`~app.models.
Guild` anchor (no extra table) so a tenant's bot configuration travels with the
anchor and is purged/restored with it. :class:`GuildSettings` is the Pydantic v2
schema for that blob: it validates the persisted dict on read and re-serializes a
canonical dict on write.

Fields (PLAN.md §3.2 "per-guild settings"):

* ``admin_role_ids`` — Discord role snowflakes the guild treats as bot admins.
  This is advisory metadata the bot uses to decide ``is_discord_guild_admin``; the
  backend's authority for admin gating is still the Manage-Guild assertion plus a
  ``guild_admin`` ``user_guild_roles`` row (see :mod:`app.integrations.discord`).
* ``notify_channel_id`` — the Discord channel snowflake the bot posts tournament
  notifications to (consumed by the notifications phase).
* ``central_feed_enabled`` — the tenant's opt-in to surface tournaments in the
  central AgonCup feed (federation phase; consent + approval still
  required downstream).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GuildSettings(BaseModel):
    """Per-guild bot settings persisted in ``guilds.settings`` (PLAN.md §3.2)."""

    model_config = ConfigDict(extra="ignore")

    admin_role_ids: list[int] = Field(
        default_factory=list,
        description="Discord role snowflakes the guild designates as bot admins.",
    )
    notify_channel_id: int | None = Field(
        default=None,
        description="Discord channel snowflake the bot posts notifications to.",
    )
    central_feed_enabled: bool = Field(
        default=False,
        description="Opt-in to surface this guild's tournaments in the central feed.",
    )

    @classmethod
    def from_jsonb(cls, raw: dict[str, Any] | None) -> GuildSettings:
        """Parse the persisted JSONB dict (tolerant of missing/extra keys)."""
        return cls.model_validate(raw or {})

    def to_jsonb(self) -> dict[str, Any]:
        """Serialize to the canonical dict written back to ``guilds.settings``."""
        return self.model_dump(mode="json")

    def merge(self, patch: GuildSettingsPatch) -> GuildSettings:
        """Return a copy with only the explicitly-set patch fields overridden.

        Unset patch fields leave the current value intact (PATCH semantics) so a
        caller updating only ``notify_channel_id`` does not clobber the rest.
        """
        updates = patch.model_dump(exclude_unset=True)
        return self.model_copy(update=updates)


class GuildSettingsPatch(BaseModel):
    """Partial update to :class:`GuildSettings` — only set fields are applied."""

    model_config = ConfigDict(extra="forbid")

    admin_role_ids: list[int] | None = None
    notify_channel_id: int | None = None
    central_feed_enabled: bool | None = None


__all__ = ["GuildSettings", "GuildSettingsPatch"]
