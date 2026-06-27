"""refresh-token device metadata (desktop pairing)

Revision ID: 0006_refresh_token_device
Revises: 0005_desktop_rank_snapshot
Create Date: 2026-06-27

PHASE 3 — DESKTOP PAIRING (PLAN.md §3.1)
========================================
A pure additive ALTER of the GLOBAL (non-RLS) ``refresh_tokens`` table. No new
tables, no RLS changes, no booleans — the ``sa.false()`` gotcha is N/A here (the
rule still stands for any FUTURE boolean ``server_default``: use ``sa.false()``,
never ``func.false()``, which compiles to a non-existent ``false()`` and breaks
``alembic upgrade head``).

The desktop pairing flow (``/auth/desktop-pair``) issues a refresh token tagged
with the human ``device_label`` the user supplied (and an optional opaque
``device_id``) so future device-management UX can list/revoke per-device sessions.
Both columns are NULLable: existing rows, the OAuth-login/dev-login pairs, and
``/auth/refresh`` rotations all carry no label.

  * ``device_label`` TEXT NULL — human label, e.g. "Kostas' MacBook".
  * ``device_id``    TEXT NULL — opaque per-install device id (advisory).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_refresh_token_device"
down_revision: str | None = "0005_desktop_rank_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("device_label", sa.Text(), nullable=True))
    op.add_column("refresh_tokens", sa.Column("device_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("refresh_tokens", "device_id")
    op.drop_column("refresh_tokens", "device_label")
