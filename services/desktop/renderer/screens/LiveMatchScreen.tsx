import React, { useEffect, useState } from 'react';
import { theme as t } from '../theme';
import { PageHead, Panel, Badge, Dot, Credential, Switch, EmptyState, Notice, Chip } from '../ui';
import { Icon } from '../icons';
import { ChampIcon, championName, useChampions } from '../champion';
import { Scoreboard, teamKills, useGameClock } from '../scoreboard';
import {
  lol, useAsync, useChampSelect, useLiveGame, errText,
  type MyMatch, type ChampSelectBoard, type ChampSelectMember, type LiveGame, type LiveGameBoard, type LcuStatus,
} from '../bridge';
import { STEPS, stepIndex, lobbyExists, isPlaying, localPhase, pickPlayingMatch, effectiveState } from '../livePhase';

// The live lobby, as the backend knows it. This screen used to show only which
// tournaments were RUNNING and say the orchestration state "is not yet on the IPC
// bridge" — so a player had no way to tell whether the custom game existed, who
// was making it, or what to type if nobody was. All of that now comes from
// GET /orchestration/matches/mine — pushed forward by what the League client on
// this machine shows (livePhase.ts), because a lobby opened by hand never leaves
// `host_lost` server-side and the screen used to sit on "Host" all game long.

/** How the lobby-state-machine phase reads to a player waiting on a game. */
const PHASE_TEXT: Record<string, string> = {
  assigned_host: 'A host has been picked — waiting for them to create the lobby',
  awaiting_create: 'Waiting for the host to create the custom game',
  lobby_open: 'The custom game exists — invites are going out',
  filling: 'Players are joining the lobby',
  ready: 'Everyone is in — starting shortly',
  starting: 'Champion select',
  in_game: 'Game in progress',
  result_pending: 'Game over — waiting on results',
  ended: 'Finished',
  aborted: 'Aborted',
  host_lost: 'This one is opened by hand — see below',
};

function Stepper({ state }: { state: string | null }) {
  const current = stepIndex(state);
  const finished = state === 'ended';
  return (
    <div className="ll-stepper" aria-label="Match progress">
      {STEPS.map((s, i) => {
        const done = finished || i < current;
        const cur = !finished && i === current;
        return (
          <div key={s.label} className={`ll-step${done ? ' is-done' : ''}${cur ? ' is-current' : ''}`}>
            <div className="ll-step__dot">{done && <Icon name="check" size={11} />}</div>
            <div className="ll-step__label">{s.label}</div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The per-session permission to drive the League client.
 *
 * It lives on this screen because this is where it matters and where it is
 * missed: every lobby write is refused until it is granted, so with it off the
 * invites never leave and the invite never arrives — a match that looks broken
 * rather than switched off. Off at every launch, by design (PLAN.md §3.3).
 */
function AutomationToggle() {
  const b = lol();
  // 'unavailable' = the running main process predates the automation IPC — an
  // old build. The box still renders and SAYS so: this used to return null and
  // a stale build looked like the feature simply did not exist.
  const [enabled, setEnabled] = useState<boolean | 'loading' | 'unavailable'>('loading');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!b || typeof b.getAutomation !== 'function') {
      setEnabled('unavailable');
      return;
    }
    b.getAutomation().then(
      (r) => setEnabled(r.enabled),
      () => setEnabled('unavailable'),
    );
  }, [b]);

  const toggle = async (): Promise<void> => {
    if (!b || typeof b.setAutomation !== 'function' || typeof enabled !== 'boolean') return;
    setBusy(true);
    try {
      const r = await b.setAutomation(!enabled);
      setEnabled(r.enabled);
    } catch {
      setEnabled('unavailable');
    } finally {
      setBusy(false);
    }
  };

  const on = enabled === true;
  return (
    <Panel tight featured={on} tone={enabled === 'unavailable' ? 'danger' : undefined} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 24 }}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
        <span style={{ color: on ? t.gold : t.textDim, display: 'inline-flex', marginTop: 2 }}><Icon name="bolt" size={20} /></span>
        <div>
          <div className="ll-strong" style={{ fontWeight: 600, fontSize: 15, color: enabled === 'unavailable' ? '#ffb3b3' : undefined }}>
            {enabled === 'loading' && 'Automation — checking…'}
            {enabled === 'unavailable' && 'Automation is unavailable — this client is running an old build. Close it fully and start it again.'}
            {enabled === true && 'Automation is on'}
            {enabled === false && 'Automation is off'}
          </div>
          <div className="ll-dim" style={{ fontSize: 12.5, marginTop: 4, lineHeight: 1.5, maxWidth: 560 }}>
            Lets this app create the lobby, invite your opponents and accept the
            lobby invite for you. It never touches anything in a game, and your
            choice is remembered on this device until you change it.
          </div>
        </div>
      </div>
      {typeof enabled === 'boolean' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
          <span className="ll-cap" style={{ color: on ? t.gold : t.textDim }}>{on ? 'On' : 'Off'}</span>
          <Switch on={on} disabled={busy} onToggle={() => void toggle()} label="Let this app drive the League client" />
        </div>
      )}
    </Panel>
  );
}

