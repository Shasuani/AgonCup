"""Add ``team_members.status`` (approval lifecycle: pending → active).

The team-leader approval flow (PLAN.md §3.1): a player redeeming a team join code
lands as a ``pending`` membership request; the captain approves it (→ ``active``)
or rejects it (row deleted). The captain's own membership is ``active``. Reuses the
project's ``pg_enum`` (VARCHAR + ORM-level validation) so the migration and
``app.models`` metadata cannot drift; existing rows backfill to ``active`` via the
server default, so pre-flow rosters stay intact.

Revision: 0010 -> down 0009.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from lol_schemas.enums import TeamMemberStatus

from app.models.tables import pg_enum

revision: str = "0010_team_member_status"
down_revision: str | None = "0009_join_codes_participants"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "team_members",
        sa.Column(
            "status",
            pg_enum(TeamMemberStatus, "team_member_status"),
            nullable=False,
            server_default=TeamMemberStatus.ACTIVE.value,
        ),
    )


def downgrade() -> None:
    op.drop_column("team_members", "status")
