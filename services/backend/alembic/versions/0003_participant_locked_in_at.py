"""tournament_participants.locked_in_at — lock-in becomes per PLAYER, not per team

Revision ID: 0003_participant_locked_in
Revises: 0002_teams_is_solo
Create Date: 2026-08-10

WHY
===
A team enters a tournament once — the captain redeems the join code and the whole
roster is in, nobody else needs the code (PLAN.md §3.1). But "the team is here" and
"the five of them are actually at their keyboards" are different facts, and only the
second one makes a bracket startable.

Until now ``Registration.status = CHECKED_IN`` carried both: the captain locked the
team in on everyone's behalf, so a roster with three players asleep looked identical
to one ready to play. Lock-in now lands on the individual ``TournamentParticipant``,
and the team's Registration flips to CHECKED_IN only once **every active member** has
locked in (``check_in_self``). The captain's word alone no longer seeds a bracket.

NULL = has not locked in. Existing rows are pre-refactor and correctly read as "not
locked in"; the team-level Registration status they already carry is untouched, so a
tournament mid-flight keeps whatever check-in state it had.

Static DDL, per the baseline's rule: never derive a migration from app.models.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_participant_locked_in"
down_revision: str | None = "0002_teams_is_solo"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tournament_participants",
        sa.Column("locked_in_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tournament_participants", "locked_in_at")