/** True for the 1v1 ARAM duel — the mode a single first blood decides. */
function isDuel(gameMode: string): boolean {
  return gameMode === 'aram_1v1';
}

/** One line naming the game's shape — same story the Discord DM now tells. */
function modeText(gameMode: string): string {
  return isDuel(gameMode)
    ? '1v1 ARAM · Howling Abyss · first blood wins'
    : "5v5 · Summoner's Rift";
}

/** Short form for the create-this settings row next to name + password. */
function modeShort(gameMode: string): string {
  return isDuel(gameMode) ? '1v1 ARAM' : '5v5 Rift';
}

function fmtGameClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const ss = String(total % 60).padStart(2, '0');
  return `${m}:${ss}`;
}

/**
 * The winner banner, straight off this machine's own game (`:2999`).
 *
 * A duel is DECIDED at first blood, so the banner goes up the moment the kill
 * lands — not minutes later when someone closes the game. A 5v5 is decided at
 * the nexus, so it waits for the end and then reads the local win/loss verdict.
 * Display-only: the recorded result still comes from everyone's reports
 * (corroboration), which is what "recorded automatically" below refers to.
 */
function LiveGamePanel({ view, duel }: { view: LiveGame; duel: boolean }) {
  const fb = view.firstBlood;
  const ended = view.status === 'ended';

  let kicker: string; let title: string; let sub: string | null = null;
  let decided = false;
  let won: boolean | null = null;

  if (duel) {
    if (fb !== null) {
      decided = true;
      won = fb.isYou;
      kicker = 'Duel decided · first blood';
      title = `${fb.scorer} wins the duel`;
      sub = fb.isYou === true
        ? `That's you — victory! First blood at ${fmtGameClock(fb.atSeconds)}.`
        : fb.isYou === false
          ? `Your opponent drew first blood at ${fmtGameClock(fb.atSeconds)} — defeat.`
          : `First blood at ${fmtGameClock(fb.atSeconds)}.`;
    } else if (ended) {
      kicker = 'Duel over';
      title = 'Game over';
      sub = 'No first blood was seen from this client — waiting for the recorded result.';
    } else {
      kicker = 'Duel live';
      title = 'First blood decides it';
      sub = 'The winner appears here the moment the kill lands.';
    }
  } else if (ended) {
    decided = view.result !== null;
    won = view.result === null ? null : view.result === 'win';
    kicker = 'Game over';
    title = view.result === 'win' ? 'Victory' : view.result === 'loss' ? 'Defeat' : 'Game over';
    sub = view.result === null
      ? 'Waiting for the recorded result.'
      : view.result === 'win' ? 'Your team won.' : 'The enemy team won.';
  } else {
    kicker = 'Game in progress';
    title = 'Playing to the nexus';
    sub = fb !== null
      ? `First blood: ${fb.scorer} at ${fmtGameClock(fb.atSeconds)}. The winner appears here when the game ends.`
      : 'The winner appears here when the game ends.';
  }

  const tone = decided ? (won === false ? 'red' : 'teal') : 'gold';
  return (
    <Panel featured tone={decided && won !== false ? 'teal' : undefined} style={{ padding: '22px 26px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
        <span className="ll-kicker" style={{ color: tone === 'red' ? '#ff9e9e' : tone === 'teal' ? t.teal : t.gold }}>{kicker}</span>
        <Dot tone={tone} pulse={!decided} />
      </div>
      <h2 className="ll-h2" style={{ fontSize: decided ? 30 : 22, marginTop: 8, color: tone === 'red' ? '#ffb3b3' : undefined }}>{title}</h2>
      {sub !== null && <p className="ll-lead" style={{ marginTop: 8 }}>{sub}</p>}
      {decided && (
        <div className="ll-dim" style={{ fontSize: 12.5, marginTop: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Icon name="check" size={14} /> The result is recorded automatically from everyone's reports — nothing to submit.
        </div>
      )}
    </Panel>
  );
}

/**
 * The scoreboard of THIS game, read from this machine's own `:2999` port —
 * champions, level, spells, items, K/D/A of everyone, the same board Watch
 * live draws for spectators, only without the server hop. Stays up, frozen on
 * its final numbers, once the game ends; display-only like the banner above it.
 */
function LiveBoardPanel({ board, ended }: { board: LiveGameBoard; ended: boolean }) {
  const clock = useGameClock(board, undefined, ended);
  return (
    <Panel flush featured>
      <div className="ll-panel__head" style={{ padding: '16px 22px', alignItems: 'center' }}>
        <div>
          <h2 className="ll-h2" style={{ fontSize: 20 }}>Scoreboard</h2>
          <div className="ll-dim" style={{ fontSize: 12.5, marginTop: 4 }}>{ended ? 'Final — as your client last saw it' : 'What everyone sees on TAB, from your League client'}</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 22, flexShrink: 0 }}>
          <div style={{ fontFamily: t.display, fontSize: 26, color: t.textStrong, fontVariantNumeric: 'tabular-nums' }} aria-label="Team kills">
            <span style={{ color: '#5aa8ff' }}>{teamKills(board.players, 'blue')}</span>
            <span className="ll-dim" style={{ margin: '0 10px', fontSize: 18 }}>vs</span>
            <span style={{ color: '#ff6b6b' }}>{teamKills(board.players, 'red')}</span>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="ll-cap" style={{ color: t.textDim }}>Game time</div>
            <div style={{ fontFamily: t.display, fontSize: 22, color: t.goldBright, fontVariantNumeric: 'tabular-nums' }} aria-label="Game time">{clock}</div>
          </div>
        </div>
      </div>
      <Scoreboard players={board.players} />
    </Panel>
  );
}

/** How the RE champ-select timer phase reads to a player. */
const SELECT_PHASE_TEXT: Record<string, string> = {
  PLANNING: 'Declaring intent',
  BAN_PICK: 'Picking champions',
  FINALIZATION: 'Everyone is locked — loading soon',
  GAME_STARTING: 'Game starting',
};

function Seat({ m }: { m: ChampSelectMember }) {
  const champions = useChampions();
  const champ = m.championName ?? (m.championId > 0 ? (championName(champions, m.championId) ?? `Champion #${m.championId}`) : null);
  return (
    <div className={`ll-seat${m.locked ? ' ll-seat--locked' : ''}`}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        <ChampIcon id={m.championId} name={champ ?? undefined} size={28} ring={m.locked ? 'gold' : 'none'} />
        <div className="ll-seat__name">{m.gameName ?? `Player ${m.cellId + 1}`}</div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, whiteSpace: 'nowrap' }}>
        <span className="ll-seat__champ" style={champ ? undefined : { color: t.textDim, fontWeight: 400 }}>{champ ?? '—'}</span>
        {m.locked
          ? <Badge tone="teal">Locked</Badge>
          : <Badge tone="dim">{champ ? 'Picking…' : 'Choosing…'}</Badge>}
      </div>
    </div>
  );
}

