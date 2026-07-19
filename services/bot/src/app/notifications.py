"""Backend→bot notification fan-out subscriber (PLAN.md §3.2 internal push).

PLAN.md §3.2 mandates exactly ONE internal-push channel: Redis pub/sub, with the
**backend as the sole emitter** and the bot consuming it. This module owns the
async subscriber task: it reads :class:`NotificationEnvelope` messages off the
``lol:notifications`` channel, resolves the originating guild's configured
``notify_channel_id`` (via the backend bridge settings), renders the matching
``lol_discord`` embed, and posts it.

Resilience contract (PLAN.md §3.2): a notification failure must NEVER crash the
gateway. Every per-message error — malformed JSON, unknown guild, missing
channel, Discord API error — is logged and swallowed; the loop keeps running. The
task is cancellable so ``close``/shutdown can tear it down cleanly.

Offline/test-safe: importing this module performs NO network or Redis I/O. The
Redis client and pubsub are injected (or built lazily from settings only when the
task starts), so tests mock them and assert routing without any live connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

import discord

from app.backend_client import BackendClient, DiscordContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import AsyncIterator

# Mirrors app.notifications.events on the backend (the wire contract). Kept as a
# local constant so the bot does not import the backend package (separate service).
NOTIFICATIONS_CHANNEL = "lol:notifications"

log = logging.getLogger("app.bot.notifications")


class _PubSub(Protocol):
    """Minimal structural type for a redis.asyncio pubsub (so tests can fake it)."""

    async def subscribe(self, *channels: str) -> Any: ...

    async def unsubscribe(self, *channels: str) -> Any: ...

    def listen(self) -> AsyncIterator[dict[str, Any]]: ...

    async def aclose(self) -> Any: ...


class _Redis(Protocol):
    """Minimal structural type for a redis.asyncio client."""

    def pubsub(self, **kwargs: Any) -> _PubSub: ...

    async def aclose(self) -> Any: ...


def _build_embed(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Map a notification kind + payload to a ``lol_discord`` embed dict.

    Returns ``None`` for an unknown kind so the caller can skip it without raising
    (forward-compatible: a newly added backend kind is ignored, not fatal).
    """
    # Imported lazily inside the function so the heavy import only happens on the
    # hot path, keeping module import trivial.
    from lol_discord import (
        match_ready,
        match_result,
        registration_approved,
        registration_verified,
        tournament_announced,
        tournament_check_in,
    )

    if kind == "tournament_announced":
        return tournament_announced(
            tournament_name=payload.get("tournament_name", "A tournament"),
            headline=payload.get("headline", "Update"),
            detail=payload.get("detail", ""),
        )
    if kind == "registration_approved":
        return registration_approved(
            tournament_name=payload.get("tournament_name", "the tournament"),
            team_name=payload.get("team_name", "Your team"),
        )
    if kind == "registration_verified":
        # DM-routed account-verified notice: carries the resolved Riot ID, the
        # region display label, and the best-effort Solo/Duo rank snapshot.
        return registration_verified(
            riot_id=payload.get("riot_id", "Your account"),
            region=payload.get("region"),
            rank_text=payload.get("rank_text"),
        )
    if kind == "check_in_reminder":
        return tournament_check_in(name=payload["tournament_name"], deadline=payload["deadline"])
    if kind == "match_ready":
        # Fan-out notification: the player is a guest (the host is named); the
        # host gets their create-lobby instruction via the desktop client plane.
        return match_ready(
            match_label=payload["match_label"],
            host_name=payload["host_name"],
            is_host=False,
            lobby_name=payload.get("lobby_name"),
            lobby_password=payload.get("lobby_password"),
        )
    if kind == "result_recorded":
        return match_result(
            match_label=payload["match_label"],
            winner_name=payload["winner_name"],
            loser_name=payload["loser_name"],
            decided_by=payload["decided_by"],
        )
    return None


