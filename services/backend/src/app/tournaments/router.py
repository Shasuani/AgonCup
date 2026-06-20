"""Tournament CRUD, bracket generation, seeding, check-in.

Phase 0: stub router. Endpoints/handlers land in later phases (see PLAN.md §7).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/tournaments", tags=["tournaments"])


@router.get("/_ping", summary="tournaments module liveness placeholder")
async def ping() -> dict[str, str]:
    """Placeholder endpoint proving the router is mounted. Replace in later phases."""
    return {"module": "tournaments", "status": "stub"}
