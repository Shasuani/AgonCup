import React from 'react';
import { theme as t } from '../theme';
import { PageHead, Panel, Badge, StatCard, AsyncState, EmptyState } from '../ui';
import { ChampIcon, ItemRow, SpellPair, championLoadingUrl, useChampions } from '../champion';
import { findMe, kdaRatio, myRecord, myRiotIds, playerLabel, shortWhen, timeAgo, topChampion } from '../format';
import { lol, useAsync, errText, type BridgeUser, type HistoryGame } from '../bridge';

/**
 * The player's own games, one scoreboard each. Display-only — the scoreboards
 * are rebuilt from the players' end-of-game reports, so this is what the
 * players saw, not the adjudicated result (that lives in the bracket).
 */
export default function HistoryScreen({ user }: { user: BridgeUser | null }) {
  const b = lol();
  const accounts = useAsync(async () => { const r = await b!.listAccounts(); return r.ok ? r.accounts : []; }, []);
  const history = useAsync(async () => {
    if (!b || typeof b.myHistory !== 'function') throw new Error('This build cannot load match history — restart the app to update.');
    const r = await b.myHistory();
    if (!r.ok) throw new Error(errText(r));
    return r.games;
  }, []);

  const ids = myRiotIds(accounts.data ?? []);
  const me = user?.displayName ?? null;
  const games = history.data ?? [];
  const rec = myRecord(games, ids, me);
  const fav = topChampion(rec);
  const winRate = rec.games > 0 ? Math.round((rec.wins / rec.games) * 100) : null;
  const avgKda = rec.games > 0 ? kdaRatio({ kills: rec.kills, deaths: rec.deaths, assists: rec.assists }) : '—';

  return (
    <div>
      <PageHead kicker="Match history" title="Your games" intro="Every tournament game you played, as your client reported it. The bracket carries the official result." />

      <div className="ll-grid-4">
        <StatCard label="Games" value={history.loading ? '—' : rec.games} />
        <StatCard label="Wins" value={history.loading ? '—' : rec.wins} accent={t.teal} sub={winRate === null ? undefined : `${winRate}% win rate`} />
        <StatCard label="Avg KDA" value={history.loading ? '—' : avgKda.replace(' KDA', '')} sub={rec.games > 0 ? `${rec.kills} / ${rec.deaths} / ${rec.assists} total` : undefined} />
        <div className="ll-stat">
          <div className="ll-stat__label">Most played</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10 }}>
            <ChampIcon id={fav ?? 0} size={40} ring="gold" />
            <div className="ll-stat__value" style={{ marginTop: 0, fontSize: 22 }}>{fav === null ? '—' : `${rec.champions.get(fav) ?? 0}×`}</div>
          </div>
        </div>
      </div>

      <div className="ll-stack" style={{ marginTop: 20 }}>
        <AsyncState loading={history.loading} error={history.error} label="Loading your games…">
          {games.length === 0 && (
            <Panel tone="dashed">
              <EmptyState icon="clock" title="No games yet">
                Scoreboards appear here after your first tournament match finishes and the clients report it.
              </EmptyState>
            </Panel>
          )}
          {games.map((g) => <GameCard key={`${g.matchId}:${g.lcuGameId}`} game={g} riotIds={ids} displayName={me} />)}
        </AsyncState>
      </div>
    </div>
  );
}

function GameCard({ game: g, riotIds, displayName }: { game: HistoryGame; riotIds: Set<string>; displayName: string | null }) {
  const me = findMe(g, riotIds, displayName);
  const champions = useChampions();
  const art = me ? championLoadingUrl(champions, me.player.championId) : null;
  const won = me ? g.teams[me.team]?.isWinner === true : null;
  const roundText = g.round != null ? `Round ${g.round}` : null;
  return (
    <Panel flush className={won === true ? 'll-panel--teal' : undefined}>
      <div className="ll-game-head" style={art ? { backgroundImage: `linear-gradient(90deg, rgba(7,19,34,0.35), #071322 42%), url("${art}")`, backgroundSize: 'auto 100%, 260px auto', backgroundPosition: 'left center, left 0 top 22%', backgroundRepeat: 'no-repeat' } : undefined}>
        <div className="ll-game-head__champ">
          <ChampIcon id={me?.player.championId ?? 0} size={52} ring={won === null ? 'none' : won ? 'teal' : 'red'} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="ll-game-head__title">{g.tournamentName ?? 'Match'}</span>
            {won !== null && <Badge tone={won ? 'teal' : 'red'}>{won ? 'Victory' : 'Defeat'}</Badge>}
          </div>
          <div className="ll-game-head__sub">
            {roundText && <>{roundText} · </>}{shortWhen(g.playedAt)} · {timeAgo(g.playedAt)}
          </div>
        </div>
        {me && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
            {(me.player.items.length > 0 || me.player.spell1Id !== null) && (
              <div className="ll-board__build">
                <SpellPair a={me.player.spell1Id} b={me.player.spell2Id} size={20} />
                <ItemRow items={me.player.items} size={26} />
              </div>
            )}
            <div style={{ textAlign: 'right' }}>
              <div className="ll-board__kda" style={{ fontSize: 18, fontFamily: t.display, color: t.goldBright }}>{me.player.kills} / {me.player.deaths} / {me.player.assists}</div>
              <div className="ll-dim" style={{ fontSize: 11.5, marginTop: 2 }}>
                {kdaRatio(me.player)}
                {me.player.minionsKilled !== null && ` · ${me.player.minionsKilled} CS`}
                {me.player.goldEarned !== null && ` · ${(me.player.goldEarned / 1000).toFixed(1)}k gold`}
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="ll-board">
        {g.teams.map((team, ti) => (
          <div key={ti} className={`ll-board__team${team.isWinner ? ' ll-board__team--win' : ''}`}>
            <div className="ll-board__head">
              <span className="ll-cap" style={{ color: team.isWinner ? t.teal : t.textDim }}>{ti === 0 ? 'Blue side' : 'Red side'}</span>
              {team.isWinner ? <Badge tone="teal">Won</Badge> : <Badge tone="dim">Lost</Badge>}
            </div>
            {team.players.map((p, pi) => {
              const isMe = me?.team === ti && me.player === p;
              return (
                <div key={p.puuid || pi} className={`ll-board__row${isMe ? ' ll-board__row--me' : ''}`}>
                  <ChampIcon id={p.championId} name={p.championName ?? undefined} size={28} ring="none" level={p.level} />
                  {(p.spell1Id !== null || p.spell2Id !== null) && <SpellPair a={p.spell1Id} b={p.spell2Id} size={13} />}
                  <span className="ll-board__name" title={p.championName ?? undefined}>{playerLabel(p)}{isMe && <span className="ll-you"> · you</span>}</span>
                  {p.items.length > 0 && <ItemRow items={p.items} size={18} />}
                  <span className="ll-board__kda">{p.kills} / {p.deaths} / {p.assists}<small>{kdaRatio(p)}</small></span>
                </div>
              );
            })}
            {team.players.length === 0 && <div className="ll-dim" style={{ fontSize: 12 }}>—</div>}
          </div>
        ))}
      </div>
      {g.teams.length === 0 && <div className="ll-panel__section ll-dim" style={{ fontSize: 13 }}>No scoreboard was reported for this game.</div>}
    </Panel>
  );
}