class NotificationSubscriber:
    """Consumes backend notification envelopes and posts embeds to guild channels.

    The subscriber is started from ``setup_hook``/``on_ready`` via :meth:`start`
    (which spawns the background task) and torn down via :meth:`close`. It is the
    only Redis consumer in the bot.
    """

    def __init__(
        self,
        bot: discord.Client,
        *,
        redis: _Redis,
        backend: BackendClient | None = None,
        channel: str = NOTIFICATIONS_CHANNEL,
    ) -> None:
        self._bot = bot
        self._redis = redis
        self._backend = backend or BackendClient()
        self._channel = channel
        self._task: asyncio.Task[None] | None = None
        # Cache backend origin_guild_id -> (discord_guild_id, notify_channel_id) so
        # routing is a read (and cached), never the mutating onboard upsert.
        self._guild_map: dict[str, tuple[int, int | None]] = {}

    def start(self) -> asyncio.Task[None]:
        """Spawn the subscriber loop as a cancellable background task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="lol-notifications")
        return self._task

    async def close(self) -> None:
        """Cancel the subscriber task and release the Redis connection."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        try:
            await self._redis.aclose()
        except Exception as exc:  # pragma: no cover - best-effort cleanup
            log.debug("redis aclose failed: %s", exc)

    async def run(self) -> None:
        """Subscribe and dispatch messages until cancelled.

        Resilient by construction: any per-message exception is logged and
        swallowed so the loop survives. Only cancellation breaks the loop.
        """
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    await self._dispatch(message.get("data"))
                except asyncio.CancelledError:  # pragma: no cover - propagate cancel
                    raise
                except Exception as exc:  # never crash the gateway (PLAN.md §3.2)
                    log.warning("notification dispatch failed: %s", exc)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        finally:
            try:
                await pubsub.unsubscribe(self._channel)
                await pubsub.aclose()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                log.debug("pubsub teardown failed: %s", exc)

    async def _dispatch(self, data: Any) -> None:
        """Parse one envelope, resolve its channel, and post the embed."""
        envelope = _parse_envelope(data)
        if envelope is None:
            return
        origin_guild_id = envelope.get("origin_guild_id")
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or not isinstance(origin_guild_id, str):
            log.warning("notification envelope missing origin_guild_id/payload")
            return

        kind = payload.get("kind")
        if not isinstance(kind, str):
            log.warning("notification payload missing kind")
            return

        embed_dict = _build_embed(kind, payload)
        if embed_dict is None:
            log.info("ignoring unknown notification kind: %s", kind)
            return

        # ``registration_verified`` is the one DM-routed kind (it carries the
        # user's discord_user_id so the bot can reach them directly). It is a
        # PERSONAL confirmation containing the linker's Riot ID + region + rank.
        # By design this is DM-ONLY: if the DM fails (DMs closed, bad id, REST
        # error) we DROP it (log only) rather than fall back to a public guild
        # channel — broadcasting a user's Riot ID + rank to a public channel
        # merely because their DMs are closed is an unintended PII disclosure
        # the user never consented to (privacy widening). The account is still
        # linked; the confirmation is advisory.
        if kind == "registration_verified":
            if not await self._try_dm(payload.get("discord_user_id"), embed_dict):
                log.info(
                    "registration_verified DM not delivered; dropping (DM-only, no public fallback)"
                )
            return

        channel = await self._resolve_channel(origin_guild_id)
        if channel is None:
            return

        # Never ping @everyone/roles/users from a backend-driven notification or
        # central-feed post (inbound-abuse defense, PLAN.md §3.2).
        await channel.send(
            embed=discord.Embed.from_dict(embed_dict),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _try_dm(self, discord_user_id: Any, embed_dict: dict[str, Any]) -> bool:
        """Attempt to DM ``embed_dict`` to the given Discord user id.

        Returns ``True`` if the DM was sent, ``False`` on any failure (closed DMs,
        unknown/invalid id, REST error). The caller DROPS the notification on
        ``False`` (no public guild fallback — see ``_dispatch``) to avoid leaking
        the linker's Riot ID + rank to a public channel. Never raises — resilience
        contract (PLAN.md §3.2).
        """
        if discord_user_id is None:
            return False
        try:
            user_id = int(discord_user_id)
            user = self._bot.get_user(user_id) or await self._bot.fetch_user(user_id)
            await user.send(
                embed=discord.Embed.from_dict(embed_dict),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
            log.info("registration_verified DM failed (%s); falling back to guild channel", exc)
            return False

    async def _resolve_channel(self, origin_guild_id: str) -> discord.abc.Messageable | None:
        """Resolve the configured notify channel for a backend guild id (or ``None``).

        The envelope carries the backend's internal ``origin_guild_id``; the bot
        maps it to a Discord guild it serves (tenant-authz gate, PLAN.md §3.2 —
        reject foreign guilds). Mapping uses the **read-only** ``/guilds/resolve``
        bridge endpoint and an in-process cache, so notification routing never
        triggers a write (no guild re-onboard / admin re-seed on the hot path).
        """
        if origin_guild_id not in self._guild_map:
            await self._refresh_guild_map()
        entry = self._guild_map.get(origin_guild_id)
        if entry is None:
            log.info("no served guild matches origin %s; rejecting", origin_guild_id)
            return None
        discord_guild_id, channel_id = entry
        if channel_id is None:
            log.info("guild %s has no notify_channel_id configured", origin_guild_id)
            return None
        guild = self._bot.get_guild(discord_guild_id)
        channel = guild.get_channel(channel_id) if guild is not None else None
        if isinstance(channel, discord.abc.Messageable):
            return channel
        log.info("notify channel %s not found in guild %s", channel_id, discord_guild_id)
        return None

    async def _refresh_guild_map(self) -> None:
        """(Re)build the origin_guild_id -> (discord_guild_id, channel) cache via the
        read-only resolve endpoint (no mutation; admin not asserted)."""
        for guild in self._bot.guilds:
            ctx = DiscordContext(
                discord_user_id=self._self_id(),
                discord_guild_id=guild.id,
                is_discord_guild_admin=False,
                display_name=guild.name,
            )
            try:
                view = await self._backend.resolve_guild(ctx)
            except Exception as exc:
                log.debug("resolve lookup failed for guild %s: %s", guild.id, exc)
                continue
            gid = str(view.get("guild_id", ""))
            settings = view.get("settings") or {}
            channel_id = settings.get("notify_channel_id") if isinstance(settings, dict) else None
            if gid:
                self._guild_map[gid] = (
                    guild.id,
                    channel_id if isinstance(channel_id, int) else None,
                )

    def _self_id(self) -> int:
        user = self._bot.user
        return user.id if user is not None else 0


def _parse_envelope(data: Any) -> dict[str, Any] | None:
    """Decode the raw pub/sub payload into an envelope dict (or ``None``)."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            log.warning("notification payload not utf-8")
            return None
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            log.warning("notification payload not valid JSON")
            return None
        return parsed if isinstance(parsed, dict) else None
    if isinstance(data, dict):
        return data
    return None


__all__ = [
    "NOTIFICATIONS_CHANNEL",
    "NotificationSubscriber",
]
