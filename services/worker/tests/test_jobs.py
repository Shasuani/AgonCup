"""Phase-0 smoke tests for the arq worker (PLAN.md §7 Phase 0).

These assert the queue *shape* is real — the worker module imports, the jobs are
registered on ``WorkerSettings``, and the liveness ``ping`` job works — without
needing a live Redis/Postgres. The v3 jobs are stubs and must raise
``NotImplementedError`` (so nothing silently no-ops).
"""

from __future__ import annotations

import pytest

from app.main import (
    WorkerSettings,
    account_verification_poll,
    lobby_watchdog,
    ping,
    score_corroboration,
)


def test_ping_job_registered() -> None:
    assert ping in WorkerSettings.functions


def test_all_v3_jobs_registered() -> None:
    registered = set(WorkerSettings.functions)
    assert {lobby_watchdog, score_corroboration, account_verification_poll} <= registered


async def test_ping_returns_pong() -> None:
    # ctx is a plain dict in arq; the job only reads ``job_id`` defensively.
    assert await ping({}) == "pong"


@pytest.mark.parametrize(
    "job, arg",
    [
        (lobby_watchdog, "match-1"),
        (score_corroboration, "match-1"),
        (account_verification_poll, "attempt-1"),
    ],
)
async def test_v3_jobs_are_explicit_phase0_stubs(job, arg) -> None:
    with pytest.raises(NotImplementedError):
        await job({}, arg)
