import React, { useState } from 'react';
import { noDrag } from './theme';
import { Input, Notice, PrimaryButton } from './ui';
import { BrandMark, Icon } from './icons';
import { lol, useLcuStatus } from './bridge';

/** Step 1 — passwordless pairing. The Discord bot mints a one-time code
 *  (/register); the user pastes it here to bootstrap a desktop session. */
export default function PairScreen({ onPaired }: { onPaired: () => void }) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const lcu = useLcuStatus();

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
    <div className="ll-stage">
      <div className="ll-stage__card" style={noDrag}>
        <div className="ll-center" style={{ gap: 12, marginBottom: 26 }}>
          <BrandMark size={30} />
          <span className="ll-wordmark" style={{ fontSize: 17 }}>LEAGUE<em>OF</em>LEAGUES</span>
        </div>

        <div className="ll-panel ll-panel--featured" style={{ padding: '34px 36px 32px' }}>
          <div className="ll-kicker">Desktop client</div>
          <h1 className="ll-h1" style={{ fontSize: 28 }}>Sign in with a code</h1>

          <div className="ll-steps" style={{ marginTop: 22 }}>
            <div className="ll-steps__item">
              <span className="ll-steps__num">1</span>
              <span>In your Discord server, run <span className="ll-kbd">/register</span>. The bot replies with a one-time code — only you can see it.</span>
            </div>
            <div className="ll-steps__item">
              <span className="ll-steps__num">2</span>
              <span>Paste the code below. It expires after 15 minutes.</span>
            </div>
          </div>

          <form onSubmit={(e) => { e.preventDefault(); void pair(); }} style={{ marginTop: 24 }}>
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="PAIRING CODE"
              aria-label="Pairing code"
              autoFocus
              code
              hero
              autoComplete="off"
            />
            {err && <div style={{ marginTop: 12 }}><Notice tone="err">{err}</Notice></div>}
            <PrimaryButton type="submit" size="lg" block busy={busy} disabled={!code.trim()} style={{ marginTop: 14 }}>
              {busy ? 'Pairing…' : 'Pair this device'}
            </PrimaryButton>
          </form>
        </div>

        <div className="ll-center" style={{ gap: 8, marginTop: 18, fontSize: 12.5, color: lcu === 'online' ? '#0ac8b9' : '#6b6a5c' }}>
          <Icon name={lcu === 'online' ? 'check' : 'info'} size={14} />
          {lcu === 'online'
            ? 'League client detected — your account links right after sign-in.'
            : 'Tip: open the League client too, so your account can be linked after sign-in.'}
        </div>
      </div>
    </div>
  );
}
