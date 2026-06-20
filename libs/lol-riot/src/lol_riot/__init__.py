"""lol-riot: thin Account-V1 client + light token-bucket limiter.

Account-V1 only (no Tournament-V5, no Match-V5). See PLAN.md §3.5 / §5.
"""

from __future__ import annotations

from lol_riot.account import AccountDTO, RiotAccountClient
from lol_riot.limiter import TokenBucketLimiter
from lol_riot.routing import ContinentalRoute

__all__ = [
    "AccountDTO",
    "ContinentalRoute",
    "RiotAccountClient",
    "TokenBucketLimiter",
]

__version__ = "0.0.0"
