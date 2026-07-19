"""Add tournament scheduling columns (registration_opens_at, start_announced_at).

Scheduled tournaments (PLAN.md §3.2): an admin sets when registration opens and
when the tournament starts. ``registration_opens_at`` gates enrolment and drives
the DRAFT→OPEN + announce transition; the pre-existing ``start_at`` is the
scheduled kickoff; ``start_announced_at`` is the once-only guard for the "starting"
announcement. All UTC, all nullable — an unscheduled tournament is unaffected.

Revision: 0011 -> down 0010.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_tournament_schedule"
down_revision: str | None = "0010_team_member_status"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tournaments",
        sa.Column("registration_opens_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tournaments",
        sa.Column("start_announced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tournaments", "start_announced_at")
    op.drop_column("tournaments", "registration_opens_at")
