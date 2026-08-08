// "Which Discord server?" — the bot-install picker (PLAN.md §3.2 multi-tenant bot).
//
// This is the entry point for a brand-new user. Signing in with Discord makes you
// a User with NO roles anywhere; roles are minted by `onboard_guild` when the bot
// JOINS a server, and only for the person who invited it. So without this screen
// a fresh account lands in an admin console it has no access to and no way in —
// a guild could only be onboarded by hand-crafting a Discord invite URL.
//
// NO ROLE GATE, on purpose: gating this on guild_admin would be circular, since
// installing the bot is how you become one.
//
// Admin-ness is Discord's own ADMINISTRATOR bit, read server-side from the user's
// OAuth token — the client asserts nothing. `invite_url` likewise comes from the
// backend (preselected to that server), so we never assemble one here.
//
// AFTER AN INSTALL the access JWT is stale: it was minted before the guild_admin
// row existed and still says roles: []. Rotating the refresh token re-runs the
// role lookup, so "I've added it" rotates and re-reads rather than telling the
// user to sign out and back in.

import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { isApiError, type AdminGuild, type TokenPair } from "@lol/clients-ts";
import { useApi } from "../../api/ApiProvider";
import { useAsyncData } from "../../api/useAsync";
import { useAuth, sessionFromTokenPair } from "../../auth/AuthProvider";
import { useActingGuild } from "../../auth/ActingGuildProvider";
import { Button, ErrorBanner, Spinner, EmptyState } from "../../components";
import { useToast } from "../../components/Toast";

import "./ServersScreen.css";

/** The backend's 409 detail meaning "your Discord token can't be used". */
export const REAUTH_DETAIL = "discord_reauth_required";

/** True when the error is the backend asking for a fresh Discord sign-in. */
export function isReauthError(err: unknown): boolean {
  return isApiError(err) && err.status === 409 && err.detail === REAUTH_DETAIL;
}

export default function ServersScreen(): JSX.Element {
  const api = useApi();
  const { login } = useAuth();
  const guilds = useAsyncData<AdminGuild[]>(() => api.auth.adminGuilds(), [api]);

  if (guilds.loading && guilds.data === null) {
    return (
      <section aria-labelledby="servers-heading">
        <h1 id="servers-heading">Your Discord servers</h1>
        <Spinner label="Loading your servers" />
      </section>
    );
  }

  // Every account that signed in before the `guilds` scope existed lands here,
  // as does anyone whose Discord token expired. One button fixes both.
  if (guilds.error && isReauthError(guilds.error)) {
    return (
      <section aria-labelledby="servers-heading">
        <h1 id="servers-heading">Your Discord servers</h1>
        <p role="alert" data-servers-reauth>
          We need permission to see which servers you are in. Sign in with Discord
          again to grant it.
        </p>
        <Button variant="primary" onClick={login}>
          Sign in with Discord again
        </Button>
      </section>
    );
  }

  const rows = guilds.data ?? [];

  return (
    <section aria-labelledby="servers-heading">
      <div data-screen-header>
        <div>
          <h1 id="servers-heading">Your Discord servers</h1>
          <p data-screen-intro>
            Servers where you are an administrator. Add the bot to one to start
            running tournaments there.
          </p>
        </div>
        <Button variant="secondary" onClick={guilds.reload} disabled={guilds.loading}>
          {guilds.loading ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      <ErrorBanner error={guilds.error} />

      {rows.length === 0 && !guilds.error ? (
        <EmptyState
          title="No servers to show"
          description="You are not an administrator of any Discord server. Ask the owner of a server to add the bot, or create your own server in Discord first."
        />
      ) : (
        <ul data-server-list>
          {rows.map((guild) => (
            <ServerCard key={guild.discord_guild_id} guild={guild} onChanged={guilds.reload} />
          ))}
        </ul>
      )}
    </section>
  );
}

interface ServerCardProps {
  guild: AdminGuild;
  onChanged: () => void;
}

function ServerCard({ guild, onChanged }: ServerCardProps): JSX.Element {
  const api = useApi();
  const navigate = useNavigate();
  const { toast } = useToast();
  const { setSession } = useAuth();
  const { setActingGuild } = useActingGuild();
  const [busy, setBusy] = useState(false);

  /** Open Discord's install page in a new tab (this tab keeps the session). */
  const install = useCallback(() => {
    window.open(guild.invite_url, "_blank", "noopener,noreferrer");
  }, [guild.invite_url]);

  /**
   * Re-check after the user says they finished the Discord install: rotate the
   * token so the freshly-granted guild_admin role is actually in our JWT, then
   * reload the list.
   */
  const confirmInstalled = useCallback(async () => {
    setBusy(true);
    try {
      // No token to pass: the browser attaches the HttpOnly session cookie and
      // the backend rotates from that.
      const pair: TokenPair = await api.auth.refresh({});
      setSession(sessionFromTokenPair(pair));
      onChanged();
    } catch {
      // A failed rotation is not fatal here — reloading the list still shows
      // whether the bot landed; the role just appears on the next sign-in.
      onChanged();
    } finally {
      setBusy(false);
    }
  }, [api, setSession, onChanged]);

  /** Switch the app's tenant to this server and go run something. */
  const manage = useCallback(() => {
    if (!guild.guild_id) return;
    setActingGuild(guild.guild_id);
    toast({ title: `Now managing ${guild.name}`, variant: "success" });
    navigate("/tournaments");
  }, [guild.guild_id, guild.name, setActingGuild, toast, navigate]);

  return (
    <li data-server-card data-installed={guild.installed ? "true" : "false"}>
      <div data-server-identity>
        {guild.icon_url ? (
          <img src={guild.icon_url} alt="" width={40} height={40} data-server-icon />
        ) : (
          <span data-server-icon data-server-icon-fallback aria-hidden="true">
            {guild.name.slice(0, 1).toUpperCase()}
          </span>
        )}
        <div>
          <h2 data-server-name>{guild.name}</h2>
          <p data-server-role>{guild.owner ? "Owner" : "Administrator"}</p>
        </div>
      </div>

      {guild.installed ? (
        <div data-server-actions>
          <span data-server-status data-ok>
            Bot installed
          </span>
          <Button variant="primary" onClick={manage}>
            Manage
          </Button>
        </div>
      ) : (
        <div data-server-actions>
          <span data-server-status>Bot not added</span>
          <Button variant="primary" onClick={install}>
            Add bot
          </Button>
          <Button variant="secondary" onClick={confirmInstalled} disabled={busy}>
            {busy ? "Checking…" : "I've added it"}
          </Button>
        </div>
      )}
    </li>
  );
}
