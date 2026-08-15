import React from 'react';
import { theme as t } from '../theme';
import { Kicker } from '../ui';
import { lol, useAsync, errText, type MyMatch } from '../bridge';

// The live lobby, as the backend knows it. This screen used to show only which
// tournaments were RUNNING and say the orchestration state "is not yet on the IPC
// bridge" — so a player had no way to tell whether the custom game existed, who
// was making it, or what to type if nobody was. All of that now comes from
// GET /orchestration/matches/mine.

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
  host_lost: 'Nobody could host this one — create it yourself, details below',
};

/** True once the lobby demonstrably exists in the League client. */
function lobbyExists(state: string | null): boolean {
  return state === 'lobby_open' || state === 'filling' || state === 'ready' ||
    state === 'starting' || state === 'in_game' || state === 'result_pending';
}

function Dot({ colour }: { colour: string }) {
  return (
    <span style={{ fontSize: 12, color: colour, letterSpacing: '.1em', animation: 'llBlink 1.6s ease-in-out infinite' }}>
      {'●'}
    </span>
  );
}

function Credential({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: t.textDim, letterSpacing: '.08em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 18, color: t.goldBright, marginTop: 4, userSelect: 'text' }}>{value}</div>
    </div>
  );
}

function MatchCard({ m }: { m: MyMatch }) {
  const exists = lobbyExists(m.lobbyState);
  const manual = m.lobbyState === 'host_lost' || (m.lobbyState !== null && m.hostName === null);
  const phase = m.lobbyState === null
    ? 'Drawn — the lobby has not been opened yet'
    : (PHASE_TEXT[m.lobbyState] ?? m.lobbyState);

  return (
    <div style={{ border: `1px solid ${exists ? t.gold : t.line}`, background: t.panel, padding: '22px 26px', marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 16 }}>
        <div>
          <div style={{ fontFamily: t.display, fontSize: 22, color: t.goldBright }}>{m.tournamentName}</div>
          <div style={{ fontSize: 13, color: t.textDim, marginTop: 4 }}>{m.roundLabel}</div>
        </div>
        <Dot colour={exists ? t.gold : t.red} />
      </div>

      <div style={{ fontSize: 14, color: t.text, marginTop: 14, lineHeight: 1.5 }}>{phase}</div>

      {m.isHost && (
        <div style={{ fontSize: 13, color: t.gold, marginTop: 10 }}>
          {'◆'} You are the host — your client is creating this lobby.
        </div>
      )}
      {!m.isHost && m.hostName !== null && (
        <div style={{ fontSize: 13, color: t.textDim, marginTop: 10 }}>
          Host: {m.hostName} — watch for the invite, it is accepted automatically.
        </div>
      )}

      {m.lobbyName !== null && m.lobbyPassword !== null && (
        <div style={{ marginTop: 18, paddingTop: 16, borderTop: `1px dashed ${t.line}` }}>
          {manual && (
            <div style={{ fontSize: 13, color: t.text, marginBottom: 14, lineHeight: 1.5 }}>
              No client is set up to host this match, so nothing will be created
              automatically. Make a <strong>custom game</strong> with these details —
              everyone else has the same ones and can join it directly.
            </div>
          )}
          <div style={{ display: 'flex', gap: 40 }}>
            <Credential label="Lobby name" value={m.lobbyName} />
            <Credential label="Password" value={m.lobbyPassword} />
          </div>
        </div>
      )}
    </div>
  );
}

export default function LiveMatchScreen() {
  const b = lol();
  const { data, loading, error } = useAsync(async () => {
    const r = await b!.listMyMatches();
    if (!r.ok) throw new Error(errText(r));
    return r.matches;
  }, []);

  const matches = data ?? [];

  return (
    <div>
      <Kicker>Live Match &middot; Orchestration</Kicker>
      <h1 style={{ fontFamily: t.display, fontWeight: 700, fontSize: 32, color: t.goldBright, margin: '10px 0 0' }}>Live match</h1>

      {loading && <div style={{ color: t.textDim, marginTop: 24 }}>Loading…</div>}
      {error && <div style={{ color: t.red, marginTop: 24, fontSize: 13 }}>{error}</div>}

      {!loading && !error && matches.length === 0 && (
        <div style={{ marginTop: 40, border: `1px dashed ${t.line}`, background: t.panel, borderRadius: 3, padding: '56px 40px', textAlign: 'center' }}>
          <div style={{ fontSize: 40, color: t.goldDim, marginBottom: 16 }}>{'▸'}</div>
          <div style={{ fontFamily: t.display, fontSize: 22, color: t.goldBright }}>No live match right now</div>
          <div style={{ fontSize: 14, color: t.text, marginTop: 10, maxWidth: 460, marginInline: 'auto', lineHeight: 1.5 }}>
            When one of your tournaments starts your match, the lobby appears here —
            who is hosting, whether the custom game exists yet, and the name and
            password to join it by hand.
          </div>
        </div>
      )}

      {!loading && !error && matches.length > 0 && (
        <div style={{ marginTop: 24 }}>
          {matches.map((m) => <MatchCard key={m.matchId} m={m} />)}
        </div>
      )}
    </div>
  );
}
