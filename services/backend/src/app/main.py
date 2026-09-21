"""FastAPI application entrypoint.

Wires structured JSON logging, the observability endpoints (`/healthz`,
`/metrics`), and mounts the (currently stub) feature routers. See PLAN.md §1.4
(observability from day 0) and §3.1 (backend modules).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from app.accounts.router import router as accounts_router
from app.audit.router import router as audit_router
from app.auth.router import router as auth_router
from app.config import get_settings
from app.desktop.router import router as desktop_router
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


async def _scheduler_loop(interval_seconds: int) -> None:
    """Periodic scheduled-tournament ticker (PLAN.md §3.2).

    Runs on the OWNER connection (cross-tenant: it must see every guild's rows,
    which an RLS-bound app_rw session with no GUC could not). Resilient — any tick
    error is logged and the loop continues.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.notifications import RedisNotificationPublisher
    from app.realtime.redis_client import get_redis
    from app.tournaments.readiness import run_readiness_tick
    from app.tournaments.scheduler import run_expiry_tick, run_scheduler_tick

    settings = get_settings()
    log = get_logger("app.scheduler")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            now = datetime.now(tz=UTC)
            publisher = RedisNotificationPublisher(get_redis())
            try:
                async with maker() as session, session.begin():
                    opened, started = await run_scheduler_tick(session, publisher, now=now)
                if opened or started:
                    log.info("tournament_scheduler_tick", opened=opened, started=started)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tournament_scheduler_tick_failed")
            # Match-ready gate (edge-cases §8.5): arm ready windows + forfeit no-shows.
            try:
                async with maker() as session, session.begin():
                    armed, forfeited = await run_readiness_tick(
                        session,
                        publisher,
                        now=now,
                        window_seconds=settings.ready_window_seconds,
                    )
                if armed or forfeited:
                    log.info("match_readiness_tick", armed=armed, forfeited=forfeited)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("match_readiness_tick_failed")
            # Abandoned tournaments: cancel the ones that were never finished, so
            # they stop sitting in every entrant's list looking live (§3.2).
            try:
                async with maker() as session, session.begin():
                    expired = await run_expiry_tick(
                        session,
                        publisher,
                        now=now,
                        after_hours=settings.stale_tournament_hours,
                    )
                if expired:
                    log.info("tournament_expiry_tick", cancelled=expired)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tournament_expiry_tick_failed")
    finally:
        await engine.dispose()


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
    scheduler_task: asyncio.Task[None] | None = None
    if settings.scheduler_enabled:
        scheduler_task = asyncio.create_task(_scheduler_loop(settings.scheduler_interval_seconds))
    yield
    if scheduler_task is not None:
        scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler_task
    # Close the shared realtime Redis pool (command pub/sub + WS gateway).
    from app.realtime.redis_client import aclose_redis

    await aclose_redis()
    get_logger("app.shutdown").info("backend.shutdown")


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    app = FastAPI(
        title="AgonCup Backend",
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
        desktop_router,
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
