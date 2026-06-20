"""Authentication & per-(user, guild) authorization.

Phase 0: stub router. Endpoints/handlers land in later phases (see PLAN.md §7).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/_ping", summary="auth module liveness placeholder")
async def ping() -> dict[str, str]:
    """Placeholder endpoint proving the router is mounted. Replace in later phases."""
    return {"module": "auth", "status": "stub"}
