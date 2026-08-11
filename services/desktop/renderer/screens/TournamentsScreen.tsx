import React, { useState } from 'react';
import { theme as t } from '../theme';
import { Kicker, PrimaryButton, AsyncState, Spinner } from '../ui';
import { lol, useAsync, errText, type BracketMatch, type Entrant, type MyTeam, type Tournament } from '../bridge';

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
  // Collapsed-by-id rather than open-by-id: a tournament you are in should show
  // its entrants and your Lock in button without a click, but sixteen entries
  // still need to be foldable away.
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
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
          {tournaments.map((tr) => (
            <TournamentCard
              key={tr.id}
              tournament={tr}
              open={!collapsed.has(tr.id)}
              onToggle={() => toggle(tr.id)}
              onRemoved={reload}
            />
          ))}
        </AsyncState>
      </div>
    </div>
  );
}

/** A bare text button that lives INSIDE the click-to-expand header row.
 *
 * The whole header toggles the card, so every one of these has to swallow the
 * click — without that, removing a tournament would also fold the card it was in.
 */
function QuietButton(
  { onClick, color, disabled, inline, children }:
  { onClick: () => void; color: string; disabled?: boolean; inline?: boolean; children: React.ReactNode },
) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      disabled={disabled}
      style={{
        background: 'transparent', border: 'none', padding: 0, color,
        fontFamily: t.body, fontSize: 11, letterSpacing: '.1em', textTransform: 'uppercase',
        cursor: disabled ? 'not-allowed' : 'pointer',
        marginTop: inline ? 0 : 8, display: inline ? 'inline' : 'block', marginLeft: inline ? 0 : 'auto',
      }}
    >
      {children}
    </button>
  );
}

/** One tournament: header, who is in, and the bracket.
 *
 * The entrants are fetched HERE rather than inside each panel: the bracket only
 * knows team ids and names, so showing a match's two rosters means reusing this
 * exact data. Fetching it twice would double the polling for one screen.
 */
