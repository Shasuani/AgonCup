// Match history — per-game scoreboards of played games (PLAN.md §3.6.5 / §3.4).
//
// Read-only: lists recent games for the acting guild, each as a card showing the
// two teams (winner highlighted) and every player's champion + KDA. Reconstructed
// from the forgeable end-of-game reports, so it is the DISPLAY view — the official
// bracket-advancing winner still comes from adjudication / admin override. The
// route is role-gated to guild_admin / platform_operator (App.tsx RequireRole).
//
// All server-provided strings render as React children (escaped) — no raw HTML.

import { useApi } from "../../api/ApiProvider";
import { useAsyncData } from "../../api/useAsync";
import { Button, EmptyState, ErrorBanner, ScreenHeader, Skeleton } from "../../components";
import {
  fetchMatchHistory,
  kdaText,
  playerLabel,
  type HistoryGame,
  type HistoryPlayer,
  type HistoryTeam,
} from "./matchHistory";
import {
  championIconUrl,
  championName,
  itemIconUrl,
  spellIconUrl,
  spellName,
  useDdragon,
  type DdragonRegistry,
} from "../../lib/ddragon";

import "./MatchHistoryScreen.css";

/** Hide a champion icon that fails to load (unknown id / offline CDN). */
function onIconError(e: React.SyntheticEvent<HTMLImageElement>): void {
  e.currentTarget.style.visibility = "hidden";
}

/** Six item slots + the trinket, in slot order; empty slots stay empty. */
function ItemRow({ items, reg }: { items: number[]; reg: DdragonRegistry | null }): JSX.Element {
  const slots = [...items];
  while (slots.length < 7) slots.push(0);
  return (
    <span data-history-items aria-label="Final build">
      {slots.slice(0, 7).map((id, i) => {
        const src = itemIconUrl(reg, id);
        return src ? (
          <img key={i} data-history-item data-trinket={i === 6 || undefined} src={src} alt={`Item ${id}`} title={`Item ${id}`} width={20} height={20} loading="lazy" onError={onIconError} />
        ) : (
          <span key={i} data-history-item data-empty data-trinket={i === 6 || undefined} aria-hidden="true" />
        );
      })}
    </span>
  );
}

function SpellPair({ a, b, reg }: { a: number | null | undefined; b: number | null | undefined; reg: DdragonRegistry | null }): JSX.Element {
  const one = (id: number | null | undefined, i: number): JSX.Element => {
    const src = id != null ? spellIconUrl(reg, id) : null;
    const label = id != null ? spellName(reg, id) ?? `Spell ${id}` : "";
    return src ? (
      <img key={i} src={src} alt={label} title={label} width={14} height={14} loading="lazy" onError={onIconError} />
    ) : (
      <span key={i} data-empty aria-hidden="true" />
    );
  };
  return <span data-history-spells>{one(a, 0)}{one(b, 1)}</span>;
}

function PlayerRow({ player, reg }: { player: HistoryPlayer; reg: DdragonRegistry | null }): JSX.Element {
  const src = championIconUrl(reg, player.champion_id);
  const name = player.champion_name ?? championName(reg, player.champion_id) ?? undefined;
  const hasBuild = (player.items?.length ?? 0) > 0 || player.spell1_id != null;
  return (
    <li data-history-player>
      <span data-history-champ-wrap>
        {src ? (
          <img data-history-champ src={src} alt="" title={name} width={28} height={28} loading="lazy" onError={onIconError} />
        ) : (
          <span data-history-champ data-empty aria-hidden="true" />
        )}
        {player.level != null ? <span data-history-level>{player.level}</span> : null}
      </span>
      {hasBuild ? <SpellPair a={player.spell1_id} b={player.spell2_id} reg={reg} /> : null}
      <span data-history-name title={name}>{playerLabel(player)}</span>
      {hasBuild ? <ItemRow items={player.items ?? []} reg={reg} /> : null}
      <span data-history-kda>
        {kdaText(player)}
        {player.minions_killed != null ? <small>{player.minions_killed} CS</small> : null}
      </span>
    </li>
  );
}

function TeamColumn({ team, label, reg }: { team: HistoryTeam; label: string; reg: DdragonRegistry | null }): JSX.Element {
  return (
    <div data-history-team data-winner={team.is_winner ? "true" : "false"}>
      <div data-history-team-head>
        <span>{label}</span>
        {team.is_winner ? <span data-history-badge>Victory</span> : null}
      </div>
      <ul data-history-players>
        {team.players.map((p, i) => (
          <PlayerRow key={p.puuid || `${label}-${i}`} player={p} reg={reg} />
        ))}
      </ul>
    </div>
  );
}

function GameCard({ game, reg }: { game: HistoryGame; reg: DdragonRegistry | null }): JSX.Element {
  const played = new Date(game.played_at);
  const when = Number.isNaN(played.getTime()) ? game.played_at : played.toLocaleString();
  const heading = game.tournament_name ?? "Match";
  const roundText = game.round != null ? `Round ${game.round}` : null;
  return (
    <article data-history-card>
      <header data-history-card-head>
        <span data-history-tournament>{heading}</span>
        {roundText ? <span data-history-round>{roundText}</span> : null}
        <span data-history-when>{when}</span>
      </header>
      <div data-history-teams>
        {game.teams.map((team, i) => (
          <TeamColumn key={i} team={team} label={i === 0 ? "Blue side" : "Red side"} reg={reg} />
        ))}
      </div>
    </article>
  );
}

export default function MatchHistoryScreen(): JSX.Element {
  const api = useApi();
  const { data: games, loading, error, reload } = useAsyncData<HistoryGame[]>(
    () => fetchMatchHistory(api),
    [api],
  );
  const reg = useDdragon();

  return (
    <section aria-labelledby="history-heading">
      <ScreenHeader
        id="history-heading"
        title="Match history"
        intro="Per-game scoreboards from completed games. Reconstructed from client reports (display only) — the official result still comes from adjudication or an admin override."
        actions={
          <Button variant="secondary" onClick={reload} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      <ErrorBanner error={error} onRetry={reload} />

      {loading && games === null ? (
        <div data-history-list data-history-loading aria-hidden="true">
          {[0, 1].map((i) => (
            <article key={i} data-history-card>
              <Skeleton variant="line" width="40%" />
              <Skeleton variant="rect" height={168} />
            </article>
          ))}
        </div>
      ) : null}

      {games !== null ? (
        games.length === 0 ? (
          <EmptyState
            title="No games have been played yet"
            description="Scoreboards appear here once a custom match finishes and the players' clients report the result."
          />
        ) : (
          <div data-history-list>
            {games.map((g) => (
              <GameCard key={`${g.match_id}:${g.lcu_game_id}`} game={g} reg={reg} />
            ))}
          </div>
        )
      ) : null}
    </section>
  );
}
