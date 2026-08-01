import React, { useState } from 'react';
import { theme as t } from '../theme';
import { Kicker, PrimaryButton, AsyncState, Spinner } from '../ui';
import { lol, useAsync, errText, type Tournament } from '../bridge';

const STATUS_ACCENT: Record<string, string> = {
  running: t.teal, check_in: t.gold, open: t.gold, draft: t.textDim, completed: t.textDim, cancelled: t.red,
};

function meta(tr: Tournament): string {
  const fmt = tr.format === 'double_elimination' ? 'Double Elimination' : 'Single Elimination';
  const mode = tr.gameMode === 'aram_1v1' ? '1v1 ARAM' : "Summoner's Rift 5v5";
  return `${fmt} · ${tr.maxTeams} teams · Bo${tr.bestOf} · ${mode}`;
}

export default function TournamentsScreen() {
  const b = lol();
  const { data, loading, error, reload } = useAsync(async () => {
    const r = await b!.listMyTournaments();
    if (!r.ok) throw new Error(errText(r));
    return r.tournaments;
  }, [], 5000); // refresh the list every 5s so status flips (draft→running) show live
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  async function join() {
    if (!b || !code.trim()) return;
    setBusy(true); setMsg(null);
    const r = await b.joinTournament(code.trim());
    setBusy(false);
    if (!r.ok) { setMsg(errText(r)); return; }
    setMsg(`Joined ${r.tournament.name} ✓`); setCode(''); reload();
  }

  const tournaments = data ?? [];

  return (
    <div>
      <Kicker>Tournaments</Kicker>
      <h1 style={{ fontFamily: t.display, fontWeight: 700, fontSize: 32, color: t.goldBright, margin: '10px 0 22px' }}>Join &amp; follow</h1>

      <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
        <input value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && join()} placeholder="Enter join code…" style={{ flex: 1, background: t.panel, border: `1px solid ${t.line}`, color: t.textStrong, fontFamily: t.mono, fontSize: 14, padding: '14px 18px', borderRadius: 2 }} />
        <PrimaryButton onClick={join} disabled={busy || !code.trim()} style={{ padding: '0 30px', fontSize: 14 }}>{busy ? 'Joining…' : 'Join'}</PrimaryButton>
      </div>
      {msg && <div style={{ marginBottom: 20, color: msg.includes('✓') ? t.teal : t.red, fontSize: 13 }}>{msg}</div>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <AsyncState loading={loading} error={error}>
          {tournaments.length === 0 && <div style={{ color: t.textDim, fontSize: 14 }}>You haven&rsquo;t joined any tournaments yet. Paste a join code above.</div>}
          {tournaments.map((tr) => {
            const accent = STATUS_ACCENT[tr.status] ?? t.textDim;
            const featured = tr.status === 'running';
            const open = openId === tr.id;
            return (
              <div key={tr.id} style={{ border: `1px solid ${featured ? t.gold : t.line}`, background: t.panel }}>
                <div onClick={() => setOpenId(open ? null : tr.id)} style={{ padding: '24px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                    <span style={{ color: t.gold, fontSize: 13, transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>{'▸'}</span>
                    <div>
                      <div style={{ fontFamily: t.display, fontSize: 22, color: t.goldBright }}>{tr.name}</div>
                      <div style={{ fontSize: 13, color: t.text, marginTop: 6 }}>{meta(tr)}</div>
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 12, letterSpacing: '.14em', textTransform: 'uppercase', color: accent }}>{featured ? '◆ ' : ''}{tr.status.replace('_', '-')}</div>
                    {tr.startAt && <div style={{ fontSize: 13, color: t.textDim, marginTop: 6 }}>starts {new Date(tr.startAt).toLocaleString()}</div>}
                  </div>
                </div>
                {open && <BracketDetail tournamentId={tr.id} />}
              </div>
            );
          })}
        </AsyncState>
      </div>
    </div>
  );
}

function BracketDetail({ tournamentId }: { tournamentId: string }) {
  const b = lol();
  const { data, loading, error } = useAsync(async () => {
    const r = await b!.tournamentBracket(tournamentId);
    if (!r.ok) throw new Error(errText(r));
    return r.matches;
  }, [tournamentId], 3000); // live-refresh every 3s while expanded

  if (loading) return <div style={{ borderTop: `1px solid ${t.lineSoft}`, padding: '20px 28px', display: 'flex', gap: 12, alignItems: 'center', color: t.textDim }}><Spinner /> Loading bracket…</div>;
  if (error) return <div style={{ borderTop: `1px solid ${t.lineSoft}`, padding: '20px 28px', color: t.red, fontSize: 13 }}>{error}</div>;

  const matches = data ?? [];
  if (matches.length === 0) {
    return <div style={{ borderTop: `1px solid ${t.lineSoft}`, padding: '20px 28px', color: t.textDim, fontSize: 14 }}>Bracket not generated yet — teams register first, then an admin starts the tournament.</div>;
  }

  const rounds = [...new Set(matches.map((m) => m.round))].sort((a, c) => a - c);

  return (
    <div style={{ borderTop: `1px solid ${t.lineSoft}`, padding: '22px 28px', display: 'flex', gap: 28, overflowX: 'auto' }}>
      {rounds.map((round) => (
        <div key={round} style={{ minWidth: 240 }}>
          <div style={{ fontSize: 11, letterSpacing: '.16em', textTransform: 'uppercase', color: t.gold, marginBottom: 12 }}>Round {round}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {matches.filter((m) => m.round === round).sort((a, c) => a.slot - c.slot).map((m) => (
              <div key={m.id} style={{ border: `1px solid ${t.line}`, background: t.bg, padding: '10px 14px' }}>
                <Side name={m.teamAName} won={!!m.winnerTeamId && m.winnerTeamId === m.teamAId} ready={m.teamAReady} live={m.status === 'in_progress'} />
                <div style={{ height: 1, background: t.lineSoft, margin: '8px 0' }} />
                <Side name={m.teamBName} won={!!m.winnerTeamId && m.winnerTeamId === m.teamBId} ready={m.teamBReady} live={m.status === 'in_progress'} />
                <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: t.textDim, marginTop: 8 }}>{m.status.replace('_', ' ')}</div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function Side({ name, won, ready, live }: { name: string | null; won: boolean; ready: boolean; live: boolean }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {live && !name ? null : <span style={{ width: 6, height: 6, borderRadius: '50%', background: ready ? t.teal : t.textDim }} />}
      <span style={{ flex: 1, color: won ? t.goldBright : name ? t.text : t.textDim, fontWeight: won ? 600 : 400, fontSize: 14 }}>{name ?? 'TBD'}</span>
      {won && <span style={{ fontSize: 11, color: t.gold }}>{'◆'}</span>}
    </div>
  );
}
