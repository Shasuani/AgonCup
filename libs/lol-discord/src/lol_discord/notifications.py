"""Notification + central-feed embed builders (PLAN.md §3.2).

Pure, framework-light builder functions that return Discord embed *dicts* (the
shape consumed by ``discord.Embed.from_dict``). They layer on top of
:func:`lol_discord.embeds.build_embed` so this module imports no ``discord.py``
and is trivially unit-testable.

Two audiences are served, with a hard privacy boundary between them:

* **Guild-local notifications** — registration / team / tournament / match
  lifecycle messages surfaced inside the originating guild. These may name the
  acting Discord user (the guild already knows who they are).
* **Central-feed items** — the consented, federated projection (PLAN.md §3.2,
  "Consented central feed"; data model §2 ``CentralFeedItem.projected_fields``).
  These cross the tenant boundary, so they MUST NOT carry a PUUID or a raw
  Discord-id — only field-whitelisted, projected data. :func:`build_central_feed_item`
  enforces this by construction (it accepts a typed, narrow projection and
  scrubs any stray identifier keys).

All builders take already-resolved display strings; resolving ids -> names and
applying per-player visibility consent is the backend's job. This module only
shapes the embed.
"""

from __future__ import annotations

from typing import Any

from lol_discord.embeds import EmbedColor, EmbedField, build_embed

# Keys that must never appear inside a central-feed embed field (federation
# privacy, PLAN.md §3.2 / §2 PlayerCentralVisibility). Matched case-insensitively
# against projected-field keys as a defence-in-depth guard.
_FORBIDDEN_CENTRAL_KEYS = frozenset(
    {
        "puuid",
        "discord_id",
        "discord_user_id",
        "user_id",
        "summoner_id",
        "summoner_name",
        "encrypted_puuid",
    }
)


def _field(name: str, value: str, *, inline: bool = False) -> EmbedField:
    return {"name": name, "value": value, "inline": inline}


# --------------------------------------------------------------------------- #
# Registration                                                                 #
# --------------------------------------------------------------------------- #
def registration_code(*, code: str, expires_minutes: int = 15) -> dict[str, Any]:
    """Sign-in code: paste it in the desktop app to sign in and link (PLAN.md §3.5).

    The player receives a one-time ``code`` (issued to their authenticated Discord
    identity) which they paste into the AgonCup desktop app to **sign in**.
    The desktop then reads the live League client (LCU) to obtain the player's
    PUUID and completes the account link — so there is no icon dance and no Riot ID
    required. The code is a secret credential, so the embed is always sent
    ephemerally.
    """
    return build_embed(
        "Paste this code in the AgonCup desktop app to sign in",
        "Use the code below to sign in to the AgonCup desktop app, which "
        "then links your League account.",
        color=EmbedColor.WARNING,
        fields=[
            _field("Your code", f"`{code}`", inline=True),
            _field(
                "Steps",
                "1. Open the AgonCup desktop app.\n"
                "2. Make sure League is running and you're signed in.\n"
                "3. Paste this code to sign in and link your account.",
            ),
            _field("Expires", f"in {expires_minutes} minutes", inline=True),
        ],
    )


def registration_verified(
    *,
    riot_id: str,
    region: str | None = None,
    rank_text: str | None = None,
) -> dict[str, Any]:
    """Verification succeeded — the canonical PUUID is now bound to the account (§3.5).

    Optionally surfaces the resolved ``region`` (display label, e.g. ``"EUNE"``)
    and the Solo/Duo ``rank_text`` snapshot (e.g. ``"Gold II, 45 LP"`` or
    ``"Unranked"``). Both are rendered as inline fields only when provided, so
    existing callers (and the central log-only path) keep working unchanged.
    """
    fields: list[EmbedField] = []
    if region:
        fields.append(_field("Region", region, inline=True))
    if rank_text:
        fields.append(_field("Solo/Duo", rank_text, inline=True))
    return build_embed(
        "Account verified",
        f"**{riot_id}** is verified and linked. You can now join teams and tournaments.",
        color=EmbedColor.SUCCESS,
        fields=fields or None,
    )


