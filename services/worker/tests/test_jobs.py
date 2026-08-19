"""Phase-0 smoke tests for the arq worker (PLAN.md §7 Phase 0).

These assert the queue *shape* is real — the worker module imports, the jobs are
registered on ``WorkerSettings``, and the liveness ``ping`` job works — without
needing a live Redis/Postgres. The v3 jobs are stubs and must raise
``NotImplementedError`` (so nothing silently no-ops).
"""

from __future__ import annotations

from typing import Any

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
        (account_verification_poll, "attempt-1"),
    ],
)
async def test_v3_jobs_refuse_a_bare_ctx(job, arg) -> None:
    with pytest.raises(NotImplementedError):
        await job({}, arg)


async def test_score_corroboration_refuses_a_bare_ctx() -> None:
    # The sweep takes no match arg (it discovers its own work list), but an
    # unwired ctx (no gateway, no settings) must still refuse to run.
    with pytest.raises(NotImplementedError):
        await score_corroboration({})


async def test_corroboration_sweep_escalates_stalled_matches() -> None:
    from app.main import StalledMatch

    class _FakeGateway:
        def __init__(self) -> None:
            self.marked: list[Any] = []
            self.cutoff: Any = None

        async def list_stalled_matches(self, *, cutoff: Any) -> list[StalledMatch]:
            self.cutoff = cutoff
            return [
                StalledMatch("m-1", "g-1", "t-1", reports_counted=5),
                StalledMatch("m-2", "g-2", "t-2", reports_counted=3),
            ]

        async def mark_disputed_timeout(self, match: StalledMatch, *, ts: Any) -> int:
            self.marked.append(match.match_id)
            return 1

    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)
    gateway = _FakeGateway()
    ctx: dict[str, Any] = {
        "corroboration_gateway": gateway,
        "now_fn": lambda: now,
        "corroboration_timeout_seconds": 120.0,
    }

    outcome = await score_corroboration(ctx)

    assert outcome == "stalled:2 escalated:2"
    assert gateway.marked == ["m-1", "m-2"]
    # The staleness cutoff honors the injected clock and timeout knob.
    assert gateway.cutoff == now - timedelta(seconds=120.0)
