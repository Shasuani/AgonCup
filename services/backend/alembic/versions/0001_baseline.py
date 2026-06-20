"""baseline: minimal guilds table + RLS posture marker

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-20

PHASE-0 BASELINE
================
This migration is intentionally minimal. It creates the global ``guilds`` table
(the tenant anchor, PLAN.md §2) plus a ``schema_migrations_marker`` row sentinel,
so the stack has *something* real to migrate against and the async Alembic env is
exercised end-to-end.

RLS / ROLE POSTURE — TO BE EXPANDED IN PHASE 1 (PLAN.md §1, §2, §7)
------------------------------------------------------------------
The full multi-tenant isolation lands with the first *guild-scoped* migration:

  * ``ALTER TABLE <t> ENABLE ROW LEVEL SECURITY``  and  ``... FORCE ROW LEVEL
    SECURITY`` on every guild-scoped table (FORCE so even the table owner is
    subject to policy).
  * A dedicated **non-BYPASSRLS** application role (``app_rw``) that the backend
    and worker connect as; a *separate* privileged role for migrations and the
    federation/platform-operator path.
  * Per-table policies keyed on the request GUC, e.g.::

        CREATE POLICY guild_isolation ON <t>
            USING (guild_id = current_setting('app.current_guild')::uuid);

    paired with ``SET LOCAL app.current_guild`` per transaction (see app/db.py).

``guilds`` itself is a *global* entity (not guild-scoped — it IS the tenant), so
it is NOT placed under guild_id RLS here. This migration only establishes the
table; the policy/role wiring is deferred to keep Phase 0 import-safe and free of
environment-specific role assumptions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guilds",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("discord_guild_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "settings",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_central",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "invited_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('active','suspended','removed')",
            name="ck_guilds_status",
        ),
    )

    # Lightweight marker table: a deterministic place for a future
    # data-presence/seed sentinel without coupling to feature tables yet.
    op.create_table(
        "schema_migrations_marker",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("note", sa.Text(), nullable=False),
    )
    op.bulk_insert(
        sa.table(
            "schema_migrations_marker",
            sa.column("key", sa.Text),
            sa.column("note", sa.Text),
        ),
        [
            {
                "key": "baseline",
                "note": "Phase-0 baseline; RLS/roles land in Phase 1 (see module docstring).",
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("schema_migrations_marker")
    op.drop_table("guilds")
