"""Declarative base, naming conventions, and shared column helpers.

A single ``Base`` whose ``metadata`` is wired into Alembic (``alembic/env.py``)
so autogenerate diffs against these models. The naming convention makes every
constraint name deterministic (stable migrations + nameable RLS policies).

Tenancy (PLAN.md §2): ``User``/``RiotAccount`` are **global**; every other
entity is **guild-scoped** and carries ``guild_id`` so Postgres Row-Level
Security can isolate tenants. Guild-scoped models mix in :class:`GuildScoped`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint naming (Alembic stability; RLS policies reference tables).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    """Server-generated UUID primary key (``gen_random_uuid()``)."""
    return mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )


def created_at_col() -> Mapped[datetime]:
    """Timezone-aware creation timestamp, defaulted server-side."""
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TimestampMixin:
    """Adds a server-defaulted ``created_at``."""

    created_at: Mapped[datetime] = created_at_col()


class GuildScoped:
    """Marker + ``guild_id`` column for tenant-scoped tables.

    Every table mixing this in is placed under guild RLS by the core migration
    (``ENABLE``/``FORCE ROW LEVEL SECURITY`` + a ``guild_isolation`` policy keyed
    on ``current_setting('app.current_guild')``). The presence of this mixin is
    what the migration and the isolation tests enumerate, so isolation can never
    silently skip a new guild-scoped table.
    """

    guild_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("guilds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


# Re-exported for models that need a raw big-int Discord snowflake column type.
DiscordSnowflake = BigInteger
