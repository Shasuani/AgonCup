import React, { useState } from 'react';
import { theme as t } from '../theme';
import { Kicker, PrimaryButton, AsyncState } from '../ui';
import { lol, useAsync, errText, type LinkedAccount } from '../bridge';

function riotId(a: LinkedAccount): string {
  return a.gameName && a.tagLine ? `${a.gameName}#${a.tagLine}` : (a.gameName ?? a.accountId);
}

export default function AccountsScreen() {
  const b = lol();
  const { data, loading, error, reload } = useAsync(async () => {
    const r = await b!.listAccounts();
    if (!r.ok) throw new Error(errText(r));
    return r.accounts;
  }, []);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function unlink(id: string) {
    if (!b) return;
    setBusy(id); setMsg(null);
    const r = await b.unlinkAccount(id);
    setBusy(null);
    if (!r.ok) { setMsg(errText(r)); return; }
    reload();
  }

  async function link() {
    if (!b) return;
    setBusy('link'); setMsg(null);
    const s = await b.readSummoner();
    if (!s.ok) { setBusy(null); setMsg(s.detail ?? s.reason ?? 'Could not read the local League client. Is it running?'); return; }
    const v = await b.verify();
    setBusy(null);
    if (!v.ok) { setMsg(v.detail ?? 'Verification failed.'); return; }
    setMsg(`Linked ${v.gameName ?? ''}${v.tagLine ? '#' + v.tagLine : ''} ✓`);
    reload();
  }

  const accounts = data ?? [];

  return (
    <div>
      <Kicker>Accounts</Kicker>
      <h1 style={{ fontFamily: t.display, fontWeight: 700, fontSize: 32, color: t.goldBright, margin: '10px 0 22px' }}>Linked Riot accounts</h1>

      {msg && <div style={{ marginBottom: 16, maxWidth: 760, color: msg.includes('✓') ? t.teal : t.red, fontSize: 13, border: `1px solid ${msg.includes('✓') ? 'rgba(10,200,185,.4)' : 'rgba(196,74,74,.4)'}`, background: msg.includes('✓') ? 'rgba(10,200,185,.06)' : 'rgba(196,74,74,.06)', padding: '12px 16px', borderRadius: 2 }}>{msg}</div>}

      <div style={{ border: `1px solid ${t.line}`, background: t.panel, padding: '26px 28px', display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 760 }}>
        <AsyncState loading={loading} error={error}>
          {accounts.length === 0 && <div style={{ color: t.textDim, fontSize: 14 }}>No linked accounts yet. Link one from your local client below.</div>}
          {accounts.map((a, i) => (
            <div key={a.accountId} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: i < accounts.length - 1 ? `1px solid ${t.lineSoft}` : 'none', paddingBottom: i < accounts.length - 1 ? 20 : 0 }}>
              <div>
                <div style={{ color: t.textStrong, fontSize: 17, fontWeight: 600 }}>{riotId(a)}</div>
                <div style={{ fontFamily: t.mono, fontSize: 12, color: t.textDim, marginTop: 6 }}>{a.region ?? '—'} · {a.rankText || 'Unranked'}</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
                <span style={{ fontSize: 12, color: a.verified ? t.teal : t.gold, letterSpacing: '.08em' }}>{'◆'} {a.verified ? 'VERIFIED' : 'PENDING'}</span>
                <button onClick={() => unlink(a.accountId)} disabled={busy === a.accountId} style={{ fontFamily: t.body, fontSize: 12, background: 'transparent', color: t.red, border: '1px solid rgba(196,74,74,.4)', padding: '8px 16px', borderRadius: 2, cursor: busy ? 'not-allowed' : 'pointer', opacity: busy === a.accountId ? 0.5 : 1 }}>{busy === a.accountId ? 'Unlinking…' : 'Unlink'}</button>
              </div>
            </div>
          ))}
        </AsyncState>
      </div>

      <div style={{ border: `1px dashed ${t.line}`, background: 'rgba(10,200,185,.03)', padding: '24px 28px', marginTop: 20, maxWidth: 760 }}>
        <div style={{ fontFamily: t.display, fontSize: 18, color: t.goldBright }}>Link a new account</div>
        <p style={{ fontSize: 14, lineHeight: 1.5, margin: '12px 0 18px', color: t.text }}>Reads your Riot ID and PUUID straight from the local League client, then double-verifies server-side via Account-V1. Read-only &mdash; outside the write-gate.</p>
        <PrimaryButton onClick={link} disabled={busy === 'link'}>{busy === 'link' ? 'Reading…' : 'Read from local client'}</PrimaryButton>
      </div>
    </div>
  );
}
