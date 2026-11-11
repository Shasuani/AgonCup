// Login screen — also the public landing page.
//
// Everyone who is not signed in lands here: organisers coming to run a
// tournament, players coming from Discord for the client, and people who were
// just sent the link and want to know what AgonCup is. So the page is a small
// product site — trailer, what it does (with real screenshots of the console
// and the desktop client), how it works — and it ends at the same sign-in card
// and downloads as before. The hooks the auth flow and tests rely on stay put:
// #login-heading labels <main>, the one "Sign in with Discord" button (data-login-cta)
// lives in data-login-card, the dev-only login sits under it.
//
// The page is always dark (it is built around the trailer and the client's
// hextech look); its styles live in landing.css, scoped under [data-site].

import { useEffect, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import { sessionFromTokenPair, useAuth } from "../../auth/AuthProvider";
import { Button, DownloadClient } from "../../components";
import { Icon, type IconName } from "../../components/Icon";
import { config } from "../../config";
import { BrandLogo } from "../../layout/Logo";
import "./landing.css";

const FACTS: ReadonlyArray<string> = [
  "5v5 Summoner's Rift",
  "1v1 ARAM duels",
  "Up to 16 teams",
  "Windows & Mac client",
];

interface Feature {
  id: string;
  icon: IconName;
  kicker: string;
  title: string;
  copy: string;
  points: ReadonlyArray<string>;
  image: string;
  alt: string;
}

const FEATURES: ReadonlyArray<Feature> = [
  {
    id: "discord",
    icon: "server",
    kicker: "Admin console",
    title: "Lives in your Discord",
    copy: "Add the bot to your server and run everything from where your community already is. The console shows what needs you right now.",
    points: [
      "Players sign in with one command",
      "Announcements, check-ins and results post to your server",
      "Running tournaments, linked players and open disputes at a glance",
    ],
    image: "/media/site_dash.webp",
    alt: "The AgonCup console dashboard: tournament, team and player counts, a result that needs a decision, and live tournaments with check-in progress.",
  },
  {
    id: "lobbies",
    icon: "download",
    kicker: "Desktop client",
    title: "Lobbies build themselves",
    copy: "When a match is due, the desktop client opens the custom game, sends every invite and follows the game to the end.",
    points: [
      "No lobby names or passwords to pass around",
      "Live scoreboard straight from the League client",
      "Reports the result when the Nexus falls",
    ],
    image: "/media/desk_live.webp",
    alt: "The desktop client's Live Match screen with automation on, the game in progress and a live scoreboard.",
  },
  {
    id: "brackets",
    icon: "tournaments",
    kicker: "Brackets",
    title: "Brackets that run themselves",
    copy: "Single elimination for up to 16 teams. Captains enter, everyone locks in, you press Start and the bracket draws itself — then fills in as games finish.",
    points: [
      "Join codes for captains",
      "Lock-in status for every player",
      "Winners advance automatically",
    ],
    image: "/media/desk_tourn2.webp",
    alt: "A tournament in the desktop client: every team locked in and a bracket with winners advancing to the final.",
  },
  {
    id: "integrity",
    icon: "disputes",
    kicker: "Integrity",
    title: "Results you can trust",
    copy: "Every result is cross-checked across all ten clients. When reports disagree, the match goes to a dispute queue with the anomaly flags, and an admin records the final word.",
    points: [
      "Automatic corroboration of reported outcomes",
      "Anomalies flagged, never silently accepted",
      "Every override written to the audit log",
    ],
    image: "/media/site_disputes.webp",
    alt: "The dispute queue: a match whose reported results contradict each other, with anomaly flags and buttons to record the winner.",
  },
  {
    id: "history",
    icon: "history",
    kicker: "Match history",
    title: "Every stat. Every game.",
    copy: "Per-game scoreboards for every match played through AgonCup — champions, KDA, items and CS for both teams.",
    points: [
      "Full scoreboards for both sides",
      "Your recent games in the client",
      "Victory and defeat at a glance",
    ],
    image: "/media/desk_history.webp",
    alt: "Match history in the desktop client: full scoreboards with champions, items and KDA for both teams.",
  },
];

const STEPS: ReadonlyArray<{ title: string; copy: string }> = [
  { title: "Add the bot", copy: "Sign in with Discord and pick a server you administer." },
  { title: "Players link their account", copy: "They run /register in Discord and paste the code into the desktop client." },
  { title: "Create a tournament", copy: "Share the join code. Captains enter their teams, everyone locks in, you start it." },
];

/** Drifting gold embers behind the page — positions are deterministic. */
const EMBERS = Array.from({ length: 28 }, (_, i) => {
  const r = (k: number): number => {
    const x = Math.sin((i + 1) * 127.1 + k * 311.7) * 43758.5453;
    return x - Math.floor(x);
  };
  return {
    left: `${(r(1) * 100).toFixed(1)}%`,
    size: 2 + Math.round(r(2) * 3),
    duration: `${(14 + r(3) * 18).toFixed(1)}s`,
    delay: `${(-r(4) * 30).toFixed(1)}s`,
    drift: `${Math.round((r(5) - 0.5) * 120)}px`,
  };
});

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

/** The trailer: plays muted on loop; one button turns the sound on (from the top). */
function Trailer(): JSX.Element {
  const ref = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(true);
  const [autoPlay] = useState(() => !prefersReducedMotion());

  function toggleSound(): void {
    const v = ref.current;
    if (!v) return;
    if (muted) {
      v.muted = false;
      v.currentTime = 0;
      void v.play?.()?.catch(() => {});
      setMuted(false);
    } else {
      v.muted = true;
      setMuted(true);
    }
  }

  return (
    <figure data-site-trailer>
      <div data-site-trailer-frame>
        <video
          ref={ref}
          src="/media/agoncup-trailer.mp4"
          poster="/media/agoncup-trailer-poster.webp"
          autoPlay={autoPlay}
          muted
          loop
          playsInline
          preload="metadata"
          controls={!autoPlay}
          aria-label="AgonCup trailer"
        />
        {autoPlay && (
          <button type="button" data-site-sound onClick={toggleSound} aria-pressed={!muted}>
            <span aria-hidden="true">{muted ? "🔇" : "🔊"}</span>
            {muted ? "Play with sound" : "Mute"}
          </button>
        )}
      </div>
      <figcaption>
        Music: “Hitman” by Kevin MacLeod (incompetech.com), licensed under{" "}
        <a href="https://creativecommons.org/licenses/by/4.0/" rel="noreferrer" target="_blank">
          CC BY 4.0
        </a>
        . Product footage uses demo data.
      </figcaption>
    </figure>
  );
}

export default function LoginScreen(): JSX.Element {
  const { isAuthenticated, login, setSession } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = (): void => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

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
    <div data-site>
      {/* Decorative backdrop: hex texture + embers (fixed, behind everything). */}
      <div data-site-backdrop aria-hidden="true">
        {EMBERS.map((e, i) => (
          <i
            key={i}
            style={{
              left: e.left,
              width: e.size,
              height: e.size,
              animationDuration: e.duration,
              animationDelay: e.delay,
              ["--drift" as string]: e.drift,
            }}
          />
        ))}
      </div>
      <header data-site-nav data-scrolled={scrolled || undefined}>
        <div data-site-wrap>
          <a href="#top" data-site-nav-brand aria-label="AgonCup — back to top">
            <BrandLogo />
          </a>
          <nav aria-label="Page sections">
            <a href="#features">Features</a>
            <a href="#how">How it works</a>
            <a href="#get-started">Download</a>
          </nav>
          <button type="button" data-site-btn="ghost" onClick={login}>
            <Icon name="discord" size={16} />
            Sign in
          </button>
        </div>
      </header>

      <main aria-labelledby="login-heading" data-login id="top">
        {/* ---------------- hero ---------------- */}
        <section data-site-hero>
          {/* Noxus vs Demacia — the trailer's clash, framing the headline. */}
          <div data-site-hero-art aria-hidden="true">
            <span data-side="left" />
            <span data-side="right" />
          </div>
          <div data-site-wrap>
            <p data-site-kicker>League of Legends tournaments · on autopilot</p>
            <h1 id="login-heading" data-site-title>
              Run League of Legends tournaments <em>from Discord.</em>
            </h1>
            <p data-site-lead>
              Brackets, check-ins, custom lobbies and results — organised in your server,
              played through a small desktop client, decided without spreadsheets.
            </p>
            <div data-site-ctas>
              <button type="button" data-site-btn="primary" onClick={login}>
                <Icon name="discord" size={18} />
                Get started with Discord
              </button>
              <a href="#get-started" data-site-btn="secondary">
                <Icon name="download" size={18} />
                Download the client
              </a>
            </div>
            <ul data-site-facts aria-label="At a glance">
              {FACTS.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
            <Trailer />
          </div>
        </section>

        {/* ---------------- features ---------------- */}
        <section data-site-section id="features" aria-labelledby="features-heading">
          <div data-site-wrap>
            <header data-site-section-head>
              <p data-site-kicker>What it does</p>
              <h2 id="features-heading">Everything between “who's playing?” and “who won?”</h2>
              <p>
                One bot in your server, one console for the organisers, one desktop client for the
                players. These are the real screens.
              </p>
            </header>
            <div data-site-features>
              {FEATURES.map((f, i) => (
                <article key={f.id} data-site-feature data-flip={i % 2 === 1 || undefined} aria-labelledby={`feat-${f.id}`}>
                  <div data-site-feature-copy>
                    <p data-site-feature-kicker>
                      <span data-site-feature-icon aria-hidden="true">
                        <Icon name={f.icon} size={18} />
                      </span>
                      {String(i + 1).padStart(2, "0")} · {f.kicker}
                    </p>
                    <h3 id={`feat-${f.id}`}>{f.title}</h3>
                    <p>{f.copy}</p>
                    <ul>
                      {f.points.map((pt) => (
                        <li key={pt}>
                          <Icon name="check" size={16} />
                          {pt}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div data-site-shot>
                    <img src={f.image} alt={f.alt} loading="lazy" decoding="async" width={1600} height={965} />
                  </div>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* ---------------- how it works ---------------- */}
        <section data-site-section id="how" aria-labelledby="how-heading">
          <div data-site-wrap>
            <header data-site-section-head>
              <p data-site-kicker>How it works</p>
              <h2 id="how-heading">From zero to a running bracket in three steps</h2>
            </header>
            <ol data-site-steps>
              {STEPS.map((s, i) => (
                <li key={s.title}>
                  <span data-site-step-num aria-hidden="true"><span>{i + 1}</span></span>
                  <h3>{s.title}</h3>
                  <p>{s.copy}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* ---------------- get started: sign in + downloads ---------------- */}
        <section data-site-section id="get-started" aria-labelledby="start-heading" data-site-art-end>
          <div data-site-wrap>
            <header data-site-section-head>
              <p data-site-kicker>Get started</p>
              <h2 id="start-heading">Organiser or player — you're one click away</h2>
            </header>
            <div data-site-start>
              <div data-login-card>
                <p data-login-card-kicker>Organisers</p>
                <h3 data-login-card-title>Sign in to your console</h3>
                <p data-login-copy>
                  Manage tournaments, teams and disputes for the Discord servers you administer.
                  Signing in is also how you add the bot to a server.
                </p>
                <Button variant="primary" onClick={login} data-login-cta>
                  <Icon name="discord" size={18} />
                  Sign in with Discord
                </Button>

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

              <div data-site-players>
                <p data-login-card-kicker>Players</p>
                {/* No account needed to download; the client links your Riot
                    account through the /register code from Discord. */}
                <DownloadClient />
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer data-site-footer>
        <div data-site-wrap>
          <div data-site-footer-top>
            <BrandLogo data-site-footer-logo />
            <nav aria-label="Footer">
              <a href="#features">Features</a>
              <a href="#how">How it works</a>
              <a href="#get-started">Get started</a>
            </nav>
          </div>
          <p data-site-legal>
            AgonCup isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot
            Games or anyone officially involved in producing or managing Riot Games properties. Riot
            Games, and all associated properties are trademarks or registered trademarks of Riot
            Games, Inc.
          </p>
          <p data-site-legal>
            Trailer music: “Hitman” by Kevin MacLeod (incompetech.com), CC BY 4.0. © {new Date().getFullYear()} AgonCup.
          </p>
        </div>
      </footer>
    </div>
  );
}
