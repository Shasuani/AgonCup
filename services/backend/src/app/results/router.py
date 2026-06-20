"""Result-report ingress, nonce validation, quorum/anomaly adjudication.

Phase 0: stub router. Endpoints/handlers land in later phases (see PLAN.md §7).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/results", tags=["results"])


@router.get("/_ping", summary="results module liveness placeholder")
async def ping() -> dict[str, str]:
    """Placeholder endpoint proving the router is mounted. Replace in later phases."""
    return {"module": "results", "status": "stub"}
