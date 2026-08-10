import React, { useState } from 'react';
import { theme as t } from '../theme';
import { Kicker, PrimaryButton, AsyncState, Spinner } from '../ui';
import { lol, useAsync, errText, type MyTeam, type Tournament } from '../bridge';

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
  // Set when a 5v5 code needs the captain to say WHICH of their teams enters.
  const [pick, setPick] = useState<{ tournamentName: string; teams: MyTeam[] } | null>(null);

  async function finishJoin(joinCode: string, teamId?: string) {
    const r = await b!.joinTournament(joinCode, teamId);
    if (!r.ok) { setMsg(errText(r)); return; }
    setPick(null); setMsg(`Joined ${r.tournament.name} ✓`); setCode(''); reload();
  }

  async function join() {
    if (!b || !code.trim()) return;
    const joinCode = code.trim();
    setBusy(true); setMsg(null); setPick(null);
    try {
      // Look the code up BEFORE enrolling: only a team tournament may ask which
      // team enters, and asking after the fact would already have joined one.
      const preview = await b.lookupTournament(joinCode);
      if (!preview.ok) { setMsg(errText(preview)); return; }

      if (preview.tournament.gameMode === 'aram_1v1') {
        await finishJoin(joinCode);
        return;
      }

      const mine = await b.myTeams();
      if (!mine.ok) { setMsg(errText(mine)); return; }
      const captained = mine.teams.filter((tm) => tm.role === 'captain' && tm.status === 'active');
      if (captained.length === 0) {
        setMsg('This is a team tournament — create a team first, then join as its captain.');
        return;
      }
      if (captained.length === 1) {
        await finishJoin(joinCode, captained[0]!.teamId);
        return;
      }
      setPick({ tournamentName: preview.tournament.name, teams: captained });
    } finally {
      setBusy(false);
    }
  }

  async function joinWithTeam(teamId: string) {
    setBusy(true);
    try {
      await finishJoin(code.trim(), teamId);
    } finally {
      setBusy(false);
    }
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

      {pick && (
        <div style={{ border: `1px solid ${t.gold}`, background: t.panel, padding: '20px 24px', marginBottom: 20 }}>
          <div style={{ fontSize: 11, letterSpacing: '.16em', textTransform: 'uppercase', color: t.gold, marginBottom: 6 }}>
            Which team enters?
          </div>
          <div style={{ fontSize: 13, color: t.textDim, marginBottom: 14 }}>
            {pick.tournamentName} is a team tournament and you captain {pick.teams.length} teams.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {pick.teams.map((tm) => (
              <button
                key={tm.teamId}
                onClick={() => joinWithTeam(tm.teamId)}
                disabled={busy}
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: t.bg, border: `1px solid ${t.line}`, color: t.textStrong, fontFamily: t.body, fontSize: 14, padding: '12px 16px', borderRadius: 2, cursor: busy ? 'not-allowed' : 'pointer', textAlign: 'left' }}
              >
                <span>{tm.name}</span>
                <span style={{ color: t.gold, fontSize: 12, letterSpacing: '.1em', textTransform: 'uppercase' }}>enter</span>
              </button>
            ))}
          </div>
          <button onClick={() => setPick(null)} style={{ marginTop: 14, background: 'transparent', border: 'none', color: t.textDim, fontSize: 13, cursor: 'pointer', padding: 0 }}>Cancel</button>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <AsyncState loading={loading} error={error}>
          {tournaments.length === 0 && (
            <div style={{ color: t.textDim, fontSize: 14, lineHeight: 1.6 }}>
              You&rsquo;re not in any tournament yet.<br />
              Paste a join code above — or, in a team tournament, wait for your captain
              to enter the team. You&rsquo;ll appear here automatically.
            </div>
          )}
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
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ fontFamily: t.display, fontSize: 22, color: t.goldBright }}>{tr.name}</span>
                        {/* Every tournament on this screen is one you are in — a
                            teammate never redeemed a code and would otherwise have
                            no idea why it is listed at all. */}
                        <span style={{ fontSize: 10, letterSpacing: '.14em', textTransform: 'uppercase', color: t.teal, border: `1px solid ${t.teal}`, padding: '3px 8px', borderRadius: 2 }}>
                          you&rsquo;re in
                        </span>
                      </div>
                      <div style={{ fontSize: 13, color: t.text, marginTop: 6 }}>{meta(tr)}</div>
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 12, letterSpacing: '.14em', textTransform: 'uppercase', color: accent }}>{featured ? '◆ ' : ''}{tr.status.replace('_', '-')}</div>
                    {tr.startAt && <div style={{ fontSize: 13, color: t.textDim, marginTop: 6 }}>starts {new Date(tr.startAt).toLocaleString()}</div>}
                    <div style={{ fontSize: 11, color: t.textDim, marginTop: 6 }}>{open ? 'hide bracket' : 'show bracket'}</div>
                  </div>
                </div>
                {/* Entrants + Lock in are always visible: they are what a player
                    came here to do, and behind a collapsed row they read as missing.
                    Only the bracket — which does not exist until an admin starts —
                    stays behind the toggle. */}
                <EntrantsPanel tournamentId={tr.id} solo={tr.gameMode === 'aram_1v1'} maxTeams={tr.maxTeams} />
                {open && <BracketDetail tournamentId={tr.id} />}
              </div>
            );
          })}
        </AsyncState>
      </div>
    </div>
  );
}

