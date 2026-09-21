// Login screen — also the public landing page.
//
// Players and admins both land here from Discord. It used to be a bare sign-in
// card on an empty stage, which told a first-time visitor nothing about what
// the product is. Now the left column carries the pitch (what it does, how it
// works in three steps) and the right column keeps the sign-in card — the same
// hooks as before (data-login, data-login-card, the Discord CTA, the downloads,
// the dev-only login), so nothing that tests or the auth flow rely on moved.
//
// The Discord OAuth CTA starts the backend flow; a DEV-only button (stripped
// from production builds) mints a local admin session via /auth/dev-login.

import { useState } from "react";
import { Navigate } from "react-router-dom";
import { sessionFromTokenPair, useAuth } from "../../auth/AuthProvider";
import { Button, DownloadClient } from "../../components";
import { Icon, type IconName } from "../../components/Icon";
import { config } from "../../config";
import { BrandLogo } from "../../layout/Logo";

const FEATURES: ReadonlyArray<{ icon: IconName; title: string; copy: string }> = [
  {
    icon: "server",
    title: "Lives in your Discord",
    copy: "Add the bot to your server. Players sign in with one command; announcements, check-ins and results post where your community already is.",
  },
  {
    icon: "tournaments",
    title: "Brackets that run themselves",
    copy: "Up to 16 teams, single-elimination, 5v5 on the Rift or 1v1 ARAM duels. Entrants lock in, you press Start, the bracket draws itself.",
  },
  {
    icon: "download",
    title: "Lobbies made for you",
    copy: "The desktop client opens the custom game, sends every invite and reports the scoreboard. Results are cross-checked across all ten clients.",
  },
];

const STEPS: ReadonlyArray<{ title: string; copy: string }> = [
  { title: "Add the bot", copy: "Sign in with Discord and pick a server you administer." },
  { title: "Players link their account", copy: "They run /register in Discord and paste the code into the desktop client." },
  { title: "Create a tournament", copy: "Share the join code. Captains enter their teams, everyone locks in, you start it." },
];

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
      <div data-landing>
        {/* The pitch. Decorative for the auth flow, load-bearing for a first visit. */}
        <section data-landing-hero aria-label="About AgonCup">
          <div data-landing-brand>
            <BrandLogo data-landing-logo />
            <p data-login-tagline>Tournament Operations Console</p>
          </div>
          <h1 id="login-heading" data-landing-title>
            Run League of Legends tournaments <em>from Discord.</em>
          </h1>
          <p data-landing-lead>
            Brackets, check-ins, custom lobbies and results — organised in your server,
            played through a small desktop client, decided without spreadsheets.
          </p>

          <ul data-landing-features>
            {FEATURES.map((f) => (
              <li key={f.title}>
                <span data-landing-feature-icon aria-hidden="true">
                  <Icon name={f.icon} size={20} />
                </span>
                <div>
                  <h2>{f.title}</h2>
                  <p>{f.copy}</p>
                </div>
              </li>
            ))}
          </ul>

          <div data-landing-steps>
            <h2 data-landing-steps-title>How it works</h2>
            <ol>
              {STEPS.map((s, i) => (
                <li key={s.title}>
                  <span data-landing-step-num aria-hidden="true">{i + 1}</span>
                  <div>
                    <strong>{s.title}</strong>
                    <span>{s.copy}</span>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <div data-login-card>
          <p data-login-card-kicker>Organisers</p>
          <h2 data-login-card-title>Sign in to your console</h2>
          <p data-login-copy>
            Manage tournaments, teams and disputes for the Discord servers you administer.
          </p>
          <Button variant="primary" onClick={login} data-login-cta>
            <Icon name="discord" size={18} />
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
      </div>
    </main>
  );
}
