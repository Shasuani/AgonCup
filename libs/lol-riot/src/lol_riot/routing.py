"""Continental routing for Riot Account-V1.

Account-V1 is served from continental clusters, **not** platform routes
(`na1`/`euw1`). See PLAN.md §3.5.
"""

from __future__ import annotations

from enum import Enum


class ContinentalRoute(str, Enum):
    """Riot continental routing value for Account-V1."""

    AMERICAS = "americas"
    ASIA = "asia"
    EUROPE = "europe"

    @property
    def base_url(self) -> str:
        """Return the HTTPS base URL for this continental cluster."""
        return f"https://{self.value}.api.riotgames.com"


__all__ = ["ContinentalRoute"]