def lobby_credentials(
    *,
    lobby_name: str,
    lobby_password: str,
    tournament_name: str,
    opponent_name: str | None = None,
    mode_text: str | None = None,
    automated: bool = True,
) -> dict[str, Any]:
    """The custom-lobby name + password for a match, DMed to the people who need it.

    The client normally creates the lobby and invites everyone, but that is an
    LCU write and writes fail: opted out, kill-switch, League not running, a
    patch that moved the endpoint. When it does, whoever is waiting has nothing
    to act on. These are the same backend-owned credentials the client would
    have used, so a lobby made by hand is the *same* lobby.

    ``automated=False`` means no client opted in to *hosting*, so the custom game
    itself will not appear on its own — this reader is the one person asked to make
    it, and telling them to wait for their client would be a promise the backend
    knows it cannot keep. Only the create is theirs to do: their client invites the
    rest of the roster the moment the lobby exists, so the message must not send
    them chasing nine people by hand either.
    """
    fields: list[EmbedField] = [
        _field("Lobby name", f"`{lobby_name}`", inline=True),
        _field("Password", f"`{lobby_password}`", inline=True),
    ]
    # The game's shape (mode, map, win rule) leads the field list: the name and
    # password say where to meet, this says WHAT to set up — without it the
    # creator is guessing map, team size and, for a 1v1, when the game ends.
    if mode_text:
        fields.append(_field("Game", mode_text))
    if opponent_name:
        fields.append(_field("Opponent", opponent_name))
    # Weave the shape into the instruction itself too — a field table is easy to
    # skim past, and the sentence is what the creator acts on.
    game_phrase = f" ({mode_text})" if mode_text else ""
    versus = f" against **{opponent_name}**" if opponent_name else ""
    if automated:
        description = (
            f"**{tournament_name}** — your client will create this lobby"
            f"{game_phrase} and invite everyone. If that does not happen, "
            "make a custom game with the name and password below and the others "
            "can join it directly."
        )
    else:
        description = (
            f"**{tournament_name}** — **you are opening this match**{versus}. "
            f"Create a **custom game**{game_phrase} with the name and password "
            "below. Leave the AgonCup client running: as soon as the lobby "
            "exists it invites the other players for you, and their clients accept "
            "automatically."
        )
    return build_embed(
        "Your match lobby",
        description,
        color=EmbedColor.DEFAULT,
        fields=fields,
    )


def registration_approved(*, tournament_name: str, team_name: str) -> dict[str, Any]:
    """A team's tournament registration was approved (§3.2).

    Distinct from account verification — announces a TEAM joining a TOURNAMENT,
    not a Riot ID (fixes the prior mislabel that rendered a team name as a
    'verified Riot account').
    """
    return build_embed(
        "Registration approved",
        f"**{team_name}** is registered for **{tournament_name}**.",
        color=EmbedColor.SUCCESS,
    )


def tournament_announced(*, tournament_name: str, headline: str, detail: str) -> dict[str, Any]:
    """A scheduled tournament milestone — registration opened, or it is starting
    (PLAN.md §3.2). ``headline`` is the banner, ``detail`` the pre-formatted body
    (join code + times). Posted to the guild's public notify channel."""
    return build_embed(
        f"{tournament_name} — {headline}",
        detail,
        color=EmbedColor.DEFAULT,
    )


# --------------------------------------------------------------------------- #
# Teams                                                                        #
# --------------------------------------------------------------------------- #
def team_created(
    *, team_name: str, captain_name: str, team_id: str | None = None
) -> dict[str, Any]:
    """A new team was created (PLAN.md §2 Team). Surfaces the ``team_id`` so it can
    be registered into a tournament (``/tournament join <tournament_id> <team_id>``)."""
    fields = [_field("Captain", captain_name, inline=True)]
    if team_id:
        # Short 8-char id — commands also accept the team name or this short id.
        fields.append(_field("Team id", f"`{team_id[:8]}` (or use the name)", inline=False))
    return build_embed(
        "Team created",
        f"**{team_name}** is ready to roster up.",
        color=EmbedColor.SUCCESS,
        fields=fields,
    )


def team_joined(*, team_name: str, member_name: str, member_count: int) -> dict[str, Any]:
    """A player joined an existing team (PLAN.md §2 TeamMember)."""
    return build_embed(
        "Roster updated",
        f"**{member_name}** joined **{team_name}**.",
        color=EmbedColor.DEFAULT,
        fields=[_field("Roster size", str(member_count), inline=True)],
    )


