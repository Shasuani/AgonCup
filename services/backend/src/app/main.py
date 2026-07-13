"""FastAPI application entrypoint.

Wires structured JSON logging, the observability endpoints (`/healthz`,
`/metrics`), and mounts the (currently stub) feature routers. See PLAN.md §1.4
(observability from day 0) and §3.1 (backend modules).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from app.accounts.router import router as accounts_router
from app.audit.router import router as audit_router
from app.auth.router import router as auth_router
from app.config import get_settings
from app.federation.router import router as federation_router
from app.integrations.discord.router import router as discord_bridge_router
from app.lifecycle.router import router as lifecycle_router
from app.logging import configure_logging, get_logger
from app.notifications.router import router as notifications_router
from app.orchestration.router import router as orchestration_router
from app.presence.router import router as presence_router
from app.realtime.router import router as realtime_router
from app.results.router import router as results_router
from app.teams.router import router as teams_router
from app.tournaments.router import router as tournaments_router

# Dedicated registry so the exposition is deterministic and test-friendly.
# Application metrics (request counters, DB-pool gauges, etc.) register here in
# later phases (PLAN.md §7 Phase 0: DB-pool gauges).
metrics_registry = CollectorRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("app.startup")
    log.info(
        "backend.startup",
        environment=settings.environment,
        version=app.version,
    )
    yield
    # Close the shared realtime Redis pool (command pub/sub + WS gateway).
    from app.realtime.redis_client import aclose_redis

    await aclose_redis()
    get_logger("app.shutdown").info("backend.shutdown")


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    app = FastAPI(
        title="LeagueOfLeagues Backend",
        version="0.1.0",
        description="Tournaments, accounts, autonomous match orchestration & results.",
        lifespan=lifespan,
    )

    # --- Observability endpoints (no auth, no tenant scope) -----------------
    @app.get("/healthz", tags=["observability"], summary="Liveness probe")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/metrics",
        tags=["observability"],
        summary="Prometheus metrics exposition",
        response_class=PlainTextResponse,
    )
    async def metrics() -> Response:
        payload = generate_latest(metrics_registry)
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)

    # --- Feature routers ----------------------------------------------------
    # ``discord_bridge_router`` is the service-authenticated bot bridge (PLAN.md
    # §3.2). ``notifications_router`` / ``federation_router`` are mounted as STUBS
    # now so the next phase fills their bodies WITHOUT editing this factory.
    api_prefix = "/api/v1"
    for r in (
        auth_router,
        accounts_router,
        teams_router,
        tournaments_router,
        orchestration_router,
        presence_router,
        results_router,
        audit_router,
        realtime_router,
        lifecycle_router,
        discord_bridge_router,
        notifications_router,
        federation_router,
    ):
        app.include_router(r, prefix=api_prefix)

    _ = settings  # reserved for future wiring (CORS, middleware, etc.)
    return app


app = create_app()
