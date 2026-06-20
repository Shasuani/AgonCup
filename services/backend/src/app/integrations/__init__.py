"""External-integration bridges (PLAN.md §3.2).

Currently the Discord bot bridge (:mod:`app.integrations.discord`): the
service-authenticated backend surface the multi-tenant bot calls. Each integration
owns its own service-auth boundary and delegates to the domain services rather than
duplicating their logic.
"""

from __future__ import annotations
