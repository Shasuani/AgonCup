"""Scheduled-tournament ticker (PLAN.md §3.2).

A periodic system job (run from the app lifespan loop) that drives scheduled
tournaments forward on wall-clock time:

* **Registration opens** — a ``DRAFT`` tournament whose ``registration_opens_at``
  has passed flips to ``OPEN`` and an announcement is published to the guild's
  Discord notify channel.
* **Starting** — a tournament whose ``start_at`` has passed gets a one-time
  "starting" announcement (guarded by ``start_announced_at``); the admin still
  starts the bracket manually (a real start needs checked-in teams).
* **Expiry** (:func:`run_expiry_tick`) — a tournament that never reached a
  terminal state and has nothing left on its calendar is auto-``CANCELLED`` after
  ``stale_tournament_hours``, so abandoned ones stop sitting in every entrant's
  list pretending to be live.

This is a **cross-tenant** job, so it must run on a session that can see every
guild's rows — i.e. the OWNER connection (RLS bypassed), NOT the request path's
``app_rw`` session (which fail-closes without a tenant GUC). The loop owner wires
that; :func:`run_scheduler_tick` is pure over the session it is given and is unit
tested with a fake session. Idempotent: the state transitions (DRAFT→OPEN,
``start_announced_at``) mean a row is acted on at most once.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from lol_schemas.enums import TournamentStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tournament
from app.notifications import NotificationPublisher, publish_tournament_announced

#: States a tournament can rot in. Everything else is terminal — already done with.
_LIVE_STATUSES = (
    TournamentStatus.DRAFT,
    TournamentStatus.OPEN,
    TournamentStatus.CHECK_IN,
    TournamentStatus.RUNNING,
)


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


def _stale_after(t: Tournament) -> datetime | None:
    """The moment a tournament stops having any scheduled future and starts rotting.

    The **latest** of everything that was ever meant to happen: creation, when
    registration was due to open, and the scheduled kickoff. Taking the maximum is
    what makes the sweep safe — a tournament created today for a kickoff next month
    has a reference date next month, so it is untouchable until the expiry window
    has run from the kickoff it was actually scheduled for. Only one with nothing
    left on its calendar can go stale.

    ``None`` when there is no date at all to measure from (an unflushed row whose
    ``created_at`` server default has not landed yet) — the caller then leaves it
    alone rather than guessing.
    """
    candidates = [d for d in (t.created_at, t.registration_opens_at, t.start_at) if d is not None]
    return max(candidates) if candidates else None


async def run_expiry_tick(
    session: AsyncSession,
    publisher: NotificationPublisher,
    *,
    now: datetime,
    after_hours: int,
) -> int:
    """Auto-cancel tournaments that were never finished. Returns the count.

    A tournament that is created and then abandoned — nobody joins, the admin
    forgets, the bracket never starts — sits in every entrant's "my tournaments"
    list forever, looking live. This is the sweep that closes them out: once
    ``after_hours`` have passed since the last thing that was ever scheduled for it
    (see :func:`_stale_after`), a non-terminal tournament flips to ``CANCELLED``
    and the guild is told once.

    Cancel, never delete: the row, its registrations and any results it did produce
    stay for the admin's record. Players clear it off their own list with the
    per-user hide (``DELETE /tournaments/{id}/mine``) — dead tournaments do not
    vanish behind anyone's back.

    Idempotent by construction: the status flip takes the row out of the query, so
    a tournament is cancelled at most once no matter how often the loop ticks.
    """
    cutoff = timedelta(hours=after_hours)
    candidates = (
        (await session.execute(select(Tournament).where(Tournament.status.in_(_LIVE_STATUSES))))
        .scalars()
        .all()
    )

    cancelled = 0
    for t in candidates:
        # Re-checked in Python (not SQL): the reference date is a max() over three
        # nullable columns, and the fake session the unit tests run on cannot model
        # that as a predicate.
        if t.status not in _LIVE_STATUSES:
            continue
        reference = _stale_after(t)
        if reference is None or now - reference <= cutoff:
            continue
        t.status = TournamentStatus.CANCELLED
        await publish_tournament_announced(
            publisher,
            origin_guild_id=str(t.guild_id),
            tournament_name=t.name,
            headline="Cancelled — abandoned",
            detail=(
                f"No result after {after_hours}h, so it was closed automatically. "
                "Create a new tournament to run it again."
            ),
        )
        cancelled += 1

    await session.flush()
    return cancelled
