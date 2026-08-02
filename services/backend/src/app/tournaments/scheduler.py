"""Scheduled-tournament ticker (PLAN.md §3.2).

A periodic system job (run from the app lifespan loop) that drives scheduled
tournaments forward on wall-clock time:

* **Registration opens** — a ``DRAFT`` tournament whose ``registration_opens_at``
  has passed flips to ``OPEN`` and an announcement is published to the guild's
  Discord notify channel.
* **Starting** — a tournament whose ``start_at`` has passed gets a one-time
  "starting" announcement (guarded by ``start_announced_at``); the admin still
  starts the bracket manually (a real start needs checked-in teams).

This is a **cross-tenant** job, so it must run on a session that can see every
guild's rows — i.e. the OWNER connection (RLS bypassed), NOT the request path's
``app_rw`` session (which fail-closes without a tenant GUC). The loop owner wires
that; :func:`run_scheduler_tick` is pure over the session it is given and is unit
tested with a fake session. Idempotent: the state transitions (DRAFT→OPEN,
``start_announced_at``) mean a row is acted on at most once.
"""

from __future__ import annotations

from datetime import datetime

from lol_schemas.enums import TournamentStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tournament
from app.notifications import NotificationPublisher, publish_tournament_announced


def _fmt(dt: datetime) -> str:
    """Format a UTC timestamp for a human-readable announcement."""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


async def run_scheduler_tick(
    session: AsyncSession,
    publisher: NotificationPublisher,
    *,
    now: datetime,
) -> tuple[int, int]:
    """One scheduler pass. Returns ``(opened, started)`` counts.

    Opens registration for due DRAFT tournaments (+ announce) and fires the
    one-time "starting" announcement for tournaments past ``start_at``.
    """
    opened = 0
    due_open = (
        (
            await session.execute(
                select(Tournament).where(
                    Tournament.status == TournamentStatus.DRAFT,
                    Tournament.registration_opens_at.is_not(None),
                    Tournament.registration_opens_at <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    for t in due_open:
        # Defensive time re-check (the query already filters; keeps the pass correct
        # even under a store that can't model the `<=` / IS NOT NULL predicates).
        if t.registration_opens_at is None or t.registration_opens_at > now:
            continue
        t.status = TournamentStatus.OPEN
        detail = f"Registration is now open. Join with code **{t.join_code}**."
        if t.start_at is not None:
            detail += f" Starts {_fmt(t.start_at)}."
        await publish_tournament_announced(
            publisher,
            origin_guild_id=str(t.guild_id),
            tournament_name=t.name,
            headline="Registration is open",
            detail=detail,
        )
        opened += 1

    started = 0
    due_start = (
        (
            await session.execute(
                select(Tournament).where(
                    Tournament.start_at.is_not(None),
                    Tournament.start_at <= now,
                    Tournament.start_announced_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for t in due_start:
        if t.start_at is None or t.start_at > now or t.start_announced_at is not None:
            continue
        t.start_announced_at = now
        await publish_tournament_announced(
            publisher,
            origin_guild_id=str(t.guild_id),
            tournament_name=t.name,
            headline="Starting now",
            detail="Registration is closed — the bracket will begin shortly.",
        )
        started += 1

    await session.flush()
    return opened, started
