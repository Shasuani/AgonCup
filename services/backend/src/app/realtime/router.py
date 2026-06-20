"""Realtime WebSocket router: tenant-authz'd command relay + ack ingestion (PLAN.md §3.6 / §5).

Thin FastAPI wiring over :mod:`app.realtime.hub`. This module only supplies the
*real* I/O — the Starlette :class:`~fastapi.WebSocket`, a request-scoped tenant
``AsyncSession`` factory, and a ``redis.asyncio`` pub/sub client — and delegates
all behaviour (auth, subscription authz, the bidirectional relay) to
:func:`app.realtime.hub.serve`, which is exhaustively unit-tested offline with a
FAKE websocket + FAKE pub/sub (no live IO).

Endpoint: ``GET /api/v1/realtime/ws`` (the router is mounted under ``/api/v1`` in
``app.main``; this adds the WS route on the existing router).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, WebSocket

from app.db import SessionLocal
from app.logging import get_logger
from app.realtime import hub

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime redis import.
    from redis.asyncio import Redis

logger = get_logger(__name__)

router = APIRouter(prefix="/realtime", tags=["realtime"])


@router.get("/_ping", summary="realtime module liveness placeholder")
async def ping() -> dict[str, str]:
    """Placeholder endpoint proving the router is mounted (HTTP liveness)."""
    return {"module": "realtime", "status": "ok"}


def _redis_client() -> Redis:
    """Construct an async Redis client from settings (lazy import keeps imports IO-free).

    Importing this module must NOT connect (SHARED CONVENTIONS / PLAN.md §3.2): the
    ``redis`` import and client construction happen only when a WS actually
    connects, so the test suite imports the router without opening a socket.
    """
    from redis.asyncio import Redis  # noqa: PLC0415 - deferred so import stays IO-free

    from app.config import get_settings

    return Redis.from_url(get_settings().redis_url)


@router.websocket("/ws")
async def realtime_ws(websocket: WebSocket) -> None:
    """The realtime WebSocket endpoint (PLAN.md §3.6.1 / §5).

    Auth is the ``?token=<access-jwt>`` query param (the hub closes with 4401 on
    failure). After a validated ``subscribe`` to a participant match, orchestration
    commands are relayed server->client and inbound ack/heartbeat/report frames are
    routed to the epoch-fenced orchestration ingestion functions. All of that lives
    in :func:`app.realtime.hub.serve`; here we just inject the real I/O seams.

    NOTE (security): a query-param token can leak via proxy/access logs — accepted
    for the v1 loopback/native desktop client. A ``Sec-WebSocket-Protocol`` bearer
    subprotocol is the documented follow-up (see hub module docstring).
    """
    client = _redis_client()

    def _pubsub_factory() -> hub.PubSubLike:
        # The concrete redis.asyncio PubSub satisfies the narrow PubSubLike surface
        # the hub uses (subscribe/unsubscribe/listen/aclose); its broader *args
        # signatures don't structurally unify, so cast at this seam boundary.
        return cast("hub.PubSubLike", client.pubsub())

    try:
        await hub.serve(
            websocket,
            session_factory=SessionLocal,
            pubsub_factory=_pubsub_factory,
        )
    finally:
        await client.aclose()


__all__ = ["router"]
