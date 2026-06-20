"""Riot account linking and the hardened ownership-verification flow.

Phase 0: stub router. Endpoints/handlers land in later phases (see PLAN.md §7).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/_ping", summary="accounts module liveness placeholder")
async def ping() -> dict[str, str]:
    """Placeholder endpoint proving the router is mounted. Replace in later phases."""
    return {"module": "accounts", "status": "stub"}
