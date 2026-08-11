"""Metadata-level assertions on the ORM models (PLAN.md §2).

These run with no database — they introspect ``Base.metadata`` — and guard the
invariants that the migration + RLS rely on: every expected table exists, every
guild-isolated table actually carries ``guild_id``, and the same-guild composite
FKs are present so a child row cannot reference another guild's parent.
"""

from __future__ import annotations

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from app.models import GUILD_ISOLATED_TABLES, Base

EXPECTED_TABLES = {
    # global / tenant anchor
    "guilds",
    "users",
    "riot_accounts",
    # auth (global)
    "refresh_tokens",
    "oauth_credentials",
    # guild-scoped
    "user_guild_roles",
    "teams",
    "team_members",
    "tournaments",
    "tournament_participants",
    "tournament_hides",
    "registrations",
    "matches",
    "match_lobbies",
    "client_sessions",
    "result_reports",
    "match_results",
    "result_nonces",
    # federation / global side tables
    "central_feed_items",
    "player_central_visibility",
    "verification_attempts",
    "audit_log",
}


def test_all_expected_tables_present() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_guild_isolated_tables_exist_and_carry_guild_id() -> None:
    for name in GUILD_ISOLATED_TABLES:
        table = Base.metadata.tables[name]
        assert "guild_id" in table.columns, f"{name} missing guild_id"
        assert not table.columns["guild_id"].nullable, f"{name}.guild_id must be NOT NULL"


def test_global_tables_are_not_guild_scoped() -> None:
    for name in ("users", "riot_accounts"):
        assert "guild_id" not in Base.metadata.tables[name].columns


def _has_unique_id_guild(table_name: str) -> bool:
    table = Base.metadata.tables[table_name]
    for c in table.constraints:
        if isinstance(c, UniqueConstraint) and {col.name for col in c.columns} == {
            "id",
            "guild_id",
        }:
            return True
    return False


def test_same_guild_invariant_parents_have_composite_unique() -> None:
    # Parents that children reference by (id, guild_id) must expose that key.
    for parent in ("teams", "tournaments", "matches"):
        assert _has_unique_id_guild(parent), f"{parent} needs UNIQUE(id, guild_id)"


def _has_guild_composite_fk(table_name: str) -> bool:
    table = Base.metadata.tables[table_name]
    for c in table.constraints:
        if isinstance(c, ForeignKeyConstraint):
            cols = {col.name for col in c.columns}
            if "guild_id" in cols and len(cols) >= 2:
                return True
    return False


def test_children_use_same_guild_composite_fks() -> None:
    for child in ("registrations", "matches", "match_lobbies", "result_reports", "match_results"):
        assert _has_guild_composite_fk(child), f"{child} must have a (.. , guild_id) composite FK"


def test_tournament_max_teams_default_is_16() -> None:
    col = Base.metadata.tables["tournaments"].columns["max_teams"]
    assert col.server_default is not None
    assert "16" in str(col.server_default.arg)


def test_result_report_unique_prevents_double_report() -> None:
    table = Base.metadata.tables["result_reports"]
    uniques = [
        {col.name for col in c.columns}
        for c in table.constraints
        if isinstance(c, UniqueConstraint)
    ]
    assert {"lcu_game_id", "reporter_puuid", "source"} in uniques
