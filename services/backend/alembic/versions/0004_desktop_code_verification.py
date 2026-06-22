"""desktop code + LCU account verification (replaces the icon challenge)

Revision ID: 0004_desktop_code_verification
Revises: 0003_auth
Create Date: 2026-06-22

PHASE 3 — DESKTOP CODE VERIFICATION (PLAN.md §3.1 account verification)
======================================================================
A pure ALTER of the existing (GLOBAL, non-RLS) ``verification_attempts`` table
plus an additive ALTER of ``riot_accounts``. No new tables, no RLS changes.

The icon-based ownership challenge (random profile icon + Account-V1 + worker
poll) is replaced by a one-time, human-typeable CODE minted at ``/register``,
bound to (discord user, origin guild). The desktop client reads the local LCU
current-summoner PUUID and POSTs it back with the code to complete the link.

``verification_attempts`` changes:
  * ``code`` VARCHAR(12) NULL — the one-time link credential (de-hyphenated). A
    partial-unique index keeps only LIVE (``status='pending'``) codes collision
    free; expired/consumed/legacy NULL rows don't block reuse.
  * ``origin_guild_id`` UUID NULL FK — the guild the user ran /register from.
  * ``verify_kind`` VARCHAR(16) NOT NULL DEFAULT 'icon' + CHECK — lets the worker
    poll skip code rows. Legacy rows default to 'icon'.
  * ``target_icon_id`` / ``puuid`` / ``nonce`` relaxed to NULLABLE — code rows
    have no icon, learn the PUUID only when the desktop POSTs, and never use a
    nonce. The existing UNIQUE on ``nonce`` ignores NULLs in Postgres.

``riot_accounts`` change:
  * ``origin_guild_id`` UUID NULL FK — durable "guild registered from", copied
    from the attempt at verify time (the attempt may later be pruned).

Forward-only project: downgrade reverts the NOT NULL relaxations, which may fail
on a populated DB (rows with NULL puuid/nonce/icon). Acceptable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

# revision identifiers, used by Alembic.
revision: str = "0004_desktop_code_verification"
down_revision: str | None = "0003_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- verification_attempts: new columns -------------------------------
    op.add_column(
        "verification_attempts",
        sa.Column("code", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "verification_attempts",
        sa.Column(
            "origin_guild_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("guilds.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "verification_attempts",
        sa.Column(
            "verify_kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'icon'"),
        ),
    )

    # --- verification_attempts: relax NOT NULL for the code flow ----------
    op.alter_column("verification_attempts", "target_icon_id", nullable=True)
    op.alter_column("verification_attempts", "puuid", nullable=True)
    op.alter_column("verification_attempts", "nonce", nullable=True)

    # --- verification_attempts: constraints + indexes ---------------------
    op.create_check_constraint(
        "verify_kind_allowed",
        "verification_attempts",
        "verify_kind in ('icon','desktop_code')",
    )
    # Only one LIVE code may exist at a time; expired/consumed/NULL rows ignored.
    op.create_index(
        "uq_verification_attempts_code_active",
        "verification_attempts",
        ["code"],
        unique=True,
        postgresql_where=sa.text("code IS NOT NULL AND status = 'pending'"),
    )
    op.create_index(
        "ix_verification_attempts_origin_guild_id",
        "verification_attempts",
        ["origin_guild_id"],
    )

    # --- riot_accounts: durable origin guild ------------------------------
    op.add_column(
        "riot_accounts",
        sa.Column(
            "origin_guild_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("guilds.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_riot_accounts_origin_guild_id",
        "riot_accounts",
        ["origin_guild_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_riot_accounts_origin_guild_id", table_name="riot_accounts")
    op.drop_column("riot_accounts", "origin_guild_id")

    op.drop_index(
        "ix_verification_attempts_origin_guild_id", table_name="verification_attempts"
    )
    op.drop_index(
        "uq_verification_attempts_code_active", table_name="verification_attempts"
    )
    op.drop_constraint(
        "verify_kind_allowed", "verification_attempts", type_="check"
    )

    # Revert NOT NULL relaxations (may fail on a populated DB — forward-only).
    op.alter_column("verification_attempts", "nonce", nullable=False)
    op.alter_column("verification_attempts", "puuid", nullable=False)
    op.alter_column("verification_attempts", "target_icon_id", nullable=False)

    op.drop_column("verification_attempts", "verify_kind")
    op.drop_column("verification_attempts", "origin_guild_id")
    op.drop_column("verification_attempts", "code")
