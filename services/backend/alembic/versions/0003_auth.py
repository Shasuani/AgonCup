"""auth core: roles, refresh tokens, oauth credentials + RLS on user_guild_roles

Revision ID: 0003_auth
Revises: 0002_core_schema_rls
Create Date: 2026-06-20

PHASE 1 — AUTH CORE (PLAN.md §3.1 auth, §7 Phase 1, §9)
=======================================================
Adds the auth tables to the v3 schema:

* ``user_guild_roles`` — per-(user, guild) RBAC role. GUILD-SCOPED, so it gets the
  same ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` + ``guild_isolation`` policy as
  every other guild-scoped table (the role lookup is naturally per-current-guild).
* ``refresh_tokens`` — rotating/revocable refresh tokens stored HASHED. GLOBAL.
* ``oauth_credentials`` — Discord OAuth tokens stored ENCRYPTED. GLOBAL.

Pattern mirrors 0002: create only the NEW tables from ``Base.metadata`` (so the
migration and ORM never drift), GRANT them to the non-BYPASSRLS ``app_rw`` role
(0002 also sets DEFAULT PRIVILEGES, but we GRANT explicitly so this migration is
self-contained), and ENABLE+FORCE RLS + the ``guild_isolation`` policy on the one
new guild-scoped table.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.models import Base

# revision identifiers, used by Alembic.
revision: str = "0003_auth"
down_revision: str | None = "0002_core_schema_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The application role the backend/worker connect as (created in 0002).
APP_ROLE = "app_rw"

# Tables this migration owns (created here, dropped on downgrade).
_NEW_TABLES = ("user_guild_roles", "refresh_tokens", "oauth_credentials")

# The single new guild-scoped table that gets the per-guild RLS policy. Kept
# explicit (rather than re-deriving GUILD_ISOLATED_TABLES) so this migration only
# touches the tables IT creates — 0002 already covers the rest.
_NEW_RLS_TABLES = ("user_guild_roles",)


def _tables_to_create() -> list:
    """The metadata Table objects this migration owns, in dependency order."""
    return [t for t in Base.metadata.sorted_tables if t.name in _NEW_TABLES]


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Tables (checkfirst so a partially-applied run is safe).
    for table in _tables_to_create():
        table.create(bind, checkfirst=True)

    # 2) Grant the new tables to the non-BYPASSRLS app role. (0002's DEFAULT
    #    PRIVILEGES likely already covered these, but GRANT is idempotent and
    #    keeps the migration self-contained.)
    for name in _NEW_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {name} TO {APP_ROLE}")

    # 3) Per-guild RLS on the new guild-scoped table (mirror 0002's policy SQL).
    for table_name in _NEW_RLS_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY guild_isolation ON {table_name}
                USING (guild_id = current_setting('app.current_guild', true)::uuid)
                WITH CHECK (guild_id = current_setting('app.current_guild', true)::uuid)
            """
        )

    # 3b) SELF-READ policy on user_guild_roles. Login/refresh must read a user's
    #     OWN role rows ACROSS guilds to choose a default acting guild + bake role
    #     claims — but at that point no `app.current_guild` GUC is set, so the
    #     guild_isolation policy alone returns zero rows for the app_rw role
    #     (FORCE RLS). This second, permissive, SELECT-only policy is OR'd with
    #     guild_isolation: a session that sets `app.current_user` to the
    #     authenticated user id can read that user's role rows regardless of the
    #     guild GUC. Writes stay guild-scoped (this policy is FOR SELECT only).
    op.execute(
        """
        CREATE POLICY self_role_read ON user_guild_roles
            FOR SELECT
            USING (user_id = current_setting('app.current_user', true)::uuid)
        """
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.execute("DROP POLICY IF EXISTS self_role_read ON user_guild_roles")
    for table_name in _NEW_RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS guild_isolation ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")

    # Drop in reverse dependency order.
    for table in reversed(_tables_to_create()):
        table.drop(bind, checkfirst=True)
