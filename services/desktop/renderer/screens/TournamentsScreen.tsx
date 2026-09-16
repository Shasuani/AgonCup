import React, { useState } from 'react';
import { theme as t } from '../theme';
import { PageHead, PrimaryButton, Spinner, Input, Panel, Badge, Chip, Feedback, Notice, LinkButton, EmptyState, type Tone } from '../ui';
import { Icon } from '../icons';
import { lol, useAsync, errText, type BracketMatch, type Entrant, type MyTeam, type Tournament } from '../bridge';

const STATUS_TONE: Record<string, Tone> = {
  running: 'teal', check_in: 'gold', open: 'gold', draft: 'dim', completed: 'dim', cancelled: 'red',
};

const STATUS_LABEL: Record<string, string> = {
  running: 'Running', check_in: 'Check-in', open: 'Open', draft: 'Draft', completed: 'Finished', cancelled: 'Cancelled',
};

// Short server labels players actually say ("EUNE", not "eun1"). Unmapped codes
// fall back to the upper-cased route so a new platform still shows something.
const REGION_SHORT: Record<string, string> = {
  eun1: 'EUNE', euw1: 'EUW', na1: 'NA', br1: 'BR', la1: 'LAN', la2: 'LAS',
  oc1: 'OCE', tr1: 'TR', ru: 'RU', jp1: 'JP',
};

export function regionShort(region: string | null): string | null {
  if (region === null) return null;
  return REGION_SHORT[region.toLowerCase()] ?? region.toUpperCase();
}

