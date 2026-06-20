"""Host designation, lobby state machine, WS command issuance, failover.

Phase 0: stub router. Endpoints/handlers land in later phases (see PLAN.md §7).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/orchestration", tags=["orchestration"])


@router.get("/_ping", summary="orchestration module liveness placeholder")
async def ping() -> dict[str, str]:
    """Placeholder endpoint proving the router is mounted. Replace in later phases."""
    return {"module": "orchestration", "status": "stub"}