# --------------------------------------------------------------------------- #
# Tournament lifecycle (PLAN.md §2 TournamentStatus)                           #
# --------------------------------------------------------------------------- #
def tournament_created(
    *,
    name: str,
    fmt: str | None = None,
    tournament_id: str | None = None,
    mode: str | None = None,
    best_of: int | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Tournament drafted/created. ``fmt`` is the bracket format display string.

    ``region`` is the display label of the platform the tournament is played on
    ("EUNE"/"EUW"/...): entrants need a verified account there, so the admin
    should see it on the confirmation. Omitted = unrestricted, no field shown.

    ``fmt`` is optional: every tournament is a knockout bracket now (owner
    decision — the single/double question is asked nowhere), so callers omit it
    and the embed shows no Format field rather than a constant.

    Surfaces the ``tournament_id`` (needed for ``/tournament join``) plus the game
    mode + series length when the caller has them, so the admin gets everything to
    run it in one message.
    """
    fields = [_field("Format", fmt, inline=True)] if fmt else []
    if mode:
        pretty = "1v1 ARAM" if mode == "aram_1v1" else "5v5 Summoner's Rift"
        fields.append(_field("Mode", pretty, inline=True))
    if best_of:
        fields.append(_field("Series", f"Best of {best_of}", inline=True))
    if region:
        fields.append(_field("Region", region, inline=True))
    if tournament_id:
        # Show the short 8-char id — commands also accept the name or this short id.
        fields.append(
            _field("Tournament id", f"`{tournament_id[:8]}` (or use the name)", inline=False)
        )
    return build_embed(
        "Tournament created",
        f"**{name}** has been created.",
        color=EmbedColor.DEFAULT,
        fields=fields,
    )


def tournament_open(*, name: str, slots: str) -> dict[str, Any]:
    """Registration is open. ``slots`` is a display string like ``"3 / 16"``."""
    return build_embed(
        "Registration open",
        f"**{name}** is now open for registration.",
        color=EmbedColor.SUCCESS,
        fields=[_field("Slots", slots, inline=True)],
    )


def tournament_check_in(*, name: str, deadline: str) -> dict[str, Any]:
    """Check-in window opened (PLAN.md §2 ``check_in``). ``deadline`` is display."""
    return build_embed(
        "Check-in open",
        f"Check in for **{name}** before it locks.",
        color=EmbedColor.WARNING,
        fields=[
            _field("Deadline", deadline, inline=True),
            _field("How", "Use `/checkin` to confirm your team."),
        ],
    )


def tournament_started(*, name: str, bracket_url: str | None = None) -> dict[str, Any]:
    """Bracket generated and the tournament is running (PLAN.md §2 ``running``)."""
    fields = [_field("Bracket", bracket_url)] if bracket_url else None
    return build_embed(
        "Tournament started",
        f"**{name}** is underway. Good luck!",
        color=EmbedColor.SUCCESS,
        fields=fields,
    )


# --------------------------------------------------------------------------- #
# Match orchestration                                                          #
# --------------------------------------------------------------------------- #
def match_ready(
    *,
    match_label: str,
    host_name: str,
    is_host: bool,
    lobby_name: str | None = None,
    lobby_password: str | None = None,
    mode_text: str | None = None,
) -> dict[str, Any]:
    """Your lobby is forming — host/join details (PLAN.md §3.3 lobby fallback).

    The host receives a create-lobby instruction; guests receive the name +
    password manual-fallback so they can join even if the invite path fails
    (PLAN.md §3.3 "name+password fallback"). Secrets are only included when
    present — callers omit them for the auto-invite happy path.
    """
    if is_host:
        description = f"You are the **host** for **{match_label}**. Create the custom lobby."
    else:
        description = (
            f"**{match_label}** is forming. **{host_name}** is hosting — watch for an invite."
        )

    fields: list[EmbedField] = [_field("Host", host_name, inline=True)]
    if mode_text:
        fields.append(_field("Game", mode_text, inline=True))
    if lobby_name:
        fields.append(_field("Lobby name", lobby_name, inline=True))
    if lobby_password:
        fields.append(_field("Password", lobby_password, inline=True))

    return build_embed(
        "Your lobby is forming",
        description,
        color=EmbedColor.WARNING,
        fields=fields,
    )


def match_result(
    *,
    match_label: str,
    winner_name: str,
    loser_name: str,
    decided_by: str,
) -> dict[str, Any]:
    """A match result was decided (PLAN.md §2 MatchResult).

    ``decided_by`` is the display form of the adjudication source
    (``quorum`` / ``admin_override`` / ``walkover`` / ``forfeit``).
    """
    return build_embed(
        "Match result",
        f"**{winner_name}** defeated **{loser_name}** in **{match_label}**.",
        color=EmbedColor.SUCCESS,
        fields=[
            _field("Winner", winner_name, inline=True),
            _field("Decided by", decided_by, inline=True),
        ],
    )


# --------------------------------------------------------------------------- #
# Central feed (federated — privacy-gated, PLAN.md §3.2)                        #
# --------------------------------------------------------------------------- #
def build_central_feed_item(
    *,
    headline: str,
    source_guild_name: str,
    tournament_name: str,
    projected_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a central-feed embed from a field-whitelisted projection (PLAN.md §3.2).

    The central feed crosses the tenant boundary, so this builder accepts only an
    already-projected, consent-checked mapping (``CentralFeedItem.projected_fields``
    in the data model, §2). As defence-in-depth it raises ``ValueError`` if any
    projected key is a known identifier (PUUID / Discord-id / summoner-id /
    summoner-name) — those must be stripped by the backend before projection and
    must never reach a federated embed.

    No identifier is ever placed in the embed footer or description either; the
    only attribution shown is the *source guild name* and *tournament name*, both
    of which are deliberately part of the public projection.
    """
    fields: list[EmbedField] = [
        _field("Guild", source_guild_name, inline=True),
        _field("Tournament", tournament_name, inline=True),
    ]
    for key, value in (projected_fields or {}).items():
        if key.lower() in _FORBIDDEN_CENTRAL_KEYS:
            raise ValueError(
                f"central-feed projection may not include identifier field {key!r} "
                "(federation privacy, PLAN.md §3.2)"
            )
        fields.append(_field(key, value))

    return build_embed(
        headline,
        color=EmbedColor.DEFAULT,
        fields=fields,
        footer="Central feed",
    )


__all__ = [
    "lobby_credentials",
    "registration_code",
    "registration_verified",
    "team_created",
    "team_joined",
    "tournament_created",
    "tournament_open",
    "tournament_check_in",
    "tournament_started",
    "match_ready",
    "match_result",
    "build_central_feed_item",
]
