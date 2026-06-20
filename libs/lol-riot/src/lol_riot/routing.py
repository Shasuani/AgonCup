"""Riot API routing values.

Two distinct routing planes are used (PLAN.md §3.5):

* **Continental** clusters (`americas | asia | europe`) serve **Account-V1**
  (Riot ID -> PUUID). These are *not* platform routes.
* **Platform** routes (`na1`, `euw1`, ...) serve **Summoner-V4** — the only
  optional secondary call, used solely to read ``profileIconId`` for the
  ownership-challenge nonce (PLAN.md §3.1 verification). Summoner-V4 is hosted
  per-platform, so it must **not** be called on a continental host, and vice
  versa.
"""

from __future__ import annotations

from enum import StrEnum


class ContinentalRoute(StrEnum):
    """Riot continental routing value for Account-V1."""

    AMERICAS = "americas"
    ASIA = "asia"
    EUROPE = "europe"

    @property
    def base_url(self) -> str:
        """Return the HTTPS base URL for this continental cluster."""
        return f"https://{self.value}.api.riotgames.com"


class PlatformRoute(StrEnum):
    """Riot platform routing value for Summoner-V4 (profile-icon nonce only).

    Account-V1 is **continental**; Summoner-V4 ``by-puuid`` is **platform**.
    Routing the wrong host for either yields a 4xx, so the verification icon
    check (PLAN.md §3.1) must pass the player's platform here, not a continent.
    """

    NA1 = "na1"
    BR1 = "br1"
    LA1 = "la1"
    LA2 = "la2"
    EUW1 = "euw1"
    EUN1 = "eun1"
    TR1 = "tr1"
    RU = "ru"
    KR = "kr"
    JP1 = "jp1"
    OC1 = "oc1"

    @property
    def base_url(self) -> str:
        """Return the HTTPS base URL for this platform host."""
        return f"https://{self.value}.api.riotgames.com"


__all__ = ["ContinentalRoute", "PlatformRoute"]
