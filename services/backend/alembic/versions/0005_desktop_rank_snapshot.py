"""desktop rank snapshot + advisory LCU puuid on riot_accounts

Revision ID: 0005_desktop_rank_snapshot
Revises: 0004_desktop_code_verification
Create Date: 2026-06-22

PHASE 3 — DESKTOP RANK SNAPSHOT (PLAN.md §3.1 / §3.5)
====================================================
A pure additive ALTER of the GLOBAL (non-RLS) ``riot_accounts`` table. No new
tables, no RLS changes, no booleans (so the ``sa.false()`` gotcha is N/A).

At desktop-verify time the backend already holds a fresh Riot API call (the
canonical-PUUID resolve), so it opportunistically reads the player's Solo/Duo
rank via League-V4 ``entries/by-puuid`` and snapshots it here. Storing the
snapshot lets the admin SPA / future tournament seeding display rank without
re-hitting League-V4 (rate-budget friendly) and gives a durable record. All
columns are NULLable — rank is best-effort (unranked / fetch-failed leaves them
NULL) and the advisory LCU UUID is optional.

  * ``lcu_puuid`` TEXT NULL — the advisory 36-char LCU UUID (distinct from the
    canonical 78-char ``puuid``), a secondary possession signal.
  * ``rank_tier`` / ``rank_division`` / ``rank_queue`` TEXT NULL — Solo/Duo tier
    (``"GOLD"``), roman division (``"II"``, empty for apex tiers), and queue.
  * ``rank_lp`` INTEGER NULL — league points.
  * ``rank_checked_at`` TIMESTAMPTZ NULL — snapshot freshness.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_desktop_rank_snapshot"
down_revision: str | None = "0004_desktop_code_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("riot_accounts", sa.Column("lcu_puuid", sa.Text(), nullable=True))
    op.add_column("riot_accounts", sa.Column("rank_tier", sa.Text(), nullable=True))
    op.add_column("riot_accounts", sa.Column("rank_division", sa.Text(), nullable=True))
    op.add_column("riot_accounts", sa.Column("rank_lp", sa.Integer(), nullable=True))
    op.add_column("riot_accounts", sa.Column("rank_queue", sa.Text(), nullable=True))
    op.add_column(
        "riot_accounts",
        sa.Column("rank_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("riot_accounts", "rank_checked_at")
    op.drop_column("riot_accounts", "rank_queue")
    op.drop_column("riot_accounts", "rank_lp")
    op.drop_column("riot_accounts", "rank_division")
    op.drop_column("riot_accounts", "rank_tier")
    op.drop_column("riot_accounts", "lcu_puuid")
