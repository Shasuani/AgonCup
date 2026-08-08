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
  championIconUrl,
  fetchMatchHistory,
  kdaText,
  playerLabel,
  type HistoryGame,
  type HistoryPlayer,
  type HistoryTeam,
} from "./matchHistory";

import "./MatchHistoryScreen.css";

/** Hide a champion icon that fails to load (unknown id / offline CDN). */
function onIconError(e: React.SyntheticEvent<HTMLImageElement>): void {
  e.currentTarget.style.visibility = "hidden";
}

function PlayerRow({ player }: { player: HistoryPlayer }): JSX.Element {
  return (
    <li data-history-player>
      <img
        data-history-champ
        src={championIconUrl(player.champion_id)}
        alt=""
        width={24}
        height={24}
        loading="lazy"
        onError={onIconError}
      />
      <span data-history-name>{playerLabel(player)}</span>
      <span data-history-kda>{kdaText(player)}</span>
    </li>
  );
}

function TeamColumn({ team, label }: { team: HistoryTeam; label: string }): JSX.Element {
  return (
    <div data-history-team data-winner={team.is_winner ? "true" : "false"}>
      <div data-history-team-head>
        <span>{label}</span>
        {team.is_winner ? <span data-history-badge>Victory</span> : null}
      </div>
      <ul data-history-players>
        {team.players.map((p, i) => (
          <PlayerRow key={p.puuid || `${label}-${i}`} player={p} />
        ))}
      </ul>
    </div>
  );
}

function GameCard({ game }: { game: HistoryGame }): JSX.Element {
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
          <TeamColumn key={i} team={team} label={`Team ${i + 1}`} />
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
              <GameCard key={`${g.match_id}:${g.lcu_game_id}`} game={g} />
            ))}
          </div>
        )
      ) : null}
    </section>
  );
}
