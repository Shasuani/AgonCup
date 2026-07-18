"""DEV-ONLY test helper: simulate a second player requesting to join a team.

Given a team's join code, this creates a throwaway "player" user (with a `player`
role in that team's guild) and submits a PENDING join request — exactly what a real
second desktop client would produce. You then see the request pop up as a ✓/✗
notification in YOUR desktop client and can approve/reject it, exercising the full
team-leader approval flow WITHOUT a second Discord account.

Usage (from services/backend, with the venv + host env):
    set -a && . ./.env && set +a
    PYTHONPATH=src .venv/bin/python scripts/sim_joiner.py <TEAM_CODE> [display_name]

Example:
    PYTHONPATH=src .venv/bin/python scripts/sim_joiner.py Z5MZ55H4 "Faker"
"""

from __future__ import annotations

import asyncio
import secrets
import sys

from sqlalchemy import select, text

from app.db import SessionLocal
from app.joincodes import normalize_join_code
from app.models import Team, User, UserGuildRole
from app.teams import service
from lol_schemas.enums import UserGuildRoleName


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: sim_joiner.py <TEAM_CODE> [display_name]")
        raise SystemExit(2)

    code = normalize_join_code(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else f"Test Joiner {secrets.token_hex(2)}"

    async with SessionLocal() as s, s.begin():
        # Find the team by its join code (owner session bypasses RLS locally).
        team = (
            await s.execute(select(Team).where(Team.join_code == code))
        ).scalar_one_or_none()
        if team is None:
            print(f"no team with code {code!r} — create the team first, then copy its code")
            raise SystemExit(1)

        # Act inside the team's guild so RLS-scoped writes land correctly.
        await s.execute(
            text("SELECT set_config('app.current_guild', :g, true)"), {"g": str(team.guild_id)}
        )

        joiner = User(discord_user_id=secrets.randbelow(10**17) + 10**17, display_name=name)
        s.add(joiner)
        await s.flush()
        s.add(
            UserGuildRole(
                user_id=joiner.id, guild_id=team.guild_id, role=UserGuildRoleName.PLAYER
            )
        )
        await s.flush()

        _, member = await service.join_by_code(s, code=code, user_id=joiner.id)
        print(
            f"✓ {name!r} requested to join {team.name!r} (status={member.status.value}).\n"
            f"  Open your desktop client — the ✓/✗ notification should appear top-left."
        )


if __name__ == "__main__":
    asyncio.run(main())