/**
 * The live champion-select board, read from THIS machine's League client. In a
 * custom every member's LCU exposes both teams, so each player sees the full
 * pick/lock state here without any server involvement; the panel disappears on
 * its own when the game loads (the LCU session ends).
 */
function ChampSelectPanel({ board }: { board: ChampSelectBoard }) {
  const mine = board.members.filter((m) => m.side === 'my');
  const theirs = board.members.filter((m) => m.side === 'their');
  const phase = (board.timerPhase && SELECT_PHASE_TEXT[board.timerPhase]) ?? 'Champion select';
  const column = (label: string, seats: ChampSelectMember[], tone: 'teal' | 'red') => (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div className="ll-cap" style={{ marginBottom: 6, color: tone === 'teal' ? t.teal : '#e07b7b' }}>{label}</div>
      {seats.length === 0 && <div className="ll-dim" style={{ fontSize: 13 }}>—</div>}
      {seats.map((m) => <Seat key={`${m.side}:${m.cellId}`} m={m} />)}
    </div>
  );
  return (
    <Panel featured style={{ padding: '22px 26px' }}>
      <div className="ll-panel__head">
        <div>
          <h2 className="ll-h2">Champion select</h2>
          <div className="ll-lead" style={{ marginTop: 4 }}>{phase}</div>
        </div>
        <Badge tone="gold" dot pulse>Live</Badge>
      </div>
      <div style={{ display: 'flex', gap: 36, marginTop: 16 }}>
        {column('Your team', mine, 'teal')}
        {column('Enemy team', theirs, 'red')}
      </div>
    </Panel>
  );
}