function TournamentCard({ tournament: tr, open, onToggle, onRemoved }: { tournament: Tournament; open: boolean; onToggle: () => void; onRemoved: () => void }) {
  const b = lol();
  const accent = STATUS_ACCENT[tr.status] ?? t.textDim;
  const featured = tr.status === 'running';
  const entrants = useAsync(async () => {
    const r = await b!.tournamentEntrants(tr.id);
    if (!r.ok) throw new Error(errText(r));
    return r.entrants;
  }, [tr.id], 5000);

  // Clearing a finished tournament off YOUR list. Offered only once it is over:
  // a tournament you still have to play is reachable only from here, so removing
  // it would strand you away from your own Lock in button and bracket.
  const finished = tr.status === 'completed' || tr.status === 'cancelled';
  const [confirming, setConfirming] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  async function remove() {
    if (!b) return;
    setRemoving(true); setRemoveError(null);
    const r = await b.hideTournament(tr.id);
    setRemoving(false);
    setConfirming(false);
    if (!r.ok) { setRemoveError(errText(r)); return; }
    onRemoved();
  }

  return (
    <div style={{ border: `1px solid ${featured ? t.gold : t.line}`, background: t.panel }}>
      <div onClick={onToggle} style={{ padding: '24px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <span style={{ color: t.gold, fontSize: 13, transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>{'▸'}</span>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontFamily: t.display, fontSize: 22, color: t.goldBright }}>{tr.name}</span>
              {/* Every tournament on this screen is one you are in — a teammate
                  never redeemed a code and would otherwise have no idea why it
                  is listed at all. */}
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
          <div style={{ fontSize: 11, color: t.textDim, marginTop: 6 }}>{open ? 'collapse' : 'expand'}</div>

          {/* Two steps on purpose: there is no undo from the client, and this
              button sits inside a row whose whole job is to be clicked. */}
          {finished && !confirming && (
            <QuietButton
              onClick={() => { setConfirming(true); setRemoveError(null); }}
              color={t.textDim}
            >
              remove from my list
            </QuietButton>
          )}
          {finished && confirming && (
            <div style={{ marginTop: 8, display: 'flex', gap: 12, justifyContent: 'flex-end', alignItems: 'baseline' }}>
              <span style={{ fontSize: 11, color: t.textDim }}>remove?</span>
              <QuietButton onClick={remove} color={t.red} disabled={removing} inline>
                {removing ? 'removing…' : 'yes'}
              </QuietButton>
              <QuietButton onClick={() => setConfirming(false)} color={t.textDim} inline>
                no
              </QuietButton>
            </div>
          )}
          {removeError && <div style={{ fontSize: 11, color: t.red, marginTop: 6 }}>{removeError}</div>}
        </div>
      </div>

      {open && (
        <>
          <EntrantsPanel
            tournamentId={tr.id}
            solo={tr.gameMode === 'aram_1v1'}
            maxTeams={tr.maxTeams}
            entrants={entrants.data ?? []}
            loading={entrants.loading}
            error={entrants.error}
            reload={entrants.reload}
          />
          <BracketDetail tournamentId={tr.id} entrants={entrants.data ?? []} />
        </>
      )}
    </div>
  );
}

/** Who is in, who has locked in, and the caller's own Lock in button.
 *
 * Without this the screen said nothing at all after a successful join — the only
 * detail was the bracket, which does not exist until an admin starts.
 */
function EntrantsPanel({ tournamentId, solo, maxTeams, entrants: data, loading, error, reload }: {
  tournamentId: string;
  solo: boolean;
  maxTeams: number;
  entrants: Entrant[];
  loading: boolean;
  error: string | null;
  reload: () => void;
}) {
  const b = lol();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function lockIn() {
    if (!b) return;
    setBusy(true); setMsg(null);
    const r = await b.tournamentLockIn(tournamentId);
    setBusy(false);
    if (!r.ok) { setMsg(errText(r)); return; }
    reload();
  }

  const entrants = data;
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

      {/* Two columns: sixteen entries in one list is a scroll, in two it is a
          field you can read at a glance. Solo entries have no roster to open,
          so they stay flat. */}
      <div style={{ display: 'grid', gridTemplateColumns: entrants.length > 6 ? '1fr 1fr' : '1fr', gap: 8, alignItems: 'start' }}>
        {entrants.map((e, i) => (
          <EntrantRow key={e.teamId} entrant={e} seed={i + 1} />
        ))}
      </div>

      {msg && <div style={{ marginTop: 12, color: t.red, fontSize: 13 }}>{msg}</div>}
    </div>
  );
}

/** One entrant: a team you can open to see who is on it, or a lone 1v1 player.
 *
 * A 16-team list of names with a status on the right tells you nothing about WHO
 * you are about to play — the roster was only ever visible for your own team.
 */
function EntrantRow({ entrant: e, seed }: { entrant: Entrant; seed: number }) {
  const [open, setOpen] = useState(false);
  const expandable = !e.isSolo && e.players.length > 0;
  const ready = e.players.filter((p) => p.lockedIn).length;

  return (
    <div style={{ border: `1px solid ${e.isYou ? t.gold : t.line}`, background: t.bg }}>
      <div
        onClick={expandable ? () => setOpen(!open) : undefined}
        style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', cursor: expandable ? 'pointer' : 'default' }}
      >
        <span style={{ fontFamily: t.mono, fontSize: 11, color: t.textDim, minWidth: 22 }}>#{seed}</span>
        {expandable && (
          <span style={{ color: t.gold, fontSize: 11, transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>{'▸'}</span>
        )}
        <span style={{ flex: 1, fontSize: 14, color: e.isYou ? t.goldBright : t.textStrong }}>
          {e.name}{e.isYou && <span style={{ color: t.textDim, fontSize: 12 }}> · you</span>}
        </span>
        <span style={{ fontSize: 11, letterSpacing: '.1em', textTransform: 'uppercase', color: e.lockedIn ? t.teal : t.textDim, whiteSpace: 'nowrap' }}>
          {e.lockedIn ? '✓ locked in' : expandable ? `${ready}/${e.players.length} ready` : 'not ready'}
        </span>
      </div>

      {open && (
        <div style={{ borderTop: `1px solid ${t.lineSoft}`, padding: '10px 14px 12px 46px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {e.players.map((p) => (
            <div key={p.userId} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
              <span style={{ color: p.isYou ? t.goldBright : t.text }}>
                {p.name}{p.isYou && <span style={{ color: t.textDim, fontSize: 12 }}> · you</span>}
              </span>
              <span style={{ color: p.lockedIn ? t.teal : t.textDim, fontSize: 11, letterSpacing: '.1em', textTransform: 'uppercase' }}>
                {p.lockedIn ? 'locked in' : 'not ready'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BracketDetail({ tournamentId, entrants }: { tournamentId: string; entrants: Entrant[] }) {
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
  const first = rounds[0] ?? 1;

  return (
    <div style={{ borderTop: `1px solid ${t.lineSoft}`, padding: '22px 28px', display: 'flex', gap: 26, overflowX: 'auto' }}>
      {rounds.map((round) => {
        const inRound = matches.filter((m) => m.round === round).sort((a, c) => a.slot - c.slot);
        // Each round halves the field, so a match must span the vertical space of
        // the two that feed it and sit centred between them. Without that the
        // columns were three top-aligned lists and the tree was unreadable —
        // nothing showed which pair produced which later match.
        const span = 2 ** (round - first);
        return (
          <div key={round} style={{ minWidth: 236, display: 'flex', flexDirection: 'column' }}>
            <div style={{ fontSize: 11, letterSpacing: '.16em', textTransform: 'uppercase', color: t.gold, marginBottom: 14 }}>
              {round === rounds[rounds.length - 1] ? 'Final' : `Round ${round}`}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
              {inRound.map((m) => (
                <div key={m.id} style={{ flex: span, display: 'flex', alignItems: 'center', paddingBottom: 10 }}>
                  <div style={{ width: '100%' }}>
                    <MatchCard match={m} entrants={entrants} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** One match in the bracket. A decided match reads as decided at a glance.
 *
 * The winner used to be a gold tint and a small ◆ against an otherwise identical
 * row — on a dark panel, next to a loser rendered in near-white, it was easy to
 * read the wrong name as the winner. Now the whole card changes: a gold edge,
 * the winner's row lit and labelled WON, and the loser dimmed out of the way.
 */
function MatchCard({ match: m, entrants }: { match: BracketMatch; entrants: Entrant[] }) {
  const [open, setOpen] = useState(false);
  const decided = !!m.winnerTeamId;
  const aWon = decided && m.winnerTeamId === m.teamAId;
  const bWon = decided && m.winnerTeamId === m.teamBId;
  const live = m.status === 'in_progress';

  // A 5v5 bracket shows two team names and tells you nothing about who you are
  // actually about to play. The rosters are already on screen in the entrants
  // list, so match them up by team id rather than asking the server again.
  const rosterOf = (teamId: string | null) =>
    (teamId ? entrants.find((e) => e.teamId === teamId)?.players : undefined) ?? [];
  const rosters = [rosterOf(m.teamAId), rosterOf(m.teamBId)];
  const expandable = rosters.some((r) => r.length > 0);

  return (
    <div style={{ border: `1px solid ${decided ? t.gold : live ? t.teal : t.line}`, background: t.bg }}>
      <Side name={m.teamAName} won={aWon} lost={decided && !aWon} ready={m.teamAReady} live={live} />
      <div style={{ height: 1, background: t.lineSoft }} />
      <Side name={m.teamBName} won={bWon} lost={decided && !bWon} ready={m.teamBReady} live={live} />

      <div
        onClick={expandable ? () => setOpen(!open) : undefined}
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: decided ? t.gold : t.textDim, padding: '7px 14px', borderTop: `1px solid ${t.lineSoft}`, cursor: expandable ? 'pointer' : 'default' }}
      >
        <span>{live ? '◆ live' : m.status.replace('_', ' ')}</span>
        {expandable && <span style={{ color: t.textDim }}>{open ? 'hide players' : 'players'}</span>}
      </div>

      {open && (
        <div style={{ borderTop: `1px solid ${t.lineSoft}`, padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[m.teamAName, m.teamBName].map((teamName, side) => (
            <div key={side}>
              <div style={{ fontSize: 10, letterSpacing: '.14em', textTransform: 'uppercase', color: side === 0 ? (aWon ? t.gold : t.textDim) : bWon ? t.gold : t.textDim, marginBottom: 4 }}>
                {teamName ?? 'TBD'}
              </div>
              {rosters[side]!.length === 0 ? (
                <div style={{ fontSize: 12, color: t.textDim }}>—</div>
              ) : (
                rosters[side]!.map((p) => (
                  <div key={p.userId} style={{ fontSize: 12, color: p.isYou ? t.goldBright : t.text, lineHeight: 1.7 }}>
                    {p.name}{p.isYou && <span style={{ color: t.textDim }}> · you</span>}
                  </div>
                ))
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Side({ name, won, lost, ready, live }: { name: string | null; won: boolean; lost: boolean; ready: boolean; live: boolean }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', background: won ? 'rgba(200,155,60,.14)' : 'transparent' }}>
      {live && !name ? null : <span style={{ width: 6, height: 6, borderRadius: '50%', background: won ? t.gold : ready ? t.teal : t.textDim, flexShrink: 0 }} />}
      <span style={{ flex: 1, color: won ? t.goldBright : lost ? t.textDim : name ? t.text : t.textDim, fontWeight: won ? 700 : 400, fontSize: 14, textDecoration: lost ? 'line-through' : 'none', opacity: lost ? 0.65 : 1 }}>
        {name ?? 'TBD'}
      </span>
      {won && (
        <span style={{ fontSize: 10, letterSpacing: '.14em', color: t.bg, background: t.gold, padding: '2px 7px', borderRadius: 2, fontWeight: 700 }}>WON</span>
      )}
    </div>
  );
}
