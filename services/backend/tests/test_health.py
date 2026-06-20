"""Health/observability smoke tests.

Uses the in-process ASGI transport (httpx) against the FastAPI app so no network
or DB is required — these are the Phase-0 "it actually runs" tests.
"""

from __future__ import annotations

import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_healthz_returns_ok() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_metrics_exposition() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 200
    # Prometheus text exposition content type.
    assert resp.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_stub_router_mounted() -> None:
    # A still-stub module (Phase 2 implements orchestration later).
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/orchestration/_ping")
    assert resp.status_code == 200
    assert resp.json()["module"] == "orchestration"


@pytest.mark.asyncio
async def test_tournaments_router_mounted_and_protected() -> None:
    # The real tournaments router replaced its stub: listing requires auth, so an
    # unauthenticated request is rejected (mounted + guarded, not a 404).
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tournaments")
    assert resp.status_code in (401, 403)
