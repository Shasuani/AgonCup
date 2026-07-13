"""Add best-of-N series support: ``tournaments.best_of`` + ``matches`` series state.

A match becomes a series won by the first team to ``best_of // 2 + 1`` games
(1 = single game, the default; 3 = best-of-three; 5 = best-of-five). Matches gain
per-side game-win counters plus the set of already-counted LCU game ids so a
re-sent report for a decided game never double-counts. Existing rows backfill to
best_of=1 / 0-0 / empty via the server defaults.

Revision: 0008 -> down 0007.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_best_of_series"
down_revision: str | None = "0007_tournament_game_mode"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tournaments",
        sa.Column("best_of", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("matches", sa.Column("wins_a", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("matches", sa.Column("wins_b", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "matches",
        sa.Column(
            "scored_game_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("matches", "scored_game_ids")
    op.drop_column("matches", "wins_b")
    op.drop_column("matches", "wins_a")
    op.drop_column("tournaments", "best_of")
