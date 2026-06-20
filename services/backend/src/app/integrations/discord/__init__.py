"""Discord bot ↔ backend bridge (PLAN.md §3.2 multi-tenant bot).

The Discord bot is a *thin layer*: it holds the only copy of the service token and
asserts the Discord context (user/guild snowflakes, Manage-Guild bool) on every
call. The backend (:mod:`app.integrations.discord.router`) trusts that context
because only the bot can present the token, resolves it to our identity/tenant,
sets the RLS GUC, gates guild-admin actions, and DELEGATES to the existing
tournaments/teams/accounts services. See :mod:`app.integrations.discord.service_auth`
for the trust boundary.
"""

from __future__ import annotations

from app.integrations.discord.router import router

__all__ = ["router"]
