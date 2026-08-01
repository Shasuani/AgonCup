"""DEV-ONLY end-to-end tournament simulator (test "bots").

Runs the WHOLE tournament flow with fake players so you can watch it in the desktop
client WITHOUT 10 real people + League clients:

    create tournament → seed N bot teams (each with verified fake players) →
    register + check-in → start (generate bracket) → auto-play every match
    (a winner is decided, the bracket advances) round-by-round until a champion.

The only step that is *simulated* (not real) is the match itself: a live LCU custom
game can't be botted, so each match winner is recorded via the real
`results.record_winner` (decided_by=admin_override), which advances the bracket
exactly like a real result would. Everything else uses the real service code paths.

Your real account is added to the first bot team so the tournament shows up in YOUR
desktop "My Tournaments" and you can expand its bracket live.

Usage (from services/backend, with the venv + host env):
    set -a && . ./.env && set +a

    # set up + play out an 8-team 5v5 single-elim in "League of Leagues", 4s/match:
    PYTHONPATH=src .venv/bin/python scripts/simulate_tournament.py

    # options:
    PYTHONPATH=src .venv/bin/python scripts/simulate_tournament.py \
        --guild "League of Leagues" --name "Test Cup" --mode 5v5 --format single \
        --teams 8 --best-of 1 --delay 4

    # 1v1 ARAM, 4 players, double elim:
    PYTHONPATH=src .venv/bin/python scripts/simulate_tournament.py --mode 1v1 --teams 4 --format double

    # just set it up, don't auto-play (you drive results elsewhere):
    PYTHONPATH=src .venv/bin/python scripts/simulate_tournament.py --no-play

    # remove ALL simulator data (bot teams/players + sim tournaments), keep real data:
    PYTHONPATH=src .venv/bin/python scripts/simulate_tournament.py --cleanup
"""

from __future__ import annotations

import argparse
import asyncio
import random
import secrets
import uuid

from lol_schemas.enums import (
    GameMode,
    MatchStatus,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TournamentFormat,
    UserGuildRoleName,
)
from sqlalchemy import delete, func, select, text

from app.db import SessionLocal
from app.joincodes import generate_join_code
from app.models import (
    Match,
    RiotAccount,
    Team,
    TeamMember,
    Tournament,
    User,
    UserGuildRole,
)
from app.results import service as results
from app.tournaments import service as ts

# Markers that make simulator data identifiable + cleanly removable.
SIM_TEAM_PREFIX = "\U0001f916 "  # 🤖
SIM_USER_PREFIX = "\U0001f916 SIM"
SIM_PUUID_PREFIX = "SIM-"
SIM_DISCORD_BASE = 9_100_000_000_000_000_000  # reserved high range, never a real Discord id

BOT_NAMES = [
    "Frostguard",
    "Noxus Reborn",
    "Demacia Elite",
    "Ionia Blades",
    "Shadow Isles",
    "Piltover Prime",
    "Zaun Chaos",
    "Freljord Wolves",
    "Bilgewater Corsairs",
    "Targon Peak",
    "Shurima Sun",
    "Void Spawn",
    "Bandle Crew",
    "Mount Rift",
    "Blue Sentinels",
    "Red Brambles",
]
CHAMPS = [
    "Ashe",
    "Darius",
    "Lux",
    "Yasuo",
    "Jinx",
    "Thresh",
    "Lee Sin",
    "Ahri",
    "Garen",
    "Zed",
]


async def _set_guild(s, guild_id: uuid.UUID) -> None:
    # Session-level (is_local=false) so it survives commits on this connection.
    await s.execute(text("SELECT set_config('app.current_guild', :g, false)"), {"g": str(guild_id)})


async def cleanup(s) -> None:
    await s.execute(text("SET app.current_guild = ''"))
    # Tournaments flagged sim (cascades matches/registrations/results/lobbies).
    sim_tids = (
        (
            await s.execute(
                select(Tournament.id).where(Tournament.ruleset_json["sim"].astext == "true")
            )
        )
        .scalars()
        .all()
    )
    for tid in sim_tids:
        await s.execute(delete(Tournament).where(Tournament.id == tid))
    # Bot teams (cascades team_members incl. your real membership row — you stay).
    await s.execute(delete(Team).where(Team.name.like(f"{SIM_TEAM_PREFIX}%")))
    # Bot users + their accounts/roles.
    sim_uids = (
        (await s.execute(select(User.id).where(User.discord_user_id >= SIM_DISCORD_BASE)))
        .scalars()
        .all()
    )
    if sim_uids:
        await s.execute(delete(RiotAccount).where(RiotAccount.user_id.in_(sim_uids)))
        await s.execute(delete(UserGuildRole).where(UserGuildRole.user_id.in_(sim_uids)))
        await s.execute(delete(User).where(User.id.in_(sim_uids)))
    await s.commit()
    print(
        f"✓ cleanup: removed {len(sim_tids)} sim tournament(s), bot teams, and {len(sim_uids)} bot user(s). Real data untouched."
    )


