import React, { useEffect, useState } from 'react';
import { theme as t, chromeStyle, dragRegion, TITLE_BAR_HEIGHT } from './theme';
import { globalCss } from './styles';
import { lol, type BridgeUser } from './bridge';
import { Spinner } from './ui';
import { BrandMark } from './icons';
import PairScreen from './PairScreen';
import DesktopClient from './DesktopClient';

// 'offline' is NOT a flavour of signed-out. It means we still hold a pairing
// credential and only the backend is out of reach, so the app retries in the
// background instead of asking for a code the user would have to fetch from the
// Discord bot. Collapsing it into 'signed-out' is exactly the bug this state
// exists to prevent.
type AuthState = 'loading' | 'signed-out' | 'offline' | 'signed-in';

export default function App() {
  const [state, setState] = useState<AuthState>('loading');
  const [user, setUser] = useState<BridgeUser | null>(null);

  async function loadSession() {
    const b = lol();
    if (!b) { setState('signed-out'); return; } // browser preview → show pairing
    try {
      const s = await b.getSession();
      setUser(s.user);
      if (s.signedIn) { setState('signed-in'); return; }
      setState(s.status === 'unavailable' ? 'offline' : 'signed-out');
    } catch {
      setState('signed-out');
    }
  }

  useEffect(() => {
    void loadSession();
    const b = lol();
    if (!b) return;
    const offAuthLost = b.onAuthLost(() => { setUser(null); setState('signed-out'); });
    // The main process retries the silent refresh on its own; this is how the
    // "reconnecting" view learns it succeeded without polling.
    const offRestored = b.onSessionRestored(() => { void loadSession(); });
    return () => { offAuthLost(); offRestored(); };
  }, []);

  let screen: React.ReactNode;
  if (state === 'loading') {
    screen = (
      <div className="ll-stage">
        <div style={{ textAlign: 'center' }}>
          <BrandMark size={40} />
          <div style={{ marginTop: 18 }}><Spinner size={22} /></div>
        </div>
      </div>
    );
  } else if (state === 'offline') screen = <OfflineScreen name={user?.displayName ?? null} />;
  else if (state === 'signed-out') screen = <PairScreen onPaired={() => void loadSession()} />;
  else screen = <DesktopClient user={user} onSignOut={() => setState('signed-out')} />;

  return (
    <>
      {/* The one global stylesheet (fonts, reset, .ll-* classes). Mounted once
          here so every screen — pair, offline, the shell — shares it. */}
      <style>{globalCss}</style>
      <DragStrip />
      {screen}
    </>
  );
}

/** The native title bar is hidden on macOS (`hiddenInset`), so the top band of
 * EVERY screen must be a window drag handle — otherwise the boot / offline /
 * pair screens leave the window stuck in place. Chromium builds drag regions
 * from layout rects, so this transparent fixed strip works even when a screen
 * paints over it; interactive controls in that band opt out with `noDrag`. */
function DragStrip() {
  return <div aria-hidden style={{ ...chromeStyle, ...dragRegion, position: 'fixed', top: 0, left: 0, right: 0, height: TITLE_BAR_HEIGHT, zIndex: 0 }} />;
}

/** Shown while we hold a session the backend cannot confirm yet. Deliberately
 * offers no code box: the credential is fine, and prompting for one would send
 * the user to Discord to solve a problem they do not have. */
function OfflineScreen({ name }: { name: string | null }) {
  return (
    <div className="ll-stage">
      <div className="ll-stage__card" style={{ textAlign: 'center', maxWidth: 440 }}>
        <div className="ll-center" style={{ gap: 12, marginBottom: 22 }}>
          <BrandMark size={28} />
          <span className="ll-wordmark"><span>AGON<em>CUP</em></span></span>
        </div>
        <div className="ll-panel" style={{ padding: '34px 36px' }}>
          <Spinner size={26} />
          <h2 className="ll-h2" style={{ marginTop: 18 }}>Can’t reach the server</h2>
          <p className="ll-lead" style={{ marginTop: 10, color: t.text }}>
            {name ? `You’re still signed in as ${name}. ` : 'You’re still signed in. '}
            Reconnecting automatically — no need to enter a new code.
          </p>
        </div>
      </div>
    </div>
  );
}
