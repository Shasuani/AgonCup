"""team_listings — a team posts "looking for players", every server sees it

Revision ID: 0006_team_listings
Revises: 0005_tournament_region
Create Date: 2026-10-05

WHY
===
A captain with three or four players had no way to find the rest except asking
around their own Discord server. The desktop client now has a "Find a team"
screen: a captain posts their team, and a player on ANY server that runs AgonCup
can see it and ask to join.

One row = one team's open post. ``team_id`` is unique: a team has at most one
listing, and posting again means deleting the old one first (owner decision
2026-10-05). The composite FK to ``teams(id, guild_id)`` makes ``guild_id`` the
team's real server and removes the listing when the team is disbanded.

Deliberately NOT under the per-guild ``guild_isolation`` policy — like
``central_feed_items`` it is cross-tenant by design (the baseline's federation
tables). It holds only what the captain chose to publish: region + a short
message. The service reads the listed teams themselves guild by guild.

Static DDL, per the baseline's rule: never derive a migration from app.models.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_team_listings"
down_revision: str | None = "0005_tournament_region"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "team_listings",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("guild_id", sa.UUID(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["guild_id"],
            ["guilds.id"],
            name=op.f("fk_team_listings_guild_id_guilds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "guild_id"],
            ["teams.id", "teams.guild_id"],
            name="fk_team_listings_team_guild",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_team_listings_created_by_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_listings")),
        sa.UniqueConstraint("team_id", name="uq_team_listings_team"),
    )
    op.create_index(op.f("ix_team_listings_guild_id"), "team_listings", ["guild_id"], unique=False)
    op.create_index(op.f("ix_team_listings_region"), "team_listings", ["region"], unique=False)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON team_listings TO app_rw")


def downgrade() -> None:
    op.drop_index(op.f("ix_team_listings_region"), table_name="team_listings")
    op.drop_index(op.f("ix_team_listings_guild_id"), table_name="team_listings")
    op.drop_table("team_listings")
