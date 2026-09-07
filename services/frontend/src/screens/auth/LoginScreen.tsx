// Login screen. The Discord OAuth CTA starts the backend flow; a DEV-only button
// (stripped from production builds) mints a local admin session via the backend
// /auth/dev-login endpoint so the app is usable without a configured OAuth app.

import { useState } from "react";
import { Navigate } from "react-router-dom";
import { sessionFromTokenPair, useAuth } from "../../auth/AuthProvider";
import { Button, DownloadClient } from "../../components";
import { config } from "../../config";

export default function LoginScreen(): JSX.Element {
  const { isAuthenticated, login, setSession } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  async function devLogin(): Promise<void> {
    setError(null);
    setBusy(true);
    const backendDownHint =
      "Backend not running on :8000 — start it with: make dev-backend (or the VS Code 'Stack: Backend + Frontend' launch).";
    try {
      let res: Response;
      try {
        res = await fetch(`${config.apiBaseUrl}/api/v1/auth/dev-login`, {
          method: "POST",
        });
      } catch {
        // fetch() throws (TypeError "Failed to fetch") when even the Vite dev
        // server/proxy can't be reached — distinct from a proxy-relayed status.
        throw new Error("Cannot reach the Vite dev server / proxy on :5173. Is `npm run dev` running?");
      }
      if (!res.ok) {
        // 502/503/504 or the proxy's backend_unreachable body means the backend
        // process is down (the proxy is up but its upstream isn't).
        const body = await res.text();
        if ([502, 503, 504].includes(res.status) || body.includes("backend_unreachable")) {
          throw new Error(backendDownHint);
        }
        throw new Error(`dev-login failed (${res.status}): ${body || "unknown error"}`);
      }
      setSession(sessionFromTokenPair(await res.json()));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main aria-labelledby="login-heading" data-login>
      <div data-login-card>
        <h1 id="login-heading">LeagueOfLeagues</h1>
        <p data-login-tagline>Tournament Operations Console</p>
        <p data-login-copy>
          Sign in with Discord to manage tournaments, teams, and disputes.
        </p>
        <Button variant="primary" onClick={login} data-login-cta>
          Sign in with Discord
        </Button>

        {/* Players land here from Discord looking for the client, not for the
            admin console — the installers sit below the sign-in, no account needed. */}
        <DownloadClient />

        {/* Stripped from production builds — import.meta.env.DEV is a compile
            time constant, so the whole block is dead code in a release bundle. */}
        {import.meta.env.DEV && (
          <div data-dev-login>
            <p data-dev-login-note>
              <strong>Local development</strong> — no Discord app configured.
            </p>
            <Button onClick={devLogin} disabled={busy}>
              {busy ? "Signing in…" : "Dev login (local only)"}
            </Button>
            {error && (
              <p role="alert" data-dev-login-error>
                {error}
              </p>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
