"""lobby_watchdog job tests (PLAN.md §3.6.1).

Offline by design — no Redis, no Postgres. The DB collaborator is injected
through ``ctx["lobby_gateway"]`` (a fake) and the clock through ``ctx["now_fn"]``,
so these exercise the *real* watchdog flow: load the ``MatchLobby``, compute the
missed-heartbeat count, delegate the per-phase host-drop decision to the pure
``lol_core.orchestration.decide_failover`` (NOT reimplemented here), and apply the
resulting action via the fenced ``(match_id, host_epoch)`` UPDATE + an AuditLog
event.

Every ``decide_failover`` branch is covered through the job:

* **PROMOTE**: pre-game drop, host still connected → lossless promote at a new
  epoch; fenced UPDATE + audit.
* **RECREATE**: pre-game drop, host gone → re-create by the next opted-in
  successor at a new epoch; lobby driven back to ``AWAITING_CREATE`` + audit.
* **NOOP within grace**: heartbeat still fresh → no write, no audit.
* **NOOP IN_GAME**: a host drop while ``IN_GAME`` is a hard no-op (game runs
  server-side) → no write, no audit.
* **NOOP phase-advanced**: gameflow legitimately advanced → suppress failover.
* **ABORT_TO_MANUAL on epoch exhaustion**: bounded epochs spent → terminal
  ``HOST_LOST`` + audit (manual lobby UI fallback).
* **ABORT_TO_MANUAL on no standby**: host gone, empty succession → manual.
* **superseded**: the fenced UPDATE matches 0 rows (another worker won) → no
  audit.
* **missing**: the lobby row is gone → no-op.
* **bare ctx**: unwired → ``NotImplementedError`` (Phase-0 smoke test honesty).

Mirrors the existing worker test style (``tests/test_account_verification.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from lol_schemas import LobbyPhase

from app.main import LobbyRecord, lobby_watchdog

_NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)


@dataclass
class _FakeLobbyGateway:
    record: LobbyRecord | None
    rows_updated: int = 1
    live: list[tuple[str, str]] = field(default_factory=list)
    applied: list[dict[str, Any]] = field(default_factory=list)
    audits: list[dict[str, Any]] = field(default_factory=list)
    loaded_with: list[tuple[str, str | None]] = field(default_factory=list)

    async def load_lobby(self, match_id: str, *, guild_id: str | None = None) -> LobbyRecord | None:
        self.loaded_with.append((match_id, guild_id))
        return self.record

    async def list_live_lobbies(self) -> list[tuple[str, str]]:
        return list(self.live)

    async def apply_failover(
        self,
        *,
        match_id: str,
        guild_id: str,
        expected_epoch: int,
        new_epoch: int | None,
        new_host_puuid: str | None,
        new_state: LobbyPhase,
    ) -> int:
        self.applied.append(
            {
                "match_id": match_id,
                "guild_id": guild_id,
                "expected_epoch": expected_epoch,
                "new_epoch": new_epoch,
                "new_host_puuid": new_host_puuid,
                "new_state": new_state,
            }
        )
        return self.rows_updated

    async def write_audit_log(
        self,
        *,
        guild_id: str,
        action: str,
        entity: str,
        before: dict[str, Any],
        after: dict[str, Any],
        ts: datetime,
    ) -> None:
        self.audits.append(
            {
                "guild_id": guild_id,
                "action": action,
                "entity": entity,
                "before": before,
                "after": after,
                "ts": ts,
            }
        )


def _lobby(
    *,
    state: LobbyPhase = LobbyPhase.FILLING,
    host_puuid: str | None = "HOST-0",
    host_succession: tuple[str, ...] = ("HOST-0", "SUCC-1", "SUCC-2"),
    host_epoch: int = 0,
    heartbeat_age_seconds: float | None = 60.0,
    host_connected: bool = False,
    gameflow_advanced: bool = False,
    phase: LobbyPhase | None = None,
) -> LobbyRecord:
    """Build a ``LobbyRecord`` whose heartbeat is ``heartbeat_age_seconds`` old.

    ``heartbeat_age_seconds=None`` models a lobby whose host never beat (maximally
    stale). The default 60s age is well past the 3-beat * 3s grace window, so the
    default lobby is "stale" unless a test overrides the age.
    """
    if heartbeat_age_seconds is None:
        last_hb = None
    else:
        last_hb = _NOW - timedelta(seconds=heartbeat_age_seconds)
    return LobbyRecord(
        match_id="match-1",
        guild_id="guild-1",
        state=state,
        host_puuid=host_puuid,
        host_succession=host_succession,
        host_epoch=host_epoch,
        last_host_heartbeat_at=last_hb,
        host_connected=host_connected,
        gameflow_advanced=gameflow_advanced,
        phase=phase,
    )


def _ctx(gateway: _FakeLobbyGateway, **overrides: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {"lobby_gateway": gateway, "now_fn": lambda: _NOW}
    ctx.update(overrides)
    return ctx


# --------------------------------------------------------------------------- #
# Acted failover branches
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_promote_when_host_still_connected() -> None:
    # Pre-game drop, host flapped but still connected → lossless PROMOTE at a new
    # epoch (current host kept), fenced on the observed epoch, audited.
    gateway = _FakeLobbyGateway(record=_lobby(host_connected=True, host_epoch=0))
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "promote"
    assert gateway.applied == [
        {
            "match_id": "match-1",
            "guild_id": "guild-1",  # the RLS scope travels with the write
            "expected_epoch": 0,
            "new_epoch": 1,
            "new_host_puuid": "HOST-0",  # promote keeps the current host
            "new_state": LobbyPhase.FILLING,  # phase preserved (lossless)
        }
    ]
    assert len(gateway.audits) == 1
    audit = gateway.audits[0]
    assert audit["action"] == "lobby_failover"
    assert audit["entity"] == "match_lobby"
    assert audit["guild_id"] == "guild-1"
    assert audit["after"]["action"] == "promote"
    assert audit["after"]["host_epoch"] == 1


@pytest.mark.asyncio
async def test_recreate_when_host_gone() -> None:
    # Pre-game drop, host gone (not connected) → RECREATE by the next opted-in
    # successor at a new epoch; lobby driven back to AWAITING_CREATE.
    gateway = _FakeLobbyGateway(record=_lobby(host_connected=False, host_epoch=0))
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "recreate"
    assert gateway.applied[0]["new_host_puuid"] == "SUCC-1"  # next successor
    assert gateway.applied[0]["new_epoch"] == 1
    assert gateway.applied[0]["new_state"] == LobbyPhase.AWAITING_CREATE
    assert gateway.audits[0]["after"]["action"] == "recreate"


@pytest.mark.asyncio
async def test_starting_recreates_when_gameflow_not_advanced() -> None:
    # STARTING (champ-select) + gameflow NOT advanced + host gone → RECREATE.
    gateway = _FakeLobbyGateway(
        record=_lobby(state=LobbyPhase.STARTING, host_connected=False, gameflow_advanced=False)
    )
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "recreate"
    assert gateway.applied[0]["new_state"] == LobbyPhase.AWAITING_CREATE


# --------------------------------------------------------------------------- #
# NO-OP branches (no write, no audit)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_noop_within_grace_window() -> None:
    # Fresh heartbeat (3s old, < 3-beat * 3s grace) → benign no-op, no write.
    gateway = _FakeLobbyGateway(record=_lobby(heartbeat_age_seconds=3.0))
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "noop_ingame"
    assert gateway.applied == []
    assert gateway.audits == []


@pytest.mark.asyncio
async def test_noop_in_game_is_hard_no_op() -> None:
    # Host drop while IN_GAME is a HARD no-op: the match runs server-side and
    # survives client death — even with a maximally stale heartbeat.
    gateway = _FakeLobbyGateway(record=_lobby(state=LobbyPhase.IN_GAME, heartbeat_age_seconds=None))
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "noop_ingame"
    assert gateway.applied == []
    assert gateway.audits == []


@pytest.mark.asyncio
async def test_noop_when_phase_legitimately_advanced() -> None:
    # Stale heartbeat BUT the freshest gameflow phase advanced past `state`
    # (READY -> STARTING): legitimate progress, suppress failover.
    gateway = _FakeLobbyGateway(record=_lobby(state=LobbyPhase.READY, phase=LobbyPhase.STARTING))
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "noop_ingame"
    assert gateway.applied == []
    assert gateway.audits == []


@pytest.mark.asyncio
async def test_noop_starting_when_gameflow_advanced() -> None:
    # STARTING + gameflow advanced to GameStart/InProgress → the game launched;
    # do NOT re-create (no-op to IN_GAME), even with a stale heartbeat.
    gateway = _FakeLobbyGateway(
        record=_lobby(state=LobbyPhase.STARTING, host_connected=False, gameflow_advanced=True)
    )
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "noop_ingame"
    assert gateway.applied == []
    assert gateway.audits == []


# --------------------------------------------------------------------------- #
# ABORT_TO_MANUAL branches
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_epoch_exhaustion_aborts_to_manual() -> None:
    # Bounded epochs spent (epoch == max_epochs) → ABORT_TO_MANUAL: terminal
    # HOST_LOST, no epoch bump, audited (manual lobby UI fallback).
    gateway = _FakeLobbyGateway(record=_lobby(host_epoch=3, host_connected=False))
    result = await lobby_watchdog(_ctx(gateway, lobby_max_epochs=3), "match-1")

    assert result == "abort_to_manual"
    assert gateway.applied[0]["new_state"] == LobbyPhase.HOST_LOST
    assert gateway.applied[0]["new_epoch"] is None  # no lease bump on abort
    assert gateway.applied[0]["new_host_puuid"] == "HOST-0"  # host preserved
    assert gateway.audits[0]["after"]["action"] == "abort_to_manual"


@pytest.mark.asyncio
async def test_no_standby_successor_aborts_to_manual() -> None:
    # Host gone and the succession chain is exhausted (only the dead host in it)
    # → no opted-in standby remains → ABORT_TO_MANUAL.
    gateway = _FakeLobbyGateway(
        record=_lobby(host_connected=False, host_succession=("HOST-0",), host_epoch=0)
    )
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "abort_to_manual"
    assert gateway.applied[0]["new_state"] == LobbyPhase.HOST_LOST


# --------------------------------------------------------------------------- #
# Fencing / idempotency / liveness
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_superseded_when_fenced_update_matches_zero_rows() -> None:
    # The fenced (match_id, host_epoch) UPDATE matched 0 rows: another worker
    # already advanced the epoch. No audit (we did not act).
    gateway = _FakeLobbyGateway(record=_lobby(host_connected=True), rows_updated=0)
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "superseded"
    assert len(gateway.applied) == 1  # we attempted the fenced UPDATE
    assert gateway.audits == []  # but did not win, so nothing audited


@pytest.mark.asyncio
async def test_missing_lobby_is_noop() -> None:
    gateway = _FakeLobbyGateway(record=None)
    result = await lobby_watchdog(_ctx(gateway), "ghost")

    assert result == "missing"
    assert gateway.applied == []
    assert gateway.audits == []


@pytest.mark.asyncio
async def test_never_heartbeat_lobby_fails_over() -> None:
    # A lobby whose host never beat (last_host_heartbeat_at is NULL) is maximally
    # stale and must fail over rather than hang forever.
    gateway = _FakeLobbyGateway(record=_lobby(heartbeat_age_seconds=None, host_connected=False))
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "recreate"


@pytest.mark.asyncio
async def test_future_dated_heartbeat_counts_as_fresh() -> None:
    # Clock skew: a future-dated heartbeat must not be read as "missed" → no-op.
    gateway = _FakeLobbyGateway(record=_lobby(heartbeat_age_seconds=-10.0))
    result = await lobby_watchdog(_ctx(gateway), "match-1")

    assert result == "noop_ingame"
    assert gateway.applied == []


@pytest.mark.asyncio
async def test_bare_ctx_raises_not_implemented() -> None:
    # No injected gateway and no settings → the engine is unwired, so the job
    # refuses to run (keeps the Phase-0 queue-shape smoke test honest).
    with pytest.raises(NotImplementedError):
        await lobby_watchdog({}, "match-1")


# ---------------------------------------------------------------------------
# The cron sweep — the timer that finally drives the watchdog
# ---------------------------------------------------------------------------
async def test_sweep_ticks_every_live_lobby_with_its_guild() -> None:
    from app.main import lobby_watchdog_sweep

    gateway = _FakeLobbyGateway(
        record=_lobby(state=LobbyPhase.FILLING, heartbeat_age_seconds=60.0),
        live=[("m-1", "g-1"), ("m-2", "g-2")],
    )
    ctx: dict[str, Any] = {"lobby_gateway": gateway, "now_fn": lambda: _NOW}

    outcome = await lobby_watchdog_sweep(ctx)

    # Both lobbies ticked, each loaded under ITS OWN guild (the RLS scope).
    assert gateway.loaded_with == [("m-1", "g-1"), ("m-2", "g-2")]
    # A 60s-stale FILLING host fails over on both ticks.
    assert outcome == "swept:2 acted:2"
    assert len(gateway.applied) == 2


async def test_sweep_survives_one_failing_tick() -> None:
    from app.main import lobby_watchdog_sweep

    class _ExplodingGateway(_FakeLobbyGateway):
        async def load_lobby(
            self, match_id: str, *, guild_id: str | None = None
        ) -> LobbyRecord | None:
            if match_id == "m-boom":
                raise RuntimeError("db hiccup")
            return await super().load_lobby(match_id, guild_id=guild_id)

    gateway = _ExplodingGateway(
        record=_lobby(state=LobbyPhase.IN_GAME),  # hard NO-OP for the survivor
        live=[("m-boom", "g-1"), ("m-2", "g-2")],
    )
    ctx: dict[str, Any] = {"lobby_gateway": gateway, "now_fn": lambda: _NOW}

    outcome = await lobby_watchdog_sweep(ctx)

    # The failing lobby is skipped; the healthy one still ticks (as a no-op).
    assert outcome == "swept:2 acted:0"
