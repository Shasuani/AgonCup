"""teams.is_solo — mark the auto-created one-player team behind a solo enrolment

Revision ID: 0002_teams_is_solo
Revises: 0001_baseline
Create Date: 2026-08-08

WHY
===
Solo game modes (``aram_1v1``) enrol a lone player, but the whole downstream
pipeline — bracket seeding, matches, ready-up, lobby orchestration, results —
keys on ``team_id``. Rather than duplicate every one of those paths for a
"participant", a solo join now creates a **one-player team named after the
player** and registers it normally (PLAN.md §3.1/§3.2).

That team is an implementation detail: the player never creates it, never sees
it in "my teams", and it must not make them look like a captain of a real team
(``_resolve_captain_team`` would otherwise raise ``AmbiguousCaptainTeamError``
for anyone who had ever joined a 1v1). ``is_solo`` is the explicit marker every
one of those queries filters on — an implicit discriminator (e.g. "join_code IS
NULL") would silently rot the first time a real team is created without a code.

Static DDL, per the baseline's rule: never derive a migration from app.models.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_teams_is_solo"
down_revision: str | None = "0001_baseline"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column("is_solo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("teams", "is_solo")