function MetaChips({ tr }: { tr: Tournament }) {
  const fmt = tr.format === 'double_elimination' ? 'Double elimination' : 'Knockout';
  const mode = tr.gameMode === 'aram_1v1' ? '1v1 ARAM' : "Summoner's Rift 5v5";
  const region = regionShort(tr.region);
  // The region is part of what the tournament IS — a player on the wrong server
  // cannot play it — so it sits in the one summary line every view shows.
  return (
    <div className="ll-chips">
      <Chip icon="map">{mode}</Chip>
      <Chip icon="trophy">{fmt}</Chip>
      <Chip icon="users">{tr.maxTeams} {tr.gameMode === 'aram_1v1' ? 'players' : 'teams'}</Chip>
      <Chip icon="bolt">Bo{tr.bestOf}</Chip>
      {region && <Chip title={tr.region ?? undefined}>{region}</Chip>}
    </div>
  );
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
      // Only a team on the tournament's own server can enter it; the list spans
      // every server the player belongs to, so filter by the code's server.
      const captained = mine.teams.filter((tm) =>
        tm.role === 'captain' && tm.status === 'active'
        && (!preview.tournament.guildId || !tm.guildId || tm.guildId === preview.tournament.guildId));
      if (captained.length === 0) {
        setMsg('This is a team tournament — create a team on this server first, then join as its captain.');
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
      <PageHead kicker="Tournaments" title="Join & follow" intro="Paste a join code from your Discord server to enter. Everything you're in shows up below, with the bracket once it starts." />

      <form className="ll-field" onSubmit={(e) => { e.preventDefault(); void join(); }} style={{ marginBottom: 14 }}>
        <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="Enter a join code…" aria-label="Join code" code autoComplete="off" />
        <PrimaryButton type="submit" icon="plus" busy={busy} disabled={!code.trim()} style={{ height: 44, padding: '0 28px' }}>Join</PrimaryButton>
      </form>
      <Feedback msg={msg} style={{ marginBottom: 16 }} />

      {pick && (
        <Panel featured tight style={{ marginBottom: 16 }}>
          <div className="ll-kicker" style={{ marginBottom: 6 }}>Which team enters?</div>
          <div className="ll-dim" style={{ fontSize: 13, marginBottom: 14 }}>
            {pick.tournamentName} is a team tournament and you captain {pick.teams.length} teams.
          </div>
          <div className="ll-stack" style={{ gap: 8 }}>
            {pick.teams.map((tm) => (
              <button
                key={tm.teamId}
                type="button"
                onClick={() => joinWithTeam(tm.teamId)}
                disabled={busy}
                className="ll-entrant ll-entrant__head"
                style={{ width: '100%', justifyContent: 'space-between', color: t.textStrong, fontSize: 14, cursor: busy ? 'not-allowed' : 'pointer', textAlign: 'left' }}
              >
                <span>{tm.name}</span>
                <span className="ll-cap" style={{ color: t.gold }}>Enter →</span>
              </button>
            ))}
          </div>
          <div style={{ marginTop: 14 }}><LinkButton onClick={() => setPick(null)} style={{ color: t.textDim }}>Cancel</LinkButton></div>
        </Panel>
      )}

      <div className="ll-stack">
        {loading && <div className="ll-loading"><Spinner /> Loading your tournaments…</div>}
        {error && <Notice tone="err">{error}</Notice>}
        {!loading && !error && tournaments.length === 0 && (
          <Panel tone="dashed">
            <EmptyState icon="trophy" title="You're not in any tournament yet">
              Paste a join code above — or, in a team tournament, wait for your captain
              to enter the team. You'll appear here automatically.
            </EmptyState>
          </Panel>
        )}
        {!loading && !error && tournaments.map((tr) => (
          <TournamentCard
            key={tr.id}
            tournament={tr}
            open={!collapsed.has(tr.id)}
            onToggle={() => toggle(tr.id)}
            onRemoved={reload}
          />
        ))}
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
  { onClick, color, disabled, children }:
  { onClick: () => void; color: string; disabled?: boolean; children: React.ReactNode },
) {
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      disabled={disabled}
      className="ll-link"
      style={{ color, fontSize: 11, letterSpacing: '.1em', textTransform: 'uppercase', cursor: disabled ? 'not-allowed' : 'pointer' }}
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
  const tone = STATUS_TONE[tr.status] ?? 'dim';
  const featured = tr.status === 'running';
  const entrants = useAsync(async () => {
    const r = await b!.tournamentEntrants(tr.id, tr.guildId);
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
    const r = await b.hideTournament(tr.id, tr.guildId);
    setRemoving(false);
    setConfirming(false);
    if (!r.ok) { setRemoveError(errText(r)); return; }
    onRemoved();
  }

  return (
    <Panel flush featured={featured}>
      <div className="ll-card-head" onClick={onToggle} role="button" aria-expanded={open} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, minWidth: 0 }}>
          <Icon name="chevron" size={16} className={`ll-chevron${open ? ' is-open' : ''}`} />
          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <h2 className="ll-h2" style={{ fontSize: 22 }}>{tr.name}</h2>
              {/* Every tournament on this screen is one you are in — a teammate
                  never redeemed a code and would otherwise have no idea why it
                  is listed at all. */}
              <Badge tone="teal">You're in</Badge>
            </div>
            <div style={{ marginTop: 8 }}><MetaChips tr={tr} /></div>
          </div>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
          <Badge tone={tone} dot pulse={featured}>{STATUS_LABEL[tr.status] ?? tr.status.replace('_', ' ')}</Badge>
          {tr.startAt && <div className="ll-dim" style={{ fontSize: 12.5 }}>Starts {new Date(tr.startAt).toLocaleString()}</div>}

          {/* Two steps on purpose: there is no undo from the client, and this
              button sits inside a row whose whole job is to be clicked. */}
          {finished && !confirming && (
            <QuietButton onClick={() => { setConfirming(true); setRemoveError(null); }} color={t.textDim}>
              remove from my list
            </QuietButton>
          )}
          {finished && confirming && (
            <div style={{ display: 'flex', gap: 12, alignItems: 'baseline' }}>
              <span style={{ fontSize: 11, color: t.textDim }}>remove?</span>
              <QuietButton onClick={remove} color="#ff9e9e" disabled={removing}>
                {removing ? 'removing…' : 'yes'}
              </QuietButton>
              <QuietButton onClick={() => setConfirming(false)} color={t.textDim}>
                no
              </QuietButton>
            </div>
          )}
          {removeError && <div style={{ fontSize: 11, color: '#ff9e9e' }}>{removeError}</div>}
        </div>
      </div>

      {open && (
        <>
          <EntrantsPanel
            tournamentId={tr.id}
            guildId={tr.guildId}
            solo={tr.gameMode === 'aram_1v1'}
            maxTeams={tr.maxTeams}
            entrants={entrants.data ?? []}
            loading={entrants.loading}
            error={entrants.error}
            reload={entrants.reload}
          />
          <BracketDetail tournamentId={tr.id} guildId={tr.guildId} entrants={entrants.data ?? []} />
        </>
      )}
    </Panel>
  );
}

