"""team_listings.last_seen_at — a post lives only while its poster's client is open

Revision ID: 0007_team_listing_last_seen
Revises: 0006_team_listings
Create Date: 2026-10-07

WHY
===
A team posted on "Find a team" stayed there forever, even after its captain had
closed the desktop client and gone to bed — strangers kept asking to join a team
nobody was watching (owner + Max, 2026-10-07). The poster's client now sends a
keep-alive every minute; a post not heard from for 5 minutes (room for a client
restart) is gone. ``last_seen_at`` is that keep-alive.

Existing rows get ``now()``, so a post made before this deploy gets the same
5-minute grace as any other, and disappears unless its captain runs a client
new enough to keep it alive.

Static DDL, per the baseline's rule: never derive a migration from app.models.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007_team_listing_last_seen"
down_revision: str | None = "0006_team_listings"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "team_listings",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("team_listings", "last_seen_at")
