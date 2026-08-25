"""tournaments.region — every tournament is played on ONE Riot platform

Revision ID: 0005_tournament_region
Revises: 0004_tournament_hides
Create Date: 2026-08-25

WHY
===
A verified Riot account lives on exactly one platform (``euw1``, ``eun1``, ...),
and an LCU lobby invite is addressed by a per-server ``summonerId`` — an EUW
client physically cannot invite an EUNE player. Until now a tournament had no
region at all, so a mixed-region field only failed at match time, with a lobby
quietly missing players.

The tournament now carries the platform it is played on, chosen at creation.
The join/lock-in gates require entrants to hold a verified account on that
platform, so the mismatch surfaces at enrolment — not mid-bracket.

NULL = created before this migration (or region-less on purpose): no region
restriction, exactly the old behaviour. Values are ``PlatformRoute`` strings,
the same vocabulary ``riot_accounts.region`` already uses; KR is rejected by
the service layer (Korea is out of scope — docs/compliance-checklist.md).

Static DDL, per the baseline's rule: never derive a migration from app.models.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tournament_region"
down_revision: str | None = "0004_tournament_hides"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("tournaments", sa.Column("region", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tournaments", "region")