/** Who is in, who has locked in, and the caller's own Lock in button.
 *
 * Without this the screen said nothing at all after a successful join — the only
 * detail was the bracket, which does not exist until an admin starts.
 */
function EntrantsPanel({ tournamentId, guildId, solo, maxTeams, entrants: data, loading, error, reload }: {
  tournamentId: string;
  guildId: string;
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
    const r = await b.tournamentLockIn(tournamentId, undefined, guildId);
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
  const lockedCount = entrants.filter((e) => e.lockedIn).length;

  if (loading && entrants.length === 0) {
    return <div className="ll-panel__section"><div className="ll-loading" style={{ padding: 0 }}><Spinner /> Loading entrants…</div></div>;
  }
  if (error) return <div className="ll-panel__section"><Notice tone="err">{error}</Notice></div>;

  return (
    <div className="ll-panel__section">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span className="ll-kicker" style={{ letterSpacing: '.16em' }}>{entrants.length} / {maxTeams} {noun}</span>
          <span className="ll-dim" style={{ fontSize: 12 }}>·</span>
          <span style={{ fontSize: 12.5, color: lockedCount === entrants.length && entrants.length > 0 ? t.teal : t.textDim }}>{lockedCount} locked in</span>
        </div>
        {me?.canLockIn && (
          <PrimaryButton size="sm" icon="check" busy={busy} onClick={lockIn}>
            {busy ? 'Locking in…' : 'Lock in'}
          </PrimaryButton>
        )}
        {me && !me.canLockIn && (
          <span style={{ fontSize: 13, color: me.lockedIn ? t.teal : t.gold, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Icon name="check" size={14} />
            {me.isSolo
              ? 'You are locked in'
              : me.lockedIn
                ? 'Your team is locked in'
                : `You are locked in · waiting on ${myPending} teammate${myPending === 1 ? '' : 's'}`}
          </span>
        )}
      </div>

      {/* Your own roster. A teammate never redeems the join code — the captain
          entered them — so this is where they find out they are in, and that the
          Lock in button above is theirs to press. */}
      {me && !me.isSolo && me.players.length > 0 && (
        <div className="ll-panel ll-panel--sunken" style={{ padding: '12px 16px', marginBottom: 14 }}>
          <div className="ll-cap" style={{ marginBottom: 10 }}>Your team · <span style={{ color: t.gold }}>{me.name}</span></div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '6px 24px' }}>
            {me.players.map((p) => (
              <div key={p.userId} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13, gap: 10 }}>
                <span style={{ color: p.isYou ? t.goldBright : t.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {p.name}{p.isYou && <span className="ll-you"> · you</span>}
                </span>
                <Badge tone={p.lockedIn ? 'teal' : 'dim'}>{p.lockedIn ? 'Locked in' : 'Not ready'}</Badge>
              </div>
            ))}
          </div>
        </div>
      )}

      {entrants.length === 0 && <div className="ll-dim" style={{ fontSize: 14 }}>Nobody has joined yet.</div>}

      {/* Two columns: sixteen entries in one list is a scroll, in two it is a
          field you can read at a glance. Solo entries have no roster to open,
          so they stay flat. */}
      <div className="ll-entrants" style={{ gridTemplateColumns: entrants.length > 6 ? '1fr 1fr' : '1fr' }}>
        {entrants.map((e, i) => (
          <EntrantRow key={e.teamId} entrant={e} seed={i + 1} />
        ))}
      </div>

      {msg && <div style={{ marginTop: 12 }}><Notice tone="err">{msg}</Notice></div>}
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
    <div className={`ll-entrant${e.isYou ? ' ll-entrant--you' : ''}`}>
      <div
        className="ll-entrant__head"
        onClick={expandable ? () => setOpen(!open) : undefined}
        style={{ cursor: expandable ? 'pointer' : 'default' }}
      >
        <span className="ll-entrant__seed">#{seed}</span>
        {expandable && <Icon name="chevron" size={13} className={`ll-chevron${open ? ' is-open' : ''}`} />}
        <span style={{ flex: 1, fontSize: 14, color: e.isYou ? t.goldBright : t.textStrong, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {e.name}{e.isYou && <span className="ll-you"> · you</span>}
        </span>
        {e.lockedIn
          ? <Badge tone="teal">Locked in</Badge>
          : <Badge tone="dim">{expandable ? `${ready}/${e.players.length} ready` : 'Not ready'}</Badge>}
      </div>

      {open && (
        <div className="ll-entrant__body">
          {e.players.map((p) => (
            <div key={p.userId} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13 }}>
              <span style={{ color: p.isYou ? t.goldBright : t.text }}>
                {p.name}{p.isYou && <span className="ll-you"> · you</span>}
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

function BracketDetail({ tournamentId, guildId, entrants }: { tournamentId: string; guildId: string; entrants: Entrant[] }) {
  const b = lol();
  const { data, loading, error } = useAsync(async () => {
    const r = await b!.tournamentBracket(tournamentId, guildId);
    if (!r.ok) throw new Error(errText(r));
    return r.matches;
  }, [tournamentId], 3000); // live-refresh every 3s while expanded

  if (loading) return <div className="ll-panel__section"><div className="ll-loading" style={{ padding: 0 }}><Spinner /> Loading bracket…</div></div>;
  if (error) return <div className="ll-panel__section"><Notice tone="err">{error}</Notice></div>;

  const matches = data ?? [];
  if (matches.length === 0) {
    return (
      <div className="ll-panel__section" style={{ display: 'flex', alignItems: 'center', gap: 12, color: t.textDim, fontSize: 13.5 }}>
        <Icon name="clock" size={16} /> Bracket not generated yet — entrants lock in first, then an admin starts the tournament.
      </div>
    );
  }

  const rounds = [...new Set(matches.map((m) => m.round))].sort((a, c) => a - c);
  const first = rounds[0] ?? 1;

  return (
    <div className="ll-panel__section">
      <div className="ll-bracket">
        {rounds.map((round) => {
          const inRound = matches.filter((m) => m.round === round).sort((a, c) => a.slot - c.slot);
          // Each round halves the field, so a match must span the vertical space of
          // the two that feed it and sit centred between them. Without that the
          // columns were three top-aligned lists and the tree was unreadable —
          // nothing showed which pair produced which later match.
          const span = 2 ** (round - first);
          return (
            <div key={round} className="ll-round">
              <div className="ll-round__title">
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
    <div className={`ll-match${decided ? ' ll-match--decided' : ''}${live ? ' ll-match--live' : ''}`}>
      <Side name={m.teamAName} won={aWon} lost={decided && !aWon} ready={m.teamAReady} live={live} />
      <Side name={m.teamBName} won={bWon} lost={decided && !bWon} ready={m.teamBReady} live={live} />

      <div
        className={`ll-match__foot${expandable ? ' is-clickable' : ''}`}
        onClick={expandable ? () => setOpen(!open) : undefined}
        style={decided ? { color: t.gold } : live ? { color: t.teal } : undefined}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {live && <span className="ll-dot ll-dot--teal ll-dot--pulse" style={{ width: 6, height: 6 }} />}
          {live ? 'Live' : m.status.replace('_', ' ')}
        </span>
        {expandable && <span>{open ? 'hide players' : 'players'}</span>}
      </div>

      {open && (
        <div style={{ borderTop: `1px solid ${t.lineSoft}`, padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[m.teamAName, m.teamBName].map((teamName, side) => (
            <div key={side}>
              <div className="ll-cap" style={{ color: side === 0 ? (aWon ? t.gold : t.textDim) : bWon ? t.gold : t.textDim, marginBottom: 4 }}>
                {teamName ?? 'TBD'}
              </div>
              {rosters[side]!.length === 0 ? (
                <div style={{ fontSize: 12, color: t.textDim }}>—</div>
              ) : (
                rosters[side]!.map((p) => (
                  <div key={p.userId} style={{ fontSize: 12, color: p.isYou ? t.goldBright : t.text, lineHeight: 1.7 }}>
                    {p.name}{p.isYou && <span className="ll-you"> · you</span>}
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
    <div className={`ll-side${won ? ' ll-side--won' : ''}${lost ? ' ll-side--lost' : ''}`}>
      {live && !name ? null : <span className={`ll-dot ll-dot--${won ? 'gold' : ready ? 'teal' : 'dim'}`} style={{ width: 6, height: 6, boxShadow: won || ready ? undefined : 'none' }} />}
      <span className="ll-side__name" style={!name ? { color: t.textDim } : undefined}>{name ?? 'TBD'}</span>
      {won && <Badge tone="solid">Won</Badge>}
    </div>
  );
}

