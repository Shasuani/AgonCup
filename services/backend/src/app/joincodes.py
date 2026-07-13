"""Human-typeable, ambiguity-free join codes for tournaments and teams.

A join code is a **shareable capability** (PLAN.md §3.1): whoever holds it can
join the associated tournament/team from the desktop client. Admins mint a
tournament code on the bot and hand it out; captains mint a team code in the
client. Codes reuse the same ambiguity-free alphabet as the desktop pairing code
(no ``0/O/1/I/L``) so they survive being read aloud or copied by hand.

Codes are guild-scoped *in effect*, not just by string: the redeem path runs on
the tenant session, so a code only resolves when the acting guild owns the row
(RLS). A leaked code therefore cannot cross tenants — cross-server participation
stays structurally impossible.
"""

from __future__ import annotations

import secrets

#: Ambiguity-free alphabet (shared in spirit with the desktop pairing code).
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
JOIN_CODE_LEN = 8


def generate_join_code() -> str:
    """Return a fresh 8-char join code over the ambiguity-free alphabet."""
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LEN))


def normalize_join_code(code: str) -> str:
    """Canonicalize a user-entered code: strip, upper-case, drop separators.

    So ``t-abcd 2345`` and ``ABCD2345`` resolve identically. Only characters in
    :data:`JOIN_CODE_ALPHABET` survive (hyphens/spaces/ambiguous glyphs dropped).
    """
    return "".join(ch for ch in code.strip().upper() if ch in JOIN_CODE_ALPHABET)
