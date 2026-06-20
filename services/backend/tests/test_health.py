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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tournaments/_ping")
    assert resp.status_code == 200
    assert resp.json()["module"] == "tournaments"
