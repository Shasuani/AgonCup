"""Add per-match readiness columns (ready_deadline_at, team_a/b_ready_at).

Match-ready gate (PLAN.md §3.6 / edge-cases §8.5): once a match has both teams
present it becomes *playable* and a **5-minute ready window** is armed
(``ready_deadline_at``). Each side's captain marks their team ready
(``team_a_ready_at`` / ``team_b_ready_at``). At the deadline, a side that readied
while its opponent did not wins by walkover; if neither readied the window is
re-armed. All UTC, all nullable — a match that never reaches "both teams present"
is unaffected.

Revision: 0012 -> down 0011.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_match_readiness"
down_revision: str | None = "0011_tournament_schedule"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("ready_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "matches",
        sa.Column("team_a_ready_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "matches",
        sa.Column("team_b_ready_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("matches", "team_b_ready_at")
    op.drop_column("matches", "team_a_ready_at")
    op.drop_column("matches", "ready_deadline_at")
