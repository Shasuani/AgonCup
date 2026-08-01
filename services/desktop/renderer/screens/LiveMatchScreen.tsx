import React from 'react';
import { theme as t } from '../theme';
import { Kicker } from '../ui';
import { lol, useAsync, errText } from '../bridge';

// The live lobby phase + per-player accept state come from the lol-realtime WS
// transport (orchestration), which is not yet exposed on the window.lol IPC bridge.
// Until it is, this screen only knows whether the signed-in user has a RUNNING
// tournament — if not, it shows an empty state (no fabricated match data).
export default function LiveMatchScreen() {
  const b = lol();
  const { data, loading, error } = useAsync(async () => {
    const r = await b!.listMyTournaments();
    if (!r.ok) throw new Error(errText(r));
    return r.tournaments.filter((tr) => tr.status === 'running');
  }, []);

  const running = data ?? [];

  return (
    <div>
      <Kicker>Live Match &middot; Orchestration</Kicker>
      <h1 style={{ fontFamily: t.display, fontWeight: 700, fontSize: 32, color: t.goldBright, margin: '10px 0 0' }}>Live match</h1>

      {loading && <div style={{ color: t.textDim, marginTop: 24 }}>Loading…</div>}
      {error && <div style={{ color: t.red, marginTop: 24, fontSize: 13 }}>{error}</div>}

      {!loading && !error && running.length === 0 && (
        <div style={{ marginTop: 40, border: `1px dashed ${t.line}`, background: t.panel, borderRadius: 3, padding: '56px 40px', textAlign: 'center' }}>
          <div style={{ fontSize: 40, color: t.goldDim, marginBottom: 16 }}>{'▸'}</div>
          <div style={{ fontFamily: t.display, fontSize: 22, color: t.goldBright }}>No live match right now</div>
          <div style={{ fontSize: 14, color: t.text, marginTop: 10, maxWidth: 460, marginInline: 'auto', lineHeight: 1.5 }}>
            When one of your tournaments starts your match, the autonomous lobby
            orchestration (host, invites, phase tracker, host controls) appears here.
          </div>
        </div>
      )}

      {!loading && !error && running.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <div style={{ marginBottom: 16, fontSize: 12, color: t.gold, border: `1px dashed ${t.line}`, background: 'rgba(200,170,110,.05)', padding: '8px 14px', borderRadius: 2, display: 'inline-block' }}>
            ◆ Live phase &amp; accept state wire to the realtime WS transport (not yet on the IPC bridge)
          </div>
          {running.map((tr) => (
            <div key={tr.id} style={{ border: `1px solid ${t.gold}`, background: t.panel, padding: '24px 28px', marginBottom: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontFamily: t.display, fontSize: 22, color: t.goldBright }}>{tr.name}</div>
                <div style={{ fontSize: 13, color: t.text, marginTop: 6 }}>Best of {tr.bestOf} · {tr.gameMode === 'aram_1v1' ? '1v1 ARAM' : "Summoner's Rift 5v5"}</div>
              </div>
              <span style={{ fontSize: 12, color: t.red, letterSpacing: '.1em', animation: 'llBlink 1.6s ease-in-out infinite' }}>{'●'} RUNNING</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
