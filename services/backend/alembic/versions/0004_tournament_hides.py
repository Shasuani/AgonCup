"""tournament_hides — a player can clear a finished tournament off their own list

Revision ID: 0004_tournament_hides
Revises: 0003_participant_locked_in
Create Date: 2026-08-11

WHY
===
``/tournaments/mine`` only ever grows. Every tournament a player has ever entered
stays on their client's screen forever, so after a season the list is a wall and
the one tournament they actually care about is buried in it.

This is the per-user dismissal: one row = "user U no longer wants to see
tournament T". It hides the tournament from *that* user's list and nobody else's.
Deliberately NOT a deletion — the tournament, its bracket and its results are the
guild's record and an admin still sees all of it (PLAN.md §3.1).

Only a **terminal** tournament (completed / cancelled) can be hidden; the service
enforces that, not the schema. Hiding one you still have to play would remove your
own way back to it.

Guild-scoped like everything else: FORCE RLS + the standard ``guild_isolation``
policy, and a same-guild composite FK to ``tournaments(id, guild_id)`` so the row
dies with the tournament (CASCADE) and can never point at another guild's.
``(tournament_id, user_id)`` is unique — hiding twice is idempotent.

Static DDL, per the baseline's rule: never derive a migration from app.models.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_tournament_hides"
down_revision: str | None = "0003_participant_locked_in"
branch_labels: str | None = None
depends_on: str | None = None

_GUILD_ISOLATION_POLICY = """
    CREATE POLICY guild_isolation ON tournament_hides
        USING (guild_id = NULLIF(current_setting('app.current_guild', true), '')::uuid)
        WITH CHECK (guild_id = NULLIF(current_setting('app.current_guild', true), '')::uuid)
"""


def upgrade() -> None:
    op.create_table(
        "tournament_hides",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("guild_id", sa.UUID(), nullable=False),
        sa.Column("tournament_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["guild_id"],
            ["guilds.id"],
            name=op.f("fk_tournament_hides_guild_id_guilds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tournament_id", "guild_id"],
            ["tournaments.id", "tournaments.guild_id"],
            name="fk_tournament_hides_tournament_guild",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tournament_hides_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tournament_hides")),
        sa.UniqueConstraint("tournament_id", "user_id", name="uq_tournament_hides_tournament_user"),
    )
    op.create_index(
        op.f("ix_tournament_hides_guild_id"), "tournament_hides", ["guild_id"], unique=False
    )
    op.create_index(
        op.f("ix_tournament_hides_user_id"), "tournament_hides", ["user_id"], unique=False
    )

    # Same posture as every other guild-scoped table: FORCE so even the table owner
    # is policed, and fail closed (zero rows) when the tenant GUC is unset.
    op.execute("ALTER TABLE tournament_hides ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tournament_hides FORCE ROW LEVEL SECURITY")
    op.execute(_GUILD_ISOLATION_POLICY)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tournament_hides TO app_rw")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS guild_isolation ON tournament_hides")
    op.drop_index(op.f("ix_tournament_hides_user_id"), table_name="tournament_hides")
    op.drop_index(op.f("ix_tournament_hides_guild_id"), table_name="tournament_hides")
    op.drop_table("tournament_hides")
