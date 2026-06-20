"""Tests for the adjudication stub contract and the lol-schemas import smoke."""

from __future__ import annotations

from uuid import uuid4

import pytest

from lol_core import (
    QUORUM_THRESHOLD,
    TOTAL_PARTICIPANTS,
    AdjudicationOutcome,
    adjudicate_result,
)


def test_quorum_constants() -> None:
    assert QUORUM_THRESHOLD == 6
    assert TOTAL_PARTICIPANTS == 10


def test_adjudication_outcomes_present() -> None:
    assert {o.value for o in AdjudicationOutcome} == {"accept", "dispute", "insufficient"}


def test_adjudicate_result_is_phase0_stub() -> None:
    team_a, team_b = uuid4(), uuid4()
    roster = {team_a: frozenset({"p1"}), team_b: frozenset({"p2"})}
    with pytest.raises(NotImplementedError):
        adjudicate_result([], roster)


def test_lol_schemas_importable_from_core_context() -> None:
    # lol_core depends on lol_schemas via a path dep; ensure the canonical DTOs
    # resolve in this environment (cross-package smoke).
    from lol_schemas import LobbyPhase, MatchResultDTO, ResultReportDTO, TournamentDTO

    assert LobbyPhase.IN_GAME.value == "in_game"
    t = TournamentDTO(id=uuid4(), guild_id=uuid4(), name="Smoke Cup")
    assert t.max_teams == 16
    assert ResultReportDTO is not None
    assert MatchResultDTO is not None
