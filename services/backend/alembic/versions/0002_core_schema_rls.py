"""core v3 schema + Row-Level Security (guild isolation)

Revision ID: 0002_core_schema_rls
Revises: 0001_baseline
Create Date: 2026-06-20

PHASE 1 — DATA MODEL + ISOLATION (PLAN.md §2, §1.4, §7, §9)
==========================================================
Creates the full v3 ORM schema (everything except the ``guilds`` tenant anchor,
which the 0001 baseline already created) and lands multi-tenant isolation:

1. **Tables** — created from ``app.models.Base.metadata`` so the migration and the
   ORM can never drift for the initial schema. ``checkfirst`` skips ``guilds``.
2. **Non-BYPASSRLS application role** (``app_rw``) — the role the backend and
   worker connect as. It is **not** the table owner and is **NOBYPASSRLS**, so
   ``FORCE ROW LEVEL SECURITY`` policies actually bind to it. (Superuser/owner —
   used for migrations — always bypasses RLS, which is why the app must NOT
   connect as the superuser; see app/db.py + APP_DATABASE_URL.)
3. **Per-guild RLS** on every guild-scoped table (``GUILD_ISOLATED_TABLES``):
   ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` + a ``guild_isolation`` policy keyed
   on ``current_setting('app.current_guild', true)::uuid``. ``SET LOCAL`` of that
   GUC happens per transaction in app/db.py. The ``, true`` (missing_ok) makes an
   **unset** GUC return NULL → ``guild_id = NULL`` is never true → **fail closed**
   (no rows leak when the tenant isn't set).

The federation tables (``central_feed_items``, ``audit_log``,
``player_central_visibility``, ``verification_attempts``) are created but get
their bespoke policies with the features that use them — they are intentionally
NOT under the strict per-guild policy.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.models import Base
from app.models.tables import GUILD_ISOLATED_TABLES

# revision identifiers, used by Alembic.
revision: str = "0002_core_schema_rls"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The application role the backend/worker connect as (NOBYPASSRLS).
APP_ROLE = "app_rw"
# Dev-only password; production injects a real one via the secrets manager and
# rotates it. The role is created idempotently so re-running is safe.
_DEV_APP_ROLE_PASSWORD = "app_rw"


def _new_tables() -> list:
    """Tables this migration owns — everything in metadata except the baseline
    ``guilds`` anchor, in dependency order."""
    return [t for t in Base.metadata.sorted_tables if t.name != "guilds"]


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Tables (checkfirst so an existing table — e.g. guilds — is skipped).
    for table in _new_tables():
        table.create(bind, checkfirst=True)

    # 2) Non-BYPASSRLS application role + privileges.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{_DEV_APP_ROLE_PASSWORD}' NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    # Future tables created by the migration owner are auto-granted to the app role.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )

    # 3) Per-guild RLS on guild-scoped tables.
    for table_name in GUILD_ISOLATED_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY guild_isolation ON {table_name}
                USING (guild_id = current_setting('app.current_guild', true)::uuid)
                WITH CHECK (guild_id = current_setting('app.current_guild', true)::uuid)
            """
        )


def downgrade() -> None:
    bind = op.get_bind()

    for table_name in GUILD_ISOLATED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS guild_isolation ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")

    # Revoke (role itself is cluster-wide; leave it to avoid breaking other DBs).
    op.execute(
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}"
    )

    for table in reversed(_new_tables()):
        table.drop(bind, checkfirst=True)