async def build(
    s,
    *,
    guild_id: uuid.UUID,
    name: str,
    mode: GameMode,
    fmt: TournamentFormat,
    teams: int,
    best_of: int,
    me_user_id: uuid.UUID | None,
) -> Tournament:
    await _set_guild(s, guild_id)

    tour = await ts.create_tournament(
        s,
        name=name,
        format=fmt,
        game_mode=mode,
        best_of=best_of,
        max_teams=max(teams, 2),
    )
    tour.ruleset_json = {"sim": True}
    await s.flush()
    print(
        f"✓ created tournament {name!r}  (id={str(tour.id)[:8]}  code={tour.join_code}  mode={mode.value}  {fmt.value}  Bo{best_of})"
    )

    size = mode.team_size  # 5 for 5v5, 1 for 1v1
    # Continue bot discord ids above any that already exist so re-runs never collide.
    max_disc = (
        await s.execute(
            select(func.max(User.discord_user_id)).where(User.discord_user_id >= SIM_DISCORD_BASE)
        )
    ).scalar()
    counter = (max_disc - SIM_DISCORD_BASE) if max_disc else 0
    for ti in range(teams):
        base = BOT_NAMES[ti % len(BOT_NAMES)]
        team_name = f"{SIM_TEAM_PREFIX}{base}" + ("" if ti < len(BOT_NAMES) else f" {ti}")
        # Members (bots) — first is captain.
        member_users: list[User] = []
        for _ in range(size):
            counter += 1
            champ = random.choice(CHAMPS)
            u = User(
                discord_user_id=SIM_DISCORD_BASE + counter,
                display_name=f"{SIM_USER_PREFIX} {champ}-{secrets.token_hex(2)}",
            )
            s.add(u)
            await s.flush()
            s.add(
                RiotAccount(
                    user_id=u.id,
                    puuid=f"{SIM_PUUID_PREFIX}{uuid.uuid4()}",
                    summoner_id=secrets.token_hex(8),
                    riot_id_game_name=champ,
                    riot_id_tag_line=base[:3].upper(),
                    region="euw1",
                    verified=True,
                    origin_guild_id=guild_id,
                )
            )
            s.add(UserGuildRole(user_id=u.id, guild_id=guild_id, role=UserGuildRoleName.PLAYER))
            member_users.append(u)
        captain = member_users[0]
        team = Team(
            guild_id=guild_id,
            name=team_name,
            captain_user_id=captain.id,
            join_code=generate_join_code(),
        )
        s.add(team)
        await s.flush()
        for j, u in enumerate(member_users):
            s.add(
                TeamMember(
                    team_id=team.id,
                    user_id=u.id,
                    guild_id=guild_id,
                    role=TeamMemberRole.CAPTAIN if j == 0 else TeamMemberRole.PLAYER,
                    status=TeamMemberStatus.ACTIVE,
                )
            )
        # Add YOUR real account to the first team so it shows in your desktop.
        if ti == 0 and me_user_id is not None:
            exists = (
                await s.execute(
                    select(TeamMember).where(
                        TeamMember.team_id == team.id, TeamMember.user_id == me_user_id
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                s.add(
                    TeamMember(
                        team_id=team.id,
                        user_id=me_user_id,
                        guild_id=guild_id,
                        role=TeamMemberRole.PLAYER,
                        status=TeamMemberStatus.ACTIVE,
                    )
                )
                print(f"  ↳ added your account to {team_name!r} (so it appears in your desktop)")
        await s.flush()

        reg = await ts.register_team(s, tournament_id=tour.id, team_id=team.id)
        reg.status = RegistrationStatus.CHECKED_IN
        await s.flush()

    await s.commit()
    print(f"✓ seeded {teams} bot teams ({size} player(s) each), all checked in")

    res = await ts.start_tournament(s, tournament_id=tour.id)
    await s.commit()
    print(
        f"✓ started — bracket: {res.matches_created} matches, {res.rounds} rounds, {res.byes} byes"
    )

    # Auto-advance any first-round byes (walkovers).
    await _set_guild(s, guild_id)
    byes = (
        (
            await s.execute(
                select(Match).where(
                    Match.tournament_id == tour.id, Match.status == MatchStatus.WALKOVER
                )
            )
        )
        .scalars()
        .all()
    )
    for m in byes:
        await results.advance_bye_chain(s, m)
    await s.commit()
    return tour


async def play(
    s, *, tour_id: uuid.UUID, guild_id: uuid.UUID, me_user_id: uuid.UUID | None, delay: float
) -> None:
    print("\n▶ playing out the bracket (winners decided by admin-override; bracket advances):\n")
    round_seen = 0
    while True:
        await _set_guild(s, guild_id)
        matches = (
            (
                await s.execute(
                    select(Match)
                    .where(Match.tournament_id == tour_id)
                    .order_by(Match.round, Match.bracket_slot)
                )
            )
            .scalars()
            .all()
        )
        decidable = [
            m
            for m in matches
            if m.team_a
            and m.team_b
            and m.winner_team_id is None
            and m.status not in (MatchStatus.COMPLETED, MatchStatus.WALKOVER)
        ]
        if not decidable:
            break
        m = decidable[0]
        if m.round != round_seen:
            round_seen = m.round
            print(f"── Round {m.round} ──")
        winner = random.choice([m.team_a, m.team_b])
        # record_winner defaults decided_by=ADMIN_OVERRIDE and advances the bracket.
        await results.record_winner(
            s,
            match_id=m.id,
            winner_team_id=winner,
            admin_override_by=me_user_id,
            admin_override_reason="simulator",
        )
        await s.commit()
        a, b = await _team_name(s, m.team_a), await _team_name(s, m.team_b)
        w = await _team_name(s, winner)
        print(f"   {a}  vs  {b}   →  🏆 {w}")
        await asyncio.sleep(delay)

    # Champion = winner of the highest-round completed match.
    await _set_guild(s, guild_id)
    finals = (
        (
            await s.execute(
                select(Match)
                .where(Match.tournament_id == tour_id, Match.winner_team_id.isnot(None))
                .order_by(Match.round.desc())
            )
        )
        .scalars()
        .all()
    )
    if finals:
        champ = await _team_name(s, finals[0].winner_team_id)
        print(f"\n🏆 CHAMPION: {champ}\n")


async def _team_name(s, team_id: uuid.UUID | None) -> str:
    if team_id is None:
        return "TBD"
    t = (await s.execute(select(Team.name).where(Team.id == team_id))).scalar_one_or_none()
    return t or str(team_id)[:8]


async def _resolve_guild(s, name: str):
    from app.models import Guild

    return (await s.execute(select(Guild).where(Guild.name == name))).scalar_one_or_none()


async def _resolve_me(s, guild_id: uuid.UUID, display_name: str | None):
    from app.models import Guild  # noqa: F401

    # Prefer an explicit display name; else the (single) real linked account's user.
    if display_name:
        u = (
            await s.execute(
                select(User).where(
                    User.display_name == display_name, User.discord_user_id < SIM_DISCORD_BASE
                )
            )
        ).scalar_one_or_none()
        if u:
            return u.id
    row = (
        (
            await s.execute(
                select(RiotAccount.user_id).where(~RiotAccount.puuid.like(f"{SIM_PUUID_PREFIX}%"))
            )
        )
        .scalars()
        .first()
    )
    return row


async def main() -> None:
    p = argparse.ArgumentParser(description="Simulate a full tournament with bot players.")
    p.add_argument("--guild", default="League of Leagues")
    p.add_argument("--name", default=None)
    p.add_argument("--mode", choices=["5v5", "1v1"], default="5v5")
    p.add_argument("--format", choices=["single", "double"], default="single")
    p.add_argument("--teams", type=int, default=8)
    p.add_argument("--best-of", type=int, default=1)
    p.add_argument("--delay", type=float, default=4.0)
    p.add_argument(
        "--me",
        default="Shasuani",
        help="your display name (added to team 1 so it shows in your desktop)",
    )
    p.add_argument("--no-me", action="store_true", help="don't add your real account to a team")
    p.add_argument("--no-play", action="store_true", help="set up only; don't auto-play matches")
    p.add_argument("--cleanup", action="store_true", help="remove all simulator data and exit")
    args = p.parse_args()

    async with SessionLocal() as s:
        if args.cleanup:
            await cleanup(s)
            return

        guild = await _resolve_guild(s, args.guild)
        if guild is None:
            print(f"guild {args.guild!r} not found. Available guilds:")
            from app.models import Guild

            for g in (await s.execute(select(Guild))).scalars():
                print(f"  - {g.name}")
            raise SystemExit(1)

        me_id = None if args.no_me else await _resolve_me(s, guild.id, args.me)
        mode = GameMode.SUMMONERS_RIFT_5V5 if args.mode == "5v5" else GameMode.ARAM_1V1
        fmt = (
            TournamentFormat.SINGLE_ELIMINATION
            if args.format == "single"
            else TournamentFormat.DOUBLE_ELIMINATION
        )
        name = args.name or f"Sim Cup {secrets.token_hex(2)}"

        tour = await build(
            s,
            guild_id=guild.id,
            name=name,
            mode=mode,
            fmt=fmt,
            teams=args.teams,
            best_of=args.best_of,
            me_user_id=me_id,
        )

        if args.no_play:
            print(
                "\n(set-up only) Open your desktop → Tournaments → expand it to watch. Run again without --no-play to auto-play."
            )
            return

        await play(s, tour_id=tour.id, guild_id=guild.id, me_user_id=me_id, delay=args.delay)
        print("Open your desktop → Tournaments → expand the tournament to see the final bracket.")
        print("When done testing, remove the bots with:  scripts/simulate_tournament.py --cleanup")


if __name__ == "__main__":
    asyncio.run(main())
