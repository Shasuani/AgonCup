"""Join codes (tournaments + teams) and the tournament-participant pool.

Adds the client-first join flow (PLAN.md §3.1): a shareable ``join_code`` on
``tournaments`` and ``teams`` (unique, ambiguity-free) plus a
``tournament_participants`` pool a user lands in when they redeem a tournament
code from the desktop. The participant table is guild-scoped, so it gets the same
``guild_isolation`` RLS policy as its siblings; a same-guild composite FK to
``tournaments(id, guild_id)`` makes cross-server enrolment structurally impossible.

Existing tournament/team rows are backfilled with a generated code so the column
is populated everywhere (kept nullable so the unique index tolerates the brief
pre-backfill window and any future legacy import).

Revision: 0009 -> down 0008.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.joincodes import generate_join_code

revision: str = "0009_join_codes_participants"
down_revision: str | None = "0008_best_of_series"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_APP_ROLE = "app_rw"


def _backfill_codes(table: str) -> None:
    """Assign a fresh unique join code to every existing row of ``table``."""
    bind = op.get_bind()
    ids = bind.execute(sa.text(f"SELECT id FROM {table} WHERE join_code IS NULL")).scalars().all()
    used: set[str] = set()
    for row_id in ids:
        code = generate_join_code()
        while code in used:
            code = generate_join_code()
        used.add(code)
        bind.execute(
            sa.text(f"UPDATE {table} SET join_code = :c WHERE id = :i"),
            {"c": code, "i": row_id},
        )


def upgrade() -> None:
    # 1) join_code columns (nullable + unique) + backfill existing rows.
    op.add_column("tournaments", sa.Column("join_code", sa.Text(), nullable=True))
    op.add_column("teams", sa.Column("join_code", sa.Text(), nullable=True))
    _backfill_codes("tournaments")
    _backfill_codes("teams")
    op.create_unique_constraint("uq_tournaments_join_code", "tournaments", ["join_code"])
    op.create_unique_constraint("uq_teams_join_code", "teams", ["join_code"])

    # 2) tournament_participants pool (guild-scoped; same-guild composite FK).
    op.create_table(
        "tournament_participants",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tournament_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("guild_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tournament_participants"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_tournament_participants_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["guild_id"],
            ["guilds.id"],
            name="fk_tournament_participants_guild_id_guilds",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tournament_id", "guild_id"],
            ["tournaments.id", "tournaments.guild_id"],
            name="fk_tournament_participants_tournament_guild",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tournament_id", "user_id", name="uq_tournament_participants_tournament_user"
        ),
    )
    op.create_index("ix_tournament_participants_user_id", "tournament_participants", ["user_id"])
    op.create_index("ix_tournament_participants_guild_id", "tournament_participants", ["guild_id"])

    # 3) Guild RLS on the new table (mirror 0002's guild_isolation policy).
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON tournament_participants TO {_APP_ROLE}")
    op.execute("ALTER TABLE tournament_participants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tournament_participants FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS guild_isolation ON tournament_participants")
    op.execute(
        """
        CREATE POLICY guild_isolation ON tournament_participants
            USING (guild_id = NULLIF(current_setting('app.current_guild', true), '')::uuid)
            WITH CHECK (guild_id = NULLIF(current_setting('app.current_guild', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS guild_isolation ON tournament_participants")
    op.execute("ALTER TABLE tournament_participants NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tournament_participants DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_tournament_participants_guild_id", table_name="tournament_participants")
    op.drop_index("ix_tournament_participants_user_id", table_name="tournament_participants")
    op.drop_table("tournament_participants")
    op.drop_constraint("uq_teams_join_code", "teams", type_="unique")
    op.drop_constraint("uq_tournaments_join_code", "tournaments", type_="unique")
    op.drop_column("teams", "join_code")
    op.drop_column("tournaments", "join_code")
