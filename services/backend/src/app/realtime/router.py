"""WebSocket hub with tenant authz and targeted command delivery + ACKs.

Phase 0: stub router. Endpoints/handlers land in later phases (see PLAN.md §7).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/realtime", tags=["realtime"])


@router.get("/_ping", summary="realtime module liveness placeholder")
async def ping() -> dict[str, str]:
    """Placeholder endpoint proving the router is mounted. Replace in later phases."""
    return {"module": "realtime", "status": "stub"}
