"""Add ``tournaments.game_mode`` (5v5 Rift vs 1v1 ARAM).

Threads a per-tournament custom-game shape so a duel tournament can run on the
Howling Abyss (ARAM, teamSize 1) instead of the default 5v5 Summoner's Rift. The
column reuses the project's ``pg_enum`` (VARCHAR storing the enum value, ORM-level
validated) so the migration and ``app.models`` metadata cannot drift; existing
rows backfill to the 5v5 default via the server default.

Revision: 0007 -> down 0006.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from lol_schemas.enums import GameMode

from app.models.tables import pg_enum

revision: str = "0007_tournament_game_mode"
down_revision: str | None = "0006_refresh_token_device"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tournaments",
        sa.Column(
            "game_mode",
            pg_enum(GameMode, "game_mode"),
            nullable=False,
            server_default=GameMode.SUMMONERS_RIFT_5V5.value,
        ),
    )


def downgrade() -> None:
    op.drop_column("tournaments", "game_mode")
