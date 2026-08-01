import React, { useState } from 'react';
import { theme as t } from './theme';
import { Kicker, PrimaryButton } from './ui';
import { lol } from './bridge';

/** Step 1 — passwordless pairing. The Discord bot mints a one-time code
 *  (/register); the user pastes it here to bootstrap a desktop session. */
export default function PairScreen({ onPaired }: { onPaired: () => void }) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function pair() {
    const b = lol();
    if (!b || !code.trim()) return;
    setBusy(true); setErr(null);
    const r = await b.pair(code.trim(), 'Desktop');
    setBusy(false);
    if (!r.ok) { setErr(r.detail ?? `Pairing failed${r.status ? ` (${r.status})` : ''}`); return; }
    onPaired();
  }

  return (
    <div style={{ width: '100vw', height: '100vh', background: 'radial-gradient(120% 80% at 50% -10%,#0d1c30 0%,#010a13 60%)', color: t.text, fontFamily: t.body, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: 460, border: `1px solid ${t.line}`, background: t.panel, padding: '40px 44px', borderRadius: 3 }}>
        <div style={{ textAlign: 'center', marginBottom: 8 }}>
          <span style={{ fontFamily: t.display, fontWeight: 600, fontSize: 22, letterSpacing: '.14em', color: t.goldBright }}>LEAGUE<span style={{ color: t.gold }}>OF</span>LEAGUES</span>
        </div>
        <div style={{ textAlign: 'center', marginBottom: 26 }}><Kicker>Desktop Client · Pair to sign in</Kicker></div>

        <p style={{ fontSize: 14, lineHeight: 1.5, color: t.text, marginBottom: 22 }}>
          In Discord, run <span style={{ fontFamily: t.mono, color: t.gold }}>/register</span> to get a one-time code, then paste it below.
        </p>

        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && pair()}
          placeholder="PAIRING CODE"
          autoFocus
          style={{ width: '100%', boxSizing: 'border-box', background: t.bg, border: `1px solid ${t.line}`, color: t.textStrong, fontFamily: t.mono, fontSize: 18, letterSpacing: '.2em', textAlign: 'center', padding: '16px 18px', borderRadius: 2, marginBottom: 16 }}
        />
        {err && <div style={{ color: t.red, fontSize: 13, marginBottom: 16 }}>{err}</div>}
        <PrimaryButton onClick={pair} disabled={busy || !code.trim()} style={{ width: '100%', padding: 16, fontSize: 15 }}>{busy ? 'Pairing…' : 'Pair this device'}</PrimaryButton>
      </div>
    </div>
  );
}
