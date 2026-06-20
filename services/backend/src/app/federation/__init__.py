"""Central-feed federation (PLAN.md §3.2 is_shared_to_central, §2 CentralFeedItem).

Implements the consented, admin-approved, field-whitelisted ONE-WAY projection of a
guild's tournament into the shared central feed: :mod:`app.federation.service` is the
privacy boundary (whitelist projection + per-player visibility), and
:mod:`app.federation.router` is the service-authenticated HTTP shell the bot calls.
The router is mounted by ``app.main`` (no edit to the application factory).
"""

from __future__ import annotations
