import React, { useEffect, useState } from 'react';
import { theme as t, chromeStyle, dragRegion, TITLE_BAR_HEIGHT } from './theme';
import { lol, type BridgeUser } from './bridge';
import { Spinner } from './ui';
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
      <div style={{ width: '100vw', height: '100vh', background: t.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spinner size={28} />
      </div>
    );
  } else if (state === 'offline') screen = <OfflineScreen name={user?.displayName ?? null} />;
  else if (state === 'signed-out') screen = <PairScreen onPaired={() => void loadSession()} />;
  else screen = <DesktopClient user={user} onSignOut={() => setState('signed-out')} />;

  return (
    <>
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
    <div style={{ width: '100vw', height: '100vh', background: t.bg, color: t.text, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ textAlign: 'center', maxWidth: 420, padding: 24 }}>
        <Spinner size={28} />
        <h2 style={{ margin: '20px 0 8px', fontSize: 18 }}>Can’t reach the server</h2>
        <p style={{ margin: 0, color: t.textDim, fontSize: 14, lineHeight: 1.6 }}>
          {name ? `You’re still signed in as ${name}. ` : 'You’re still signed in. '}
          Reconnecting automatically — no need to enter a new code.
        </p>
      </div>
    </div>
  );
}
