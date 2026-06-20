// Login screen. The Discord OAuth CTA starts the backend flow; a DEV-only button
// (stripped from production builds) mints a local admin session via the backend
// /auth/dev-login endpoint so the app is usable without a configured OAuth app.

import { useState } from "react";
import { Navigate } from "react-router-dom";
import { sessionFromTokenPair, useAuth } from "../../auth/AuthProvider";
import { Button } from "../../components";
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
    try {
      const res = await fetch(`${config.apiBaseUrl}/api/v1/auth/dev-login`, {
        method: "POST",
      });
      if (!res.ok) {
        throw new Error(`dev-login failed (${res.status}) — is the backend running?`);
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
      <h1 id="login-heading">LeagueOfLeagues — Admin</h1>
      <p>Sign in with Discord to manage tournaments, teams, and disputes.</p>
      <Button variant="primary" onClick={login}>
        Sign in with Discord
      </Button>

      {import.meta.env.DEV && (
        <div data-dev-login style={{ marginTop: "1.5rem", opacity: 0.85 }}>
          <hr />
          <p>
            <strong>Local development</strong> — no Discord app configured. Mint a
            platform-operator session:
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
    </main>
  );
}
