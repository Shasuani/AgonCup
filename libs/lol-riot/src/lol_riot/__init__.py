"""lol-riot: thin Account-V1 client + light token-bucket limiter.

Account-V1 only (no Tournament-V5, no Match-V5), plus a minimal Summoner-V4
``by-puuid`` for the profile-icon verification nonce. See PLAN.md §3.5 / §5.
"""

from __future__ import annotations

from lol_riot.account import AccountDTO, RiotAccountClient, SummonerDTO
from lol_riot.exceptions import NotFound, RateLimited, RiotApiError
from lol_riot.limiter import TokenBucketLimiter
from lol_riot.routing import ContinentalRoute, PlatformRoute

__all__ = [
    "AccountDTO",
    "ContinentalRoute",
    "NotFound",
    "PlatformRoute",
    "RateLimited",
    "RiotApiError",
    "RiotAccountClient",
    "SummonerDTO",
    "TokenBucketLimiter",
]

__version__ = "0.0.0"
