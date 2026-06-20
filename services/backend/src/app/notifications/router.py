"""Notifications router (PLAN.md §3.2 — internal-push liveness + seam mount).

The notification surface is **emit-only** from the backend's perspective: domain
flows publish typed :class:`~app.notifications.events.NotificationEnvelope`
messages onto the single Redis pub/sub channel (the bot consumes — PLAN.md §3.2,
"ONE channel … the backend is the sole emitter"). There is therefore no inbound
HTTP notification endpoint to expose; the real work lives in
:mod:`app.notifications.service` (the injectable publisher seam) and
:mod:`app.notifications.events` (the wire contract).

This router stays mounted by ``app.main`` (we replace the stub body without
touching the application factory) and exposes a liveness probe documenting the
emit channel + the supported notification kinds for operators.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.notifications.events import NOTIFICATIONS_CHANNEL, NotificationKind

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/_ping", summary="notifications module liveness")
async def ping() -> dict[str, object]:
    """Liveness probe + emit-plane self-description (PLAN.md §3.2).

    The backend is the sole emitter on the internal-push plane; this endpoint
    advertises the Redis channel and the closed set of notification kinds without
    exposing any tenant data.
    """
    return {
        "module": "notifications",
        "status": "ok",
        "channel": NOTIFICATIONS_CHANNEL,
        "kinds": [kind.value for kind in NotificationKind],
    }