/** Who is in, who has locked in, and the caller's own Lock in button.
 *
 * Without this the screen said nothing at all after a successful join — the only
 * detail was the bracket, which does not exist until an admin starts.
 */
function EntrantsPanel({ tournamentId, solo, maxTeams }: { tournamentId: string; solo: boolean; maxTeams: number }) {
  const b = lol();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const { data, loading, error, reload } = useAsync(async () => {
    const r = await b!.tournamentEntrants(tournamentId);
    if (!r.ok) throw new Error(errText(r));
    return r.entrants;
  }, [tournamentId], 5000);

  async function lockIn() {
    if (!b) return;
    setBusy(true); setMsg(null);
    const r = await b.tournamentLockIn(tournamentId);
    setBusy(false);
    if (!r.ok) { setMsg(errText(r)); return; }
    reload();
  }

  const entrants = data ?? [];
  const me = entrants.find((e) => e.isYou);
  const noun = solo ? 'players' : 'teams';
  // How many of MY teammates still have to press it. Your captain redeemed the code
  // for all of you, so the thing you actually need to know is who is still missing.
  const myPending = me ? me.players.filter((p) => !p.lockedIn).length : 0;
  const border = { borderTop: `1px solid ${t.lineSoft}`, padding: '20px 28px' } as const;

  if (loading && entrants.length === 0) {
    return <div style={{ ...border, display: 'flex', gap: 12, alignItems: 'center', color: t.textDim }}><Spinner /> Loading entrants…</div>;
  }
  if (error) return <div style={{ ...border, color: t.red, fontSize: 13 }}>{error}</div>;

  return (
    <div style={border}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ fontSize: 11, letterSpacing: '.16em', textTransform: 'uppercase', color: t.gold }}>
          {entrants.length} / {maxTeams} {noun} · {entrants.filter((e) => e.lockedIn).length} locked in
        </div>
        {me?.canLockIn && (
          <PrimaryButton onClick={lockIn} disabled={busy} style={{ padding: '0 22px', fontSize: 12, height: 34 }}>
            {busy ? 'Locking in…' : 'Lock in'}
          </PrimaryButton>
        )}
        {me && !me.canLockIn && (
          <div style={{ fontSize: 13, color: me.lockedIn ? t.teal : t.gold }}>
            {me.isSolo
              ? 'You are locked in ✓'
              : me.lockedIn
                ? 'Your team is locked in ✓'
                : `You are locked in ✓ · waiting on ${myPending} teammate${myPending === 1 ? '' : 's'}`}
          </div>
        )}
      </div>

      {/* Your own roster. A teammate never redeems the join code — the captain
          entered them — so this is where they find out they are in, and that the
          Lock in button above is theirs to press. */}
      {me && !me.isSolo && me.players.length > 0 && (
        <div style={{ border: `1px solid ${t.lineSoft}`, background: t.bg, padding: '12px 16px', marginBottom: 14 }}>
          <div style={{ fontSize: 11, letterSpacing: '.16em', textTransform: 'uppercase', color: t.textDim, marginBottom: 10 }}>
            Your team · {me.name}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {me.players.map((p) => (
              <div key={p.userId} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                <span style={{ color: p.isYou ? t.goldBright : t.text }}>
                  {p.name}{p.isYou && <span style={{ color: t.textDim, fontSize: 12 }}> · you</span>}
                </span>
                <span style={{ color: p.lockedIn ? t.teal : t.textDim, fontSize: 12, letterSpacing: '.1em', textTransform: 'uppercase' }}>
                  {p.lockedIn ? 'locked in' : 'not ready'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {entrants.length === 0 && <div style={{ color: t.textDim, fontSize: 14 }}>Nobody has joined yet.</div>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {entrants.map((e) => (
          <div key={e.teamId} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', border: `1px solid ${e.isYou ? t.gold : t.line}`, background: t.bg, padding: '10px 14px' }}>
            <span style={{ fontSize: 14, color: e.isYou ? t.goldBright : t.textStrong }}>
              {e.name}{e.isYou && <span style={{ color: t.textDim, fontSize: 12 }}> · you</span>}
            </span>
            <span style={{ fontSize: 12, letterSpacing: '.1em', textTransform: 'uppercase', color: e.lockedIn ? t.teal : t.textDim }}>
              {e.lockedIn
                ? 'locked in'
                : e.isSolo || e.players.length === 0
                  ? 'not ready'
                  : `${e.players.filter((p) => p.lockedIn).length}/${e.players.length} locked in`}
            </span>
          </div>
        ))}
      </div>

      {msg && <div style={{ marginTop: 12, color: t.red, fontSize: 13 }}>{msg}</div>}
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
