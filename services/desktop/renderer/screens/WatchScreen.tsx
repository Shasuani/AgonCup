import React, { useState } from 'react';
import { theme as t } from '../theme';
import { PageHead, Panel, Badge, Notice, AsyncState, EmptyState } from '../ui';
import { Scoreboard, teamKills, useGameClock } from '../scoreboard';
import { lol, useAsync, errText, type LiveBoard, type PresenceEntry } from '../bridge';

/** How often the open scoreboard asks for a fresher board (clients send every ~5s). */
const BOARD_POLL_MS = 3_000;

/**
 * Watch a tournament game while it is played: every game presence lists, and for
 * the one picked, the live scoreboard its players' clients share — champion,
 * level, spells, items, K/D/A and CS. Only what TAB shows everyone in the game;
 * display-only, never the result.
 */
export default function WatchScreen() {
  const b = lol();
  const games = useAsync(async () => {
    const r = await b!.presence();
    if (!r.ok) throw new Error(errText(r));
    return r.entries;
  }, [], 10_000);
  const entries = games.data ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const selected = entries.find((e) => e.matchId === picked) ?? entries[0] ?? null;

  return (
    <div>
      <PageHead kicker="Watch live" title="Games on now" intro="Tournament games being played right now. Pick one to follow its scoreboard — what every player sees on TAB, a few seconds behind." />

      <AsyncState loading={games.loading} error={games.error} label="Looking for live games…">
        {entries.length === 0 && (
          <Panel tone="dashed">
            <EmptyState icon="eye" title="No games on right now">
              When a tournament game starts anywhere on AgonCup, it shows up here to watch.
            </EmptyState>
          </Panel>
        )}
        {entries.length > 0 && (
          <div className="ll-stack">
            {entries.length > 1 && (
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {entries.map((e) => (
                  <GamePick key={e.matchId} entry={e} active={e.matchId === selected?.matchId} onPick={() => setPicked(e.matchId)} />
                ))}
              </div>
            )}
            {selected && <LiveGame key={selected.matchId} entry={selected} />}
          </div>
        )}
      </AsyncState>
    </div>
  );
}

function GamePick({ entry: e, active, onPick }: { entry: PresenceEntry; active: boolean; onPick: () => void }) {
  return (
    <button
      type="button"
      onClick={onPick}
      className="ll-entrant ll-entrant__head"
      aria-pressed={active}
      style={{ cursor: 'pointer', textAlign: 'left', color: active ? t.goldBright : t.text, borderColor: active ? t.gold : undefined, display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}
    >
      <span style={{ fontSize: 14, fontWeight: 600 }}>{e.tournamentName}</span>
      <span className="ll-dim" style={{ fontSize: 12 }}>{e.guildName} · {modeShort(e.gameMode)}</span>
    </button>
  );
}

function LiveGame({ entry }: { entry: PresenceEntry }) {
  const b = lol();
  const board = useAsync(async (): Promise<LiveBoard | null> => {
    if (!b || typeof b.liveBoard !== 'function') throw new Error('This build cannot show live games — restart the app to update.');
    const r = await b.liveBoard(entry.matchId);
    if (r.ok) return r.board;
    if (r.status === 404) return null; // live, but no scoreboard has arrived yet
    throw new Error(errText(r));
  }, [entry.matchId], BOARD_POLL_MS);
  const live = board.data;
  const clock = useGameClock(live, live?.matchId);
  const players = live?.players ?? [];

  return (
    <Panel flush featured>
      <div className="ll-panel__head" style={{ padding: '18px 22px', alignItems: 'center' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <h2 className="ll-h2" style={{ fontSize: 22 }}>{entry.tournamentName}</h2>
            <Badge tone="red" dot pulse>Live</Badge>
          </div>
          <div className="ll-dim" style={{ fontSize: 12.5, marginTop: 6 }}>{entry.guildName} · {modeText(entry.gameMode)}</div>
        </div>
        {live && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 22, flexShrink: 0 }}>
            <div style={{ fontFamily: t.display, fontSize: 26, color: t.textStrong, fontVariantNumeric: 'tabular-nums' }} aria-label="Team kills">
              <span style={{ color: '#5aa8ff' }}>{teamKills(players, 'blue')}</span>
              <span className="ll-dim" style={{ margin: '0 10px', fontSize: 18 }}>vs</span>
              <span style={{ color: '#ff6b6b' }}>{teamKills(players, 'red')}</span>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div className="ll-cap" style={{ color: t.textDim }}>Game time</div>
              <div style={{ fontFamily: t.display, fontSize: 22, color: t.goldBright, fontVariantNumeric: 'tabular-nums' }} aria-label="Game time">{clock}</div>
            </div>
          </div>
        )}
      </div>

      {board.error && <div className="ll-panel__section"><Notice tone="err">{board.error}</Notice></div>}
      {!board.error && board.loading && <div className="ll-panel__section ll-dim">Loading the scoreboard…</div>}
      {!board.error && !board.loading && live === null && (
        <div className="ll-panel__section"><Notice tone="gold">The game is on — waiting for the first scoreboard from the players' clients.</Notice></div>
      )}

      {live && (
        <>
          <Scoreboard players={players} />
          <div className="ll-panel__section ll-dim" style={{ fontSize: 12 }}>
            Scoreboard shared by the players' clients · updates every few seconds · display-only
          </div>
        </>
      )}
    </Panel>
  );
}

function modeShort(gameMode: string): string {
  return gameMode === 'aram_1v1' ? '1v1 ARAM' : '5v5 Rift';
}

function modeText(gameMode: string): string {
  return gameMode === 'aram_1v1' ? '1v1 ARAM · Howling Abyss' : "5v5 · Summoner's Rift";
}
