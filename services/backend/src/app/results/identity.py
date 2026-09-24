"""Who is this scoreboard line? — League-client ids -> the linked account (PLAN.md §3.6.5).

A report's ``teams[].players[]`` lines come straight out of the League client,
and the client does not know our canonical identity. Verified 2026-09-24 against
a live client: every ``puuid`` the LCU exposes (``current-summoner``, match
history and therefore the eog-stats-block) is the **36-char LCU UUID**, not the
78-char Account-V1 PUUID the roster is keyed on. A line can also carry the
numeric ``summonerId`` and, mid-game (``:2999``), only a ``riotId``. All three
are stored on :class:`RiotAccount` at verify / lock-in time (``lcu_puuid``,
``summoner_id``, the Riot ID display fields) — this module is the one place
that turns any of them back into the canonical PUUID, so adjudication and
match history agree on who played.

Resolution order per line: canonical or LCU ``puuid`` → ``summonerId`` → Riot ID
(``riotId``, or ``riotIdGameName#riotIdTagLine``, or a ``summonerName`` that
already reads ``name#tag``), casefolded. A Riot ID shared by two linked accounts
resolves to neither rather than guessing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RiotAccount, User


@dataclass(frozen=True)
class LinkedPlayer:
    """The linked account a scoreboard line resolved to."""

    puuid: str
    """The canonical (Account-V1) PUUID — the roster / check-in key."""
    display_name: str | None
    riot_id: str | None


def riot_id_key(value: object) -> str | None:
    """Casefolded ``name#tag`` join key for a Riot ID string, or ``None``."""
    if not isinstance(value, str) or "#" not in value:
        return None
    name, _, tag = value.partition("#")
    name, tag = name.strip(), tag.strip()
    if not name or not tag:
        return None
    return f"{name.casefold()}#{tag.casefold()}"


def line_riot_id_key(line: Mapping[str, Any]) -> str | None:
    """The Riot ID join key a scoreboard line carries, whichever field it used."""
    key = riot_id_key(line.get("riotId"))
    if key is not None:
        return key
    game_name, tag_line = line.get("riotIdGameName"), line.get("riotIdTagLine")
    if isinstance(game_name, str) and isinstance(tag_line, str):
        key = riot_id_key(f"{game_name}#{tag_line}")
        if key is not None:
            return key
    return riot_id_key(line.get("summonerName"))


def line_summoner_id(line: Mapping[str, Any]) -> str | None:
    """The numeric summoner id on a line as the string the account stores."""
    raw = line.get("summonerId")
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return str(int(raw))
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


class ScoreboardIdentities:
    """Lookup from any id a League client puts on a scoreboard to the linked account."""

    def __init__(self) -> None:
        self._by_puuid: dict[str, LinkedPlayer] = {}
        self._by_summoner_id: dict[str, LinkedPlayer] = {}
        self._by_riot_id: dict[str, LinkedPlayer] = {}
        self._ambiguous_riot_ids: set[str] = set()

    def add(
        self,
        *,
        puuid: str,
        lcu_puuid: str | None,
        summoner_id: str | None,
        game_name: str | None,
        tag_line: str | None,
        display_name: str | None,
    ) -> None:
        riot_id = f"{game_name}#{tag_line}" if game_name and tag_line else None
        player = LinkedPlayer(puuid=str(puuid), display_name=display_name, riot_id=riot_id)
        self._by_puuid[str(puuid)] = player
        if lcu_puuid and lcu_puuid.strip():
            self._by_puuid[lcu_puuid.strip()] = player
        if summoner_id and summoner_id.strip():
            self._by_summoner_id[summoner_id.strip()] = player
        key = riot_id_key(riot_id)
        if key is not None and key not in self._ambiguous_riot_ids:
            other = self._by_riot_id.get(key)
            if other is not None and other.puuid != player.puuid:
                del self._by_riot_id[key]
                self._ambiguous_riot_ids.add(key)
            else:
                self._by_riot_id[key] = player

    def resolve(self, line: Mapping[str, Any]) -> LinkedPlayer | None:
        """The linked account behind one ``players[]`` line, or ``None``."""
        puuid = line.get("puuid")
        if isinstance(puuid, str) and puuid in self._by_puuid:
            return self._by_puuid[puuid]
        summoner_id = line_summoner_id(line)
        if summoner_id is not None and summoner_id in self._by_summoner_id:
            return self._by_summoner_id[summoner_id]
        key = line_riot_id_key(line)
        if key is not None:
            return self._by_riot_id.get(key)
        return None

    def __len__(self) -> int:
        return len({p.puuid for p in self._by_puuid.values()})


_ACCOUNT_COLUMNS = (
    RiotAccount.puuid,
    RiotAccount.lcu_puuid,
    RiotAccount.summoner_id,
    RiotAccount.riot_id_game_name,
    RiotAccount.riot_id_tag_line,
    User.display_name,
)


def _fill(identities: ScoreboardIdentities, rows: Iterable[Any]) -> ScoreboardIdentities:
    for puuid, lcu_puuid, summoner_id, game_name, tag_line, display_name in rows:
        identities.add(
            puuid=str(puuid),
            lcu_puuid=lcu_puuid,
            summoner_id=summoner_id,
            game_name=game_name,
            tag_line=tag_line,
            display_name=display_name,
        )
    return identities


async def load_identities_for_lines(
    session: AsyncSession, lines: Iterable[Mapping[str, Any]]
) -> ScoreboardIdentities:
    """Resolve every linked account the given scoreboard lines could refer to.

    One query over the global ``RiotAccount``/``User`` tables, matching each of
    the id spaces a line may carry. Names match on the game name only (the tag
    is checked in :meth:`ScoreboardIdentities.resolve`), so a renamed tag never
    hides a player from their own history.
    """
    puuids: set[str] = set()
    summoner_ids: set[str] = set()
    game_names: set[str] = set()
    for line in lines:
        puuid = line.get("puuid")
        if isinstance(puuid, str) and puuid:
            puuids.add(puuid)
        sid = line_summoner_id(line)
        if sid is not None:
            summoner_ids.add(sid)
        key = line_riot_id_key(line)
        if key is not None:
            game_names.add(key.split("#", 1)[0])
    identities = ScoreboardIdentities()
    clauses = []
    if puuids:
        clauses.append(RiotAccount.puuid.in_(puuids))
        clauses.append(RiotAccount.lcu_puuid.in_(puuids))
    if summoner_ids:
        clauses.append(RiotAccount.summoner_id.in_(summoner_ids))
    if game_names:
        clauses.append(func.lower(RiotAccount.riot_id_game_name).in_(game_names))
    if not clauses:
        return identities
    rows = (
        await session.execute(
            select(*_ACCOUNT_COLUMNS)
            .join(User, RiotAccount.user_id == User.id)
            .where(or_(*clauses))
        )
    ).all()
    return _fill(identities, rows)


async def load_identities_for_roster(
    session: AsyncSession, puuids: Iterable[str]
) -> ScoreboardIdentities:
    """The linked accounts of a known roster (canonical PUUIDs), every id space filled."""
    wanted = sorted({str(p) for p in puuids if p})
    identities = ScoreboardIdentities()
    if not wanted:
        return identities
    rows = (
        await session.execute(
            select(*_ACCOUNT_COLUMNS)
            .join(User, RiotAccount.user_id == User.id)
            .where(RiotAccount.puuid.in_(wanted))
        )
    ).all()
    return _fill(identities, rows)
