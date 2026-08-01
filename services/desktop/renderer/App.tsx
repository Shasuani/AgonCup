import React, { useEffect, useState } from 'react';
import { theme as t } from './theme';
import { lol, type BridgeUser } from './bridge';
import { Spinner } from './ui';
import PairScreen from './PairScreen';
import DesktopClient from './DesktopClient';

type AuthState = 'loading' | 'signed-out' | 'signed-in';

export default function App() {
  const [state, setState] = useState<AuthState>('loading');
  const [user, setUser] = useState<BridgeUser | null>(null);

  async function loadSession() {
    const b = lol();
    if (!b) { setState('signed-out'); return; } // browser preview → show pairing
    try {
      const s = await b.getSession();
      setUser(s.user);
      setState(s.signedIn ? 'signed-in' : 'signed-out');
    } catch {
      setState('signed-out');
    }
  }

  useEffect(() => {
    void loadSession();
    const b = lol();
    if (!b) return;
    const off = b.onAuthLost(() => { setUser(null); setState('signed-out'); });
    return off;
  }, []);

  if (state === 'loading') {
    return (
      <div style={{ width: '100vw', height: '100vh', background: t.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spinner size={28} />
      </div>
    );
  }
  if (state === 'signed-out') return <PairScreen onPaired={() => void loadSession()} />;
  return <DesktopClient user={user} onSignOut={() => setState('signed-out')} />;
}