function MatchCard({ m, state }: { m: MyMatch; state: string | null }) {
  // `state` is the furthest of what the backend recorded and what the League
  // client on this machine shows (effectiveState): once the game is on, who was
  // to open the lobby and how to join it by hand no longer matter.
  const exists = lobbyExists(state);
  const inPlay = isPlaying(state);
  // "By hand" = no client took the host lease, so a human opens the custom game.
  // The backend still names WHO (host_puuid), which is what tells one player it
  // is theirs to open and everyone else who they are waiting for; only a match
  // with nobody attached at all has no name to give.
  const byHand = state === 'host_lost';
  const nobody = !exists && state !== null && m.hostName === null && !m.isHost;
  const phase = state === null
    ? 'Drawn — the lobby has not been opened yet'
    : (PHASE_TEXT[state] ?? state);
  const aborted = state === 'aborted';

  return (
    <Panel featured={exists} style={{ padding: '22px 26px' }}>
      <div className="ll-panel__head" style={{ alignItems: 'flex-start' }}>
        <div>
          <h2 className="ll-h2" style={{ fontSize: 22 }}>{m.tournamentName}</h2>
          <div className="ll-chips" style={{ marginTop: 8 }}>
            <Chip icon="trophy">{m.roundLabel}</Chip>
            <Chip icon="map">{modeText(m.gameMode)}</Chip>
          </div>
        </div>
        {inPlay
          ? <Badge tone="teal" dot pulse={state !== 'result_pending'}>{state === 'starting' ? 'Champ select' : state === 'in_game' ? 'In game' : 'Game over'}</Badge>
          : exists
            ? <Badge tone="teal" dot pulse>Lobby up</Badge>
            : aborted ? <Badge tone="red">Aborted</Badge> : <Badge tone="gold" dot pulse>Waiting</Badge>}
      </div>

      {!aborted && <Stepper state={state} />}

      <div className="ll-lead" style={{ marginTop: 18, color: t.textStrong }}>{phase}</div>

      {!inPlay && m.isHost && !byHand && (
        <div style={{ fontSize: 13, color: t.gold, marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
          <Icon name="crown" size={15} /> You are the host — your client is creating this lobby.
        </div>
      )}
      {!inPlay && m.isHost && byHand && (
        <div style={{ fontSize: 13, color: t.gold, marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
          <Icon name="crown" size={15} /> You are opening this match — create the custom game below.
        </div>
      )}
      {!inPlay && !m.isHost && m.hostName !== null && (
        <div className="ll-dim" style={{ fontSize: 13, marginTop: 8 }}>
          {byHand
            ? `${m.hostName} is opening the custom game — you will be invited automatically.`
            : `Host: ${m.hostName} — watch for the invite, it is accepted automatically.`}
        </div>
      )}

      {!inPlay && m.lobbyName !== null && m.lobbyPassword !== null && (
        <div className="ll-hr-dashed" style={{ marginBottom: 0 }}>
          {m.isHost && byHand && (
            <p className="ll-lead" style={{ margin: '16px 0 14px' }}>
              Make a <strong className="ll-strong">custom game</strong> — {modeText(m.gameMode)} — with
              these exact details. As soon as it exists this app invites the other
              players for you — leave it running.
            </p>
          )}
          {nobody && (
            <p className="ll-lead" style={{ margin: '16px 0 14px' }}>
              Nobody is set up to open this match. Make a <strong className="ll-strong">custom game</strong>{' '}
              with these details — everyone else has the same ones and can join it
              directly.
            </p>
          )}
          {!m.isHost && byHand && !nobody && (
            <p className="ll-dim" style={{ fontSize: 13, margin: '16px 0 14px' }}>
              If the invite does not arrive, join the custom game by hand with these:
            </p>
          )}
          {!(m.isHost && byHand) && !nobody && !(!m.isHost && byHand) && (
            <p className="ll-dim" style={{ fontSize: 12.5, margin: '14px 0 12px' }}>Lobby details, in case you need to join by hand:</p>
          )}
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
            <Credential label="Lobby name" value={m.lobbyName} />
            <Credential label="Password" value={m.lobbyPassword} />
            <Credential label="Game" value={modeShort(m.gameMode)} copy={false} />
          </div>
          {isDuel(m.gameMode) && (
            <div className="ll-dim" style={{ fontSize: 12.5, marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
              <Icon name="bolt" size={14} /> This is a duel: the game is decided by <strong className="ll-strong">first blood</strong> — one kill and it's over.
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

export default function LiveMatchScreen({ lcu }: { lcu?: LcuStatus }) {
  const b = lol();
  const board = useChampSelect();
  const live = useLiveGame();
  const { data, loading, error } = useAsync(async () => {
    const r = await b!.listMyMatches();
    if (!r.ok) throw new Error(errText(r));
    return r.matches;
  }, [], 10_000);

  const matches = data ?? [];
  // The banner and the scoreboard are for THIS tournament's game, not whatever
  // the player queues up on their own. The backend's phase used to be the only
  // gate — but a by-hand lobby never leaves `host_lost` server-side, so the game
  // ran with no banner and no items. The League client on this machine is the
  // witness now: champ select / in game / just ended, attributed to the open
  // match it fits (mode-checked against the live game, see livePhase.ts).
  const phase = localPhase(board, live);
  const playing = pickPlayingMatch(matches, phase, live?.board?.gameMode ?? null);

  return (
    <div>
      <PageHead kicker="Live match · Orchestration" title="Live match" intro="Your matches as they happen: who opens the lobby, whether it exists yet, and the name and password to join by hand." />

      <div className="ll-stack">
        <AutomationToggle />

        {lcu === 'offline' && matches.length > 0 && (
          <Notice tone="gold">The League client is not open. Start it so the lobby invite can reach you.</Notice>
        )}

        {live !== null && playing !== undefined && (
          <LiveGamePanel view={live} duel={isDuel(playing.gameMode)} />
        )}
        {live !== null && playing !== undefined && live.board && live.board.players.length > 0 && (
          <LiveBoardPanel board={live.board} ended={live.status === 'ended'} />
        )}

        {board !== null && <ChampSelectPanel board={board} />}

        {loading && <div className="ll-loading">Loading your matches…</div>}
        {error && <Notice tone="err">{error}</Notice>}

        {!loading && !error && matches.length === 0 && (
          <Panel tone="dashed" style={{ marginTop: 8 }}>
            <EmptyState icon="swords" title="No live match right now">
              When one of your tournaments starts your match, the lobby appears here —
              who is opening it, whether the custom game exists yet, and the name and
              password to join it by hand.
            </EmptyState>
          </Panel>
        )}

        {!loading && !error && matches.map((m) => (
          <MatchCard key={m.matchId} m={m} state={effectiveState(m, phase, playing)} />
        ))}
      </div>
    </div>
  );
}
