// Authenticated landing page: an operations dashboard.
//
// Layout: (1) KPI stat-cards, (2) "Needs attention" + "Live & upcoming"
// (tournaments with their lock-in progress), (3) recent results + recent
// activity feeds, (4) the desktop-client downloads. A row of plain links to the
// sections stays at the top so the dashboard is still the way in.
//
// INTEGRITY (hard rule): every figure comes from a real endpoint — tournaments,
// teams, accounts, the dispute queue, match history and the audit feed. A
// source that errors (403 for a guild admin on the audit feed, 404 on a route
// that has not shipped) renders "—" or hides its panel; nothing is invented.
//
// TEST CONSTRAINT: HomeScreen is unit-tested with ONLY a MemoryRouter (no
// ApiProvider). All data fetching lives inside <DashboardBody>, wrapped in a
// <ScreenBoundary> that swallows the throw when useApi() is absent — the
// "Dashboard" h1 and the "Tournaments" link always render regardless.

import {
  Component,
  useMemo,
  type ErrorInfo,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import type { EntrantResponse, TournamentResponse } from "@lol/clients-ts";
import { useApi } from "../api/ApiProvider";
import { useAsyncData } from "../api/useAsync";
import { ScreenHeader } from "../components/ScreenHeader";
import { StatCard, StatCardGrid } from "../components/StatCard/StatCard";
import { StatusBadge } from "../components/StatusBadge";
import { Icon, type IconName } from "../components/Icon";
import { DownloadClient } from "../components/DownloadClient";
import { fetchDisputeQueue } from "./results/disputes";
import { fetchMatchHistory, playerLabel, type HistoryGame } from "./results/matchHistory";
import { fetchAuditEvents, type AuditEvent } from "./audit/audit";
import {
  GAME_MODE_LABELS_SHORT,
  TOURNAMENT_STATUS_LABELS,
  entrantsText,
  humanize,
  label as labelFor,
} from "../lib/labels";
import { shortDateTime, timeAgo, timeUntil } from "../lib/time";

import "./HomeScreen.css";

interface DashItem {
  to: string;
  label: string;
  icon: IconName;
}

/** The way in to every section — plain links, so the row costs no vertical
 *  space and duplicates nothing the sidebar already says loudly. */
const DASH_ITEMS: ReadonlyArray<DashItem> = [
  { to: "/tournaments", label: "Tournaments", icon: "tournaments" },
  { to: "/teams", label: "Teams", icon: "teams" },
  { to: "/accounts", label: "Accounts", icon: "accounts" },
  { to: "/history", label: "Match history", icon: "history" },
  { to: "/disputes", label: "Disputes", icon: "disputes" },
  { to: "/servers", label: "Servers", icon: "server" },
];

/** Status strings that indicate a tournament is actively running. */
const ACTIVE_STATUSES = new Set(["running", "in_progress", "active", "live"]);
const FINISHED_STATUSES = new Set(["completed", "cancelled", "ended", "done"]);
const ENTRY_STATUSES = new Set(["open", "check_in", "registration", "scheduled", "draft"]);

/**
 * Swallows render-time throws (e.g. useApi() outside ApiProvider in the bare
 * MemoryRouter test mount) so the surrounding heading + links still render.
 */
class ScreenBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Intentionally swallowed — the dashboard heading + links remain.
  }

  render(): ReactNode {
    if (this.state.failed) return null;
    return this.props.children;
  }
}

type EntrantsById = ReadonlyMap<string, EntrantResponse[] | null>;

interface Attention {
  key: string;
  tone: "danger" | "warning" | "success" | "info";
  icon: IconName;
  title: string;
  detail: string;
  to: string;
}

function lockedCount(entrants: EntrantResponse[] | null | undefined): number {
  return (entrants ?? []).filter((e) => e.locked_in).length;
}

/** What an operator should look at first, derived from the loaded state. */
function attentionItems(
  tournaments: TournamentResponse[],
  entrants: EntrantsById,
  disputes: number | null,
  now: number,
): Attention[] {
  const out: Attention[] = [];
  if (disputes !== null && disputes > 0) {
    out.push({
      key: "disputes",
      tone: "danger",
      icon: "disputes",
      title: `${disputes} ${disputes === 1 ? "result needs" : "results need"} a decision`,
      detail: "Reported outcomes failed corroboration. Review them and record a winner.",
      to: "/disputes",
    });
  }
  for (const t of tournaments) {
    if (!ENTRY_STATUSES.has(t.status)) continue;
    const list = entrants.get(t.id);
    if (list === undefined) continue;
    const locked = lockedCount(list);
    const total = list?.length ?? 0;
    const startsIn = t.start_at ? timeUntil(t.start_at, now) : null;
    if (total > 0 && locked === total && t.status === "check_in") {
      out.push({
        key: `ready:${t.id}`,
        tone: "success",
        icon: "check",
        title: `${t.name} is ready to start`,
        detail: `${entrantsText(total, t.game_mode)} locked in — press Start to draw the bracket.`,
        to: `/tournaments?open=${encodeURIComponent(t.id)}`,
      });
    } else if (startsIn !== null && Date.parse(t.start_at ?? "") - now < 24 * 3600e3) {
      out.push({
        key: `soon:${t.id}`,
        tone: "warning",
        icon: "history",
        title: `${t.name} starts ${startsIn}`,
        detail:
          total === 0
            ? "Nobody has entered yet — share the join code."
            : `${locked}/${total} locked in so far.`,
        to: `/tournaments?open=${encodeURIComponent(t.id)}`,
      });
    }
  }
  return out;
}

function DashboardBody(): JSX.Element {
  const api = useApi();
  const now = Date.now();

  const tournaments = useAsyncData<TournamentResponse[]>(() => api.tournaments.list(), [api]);
  const teams = useAsyncData(() => api.teams.list(), [api]);
  const players = useAsyncData(() => api.accounts.list(), [api]);
  const disputes = useAsyncData(() => fetchDisputeQueue(api), [api]);
  const history = useAsyncData(() => fetchMatchHistory(api), [api]);
  const audit = useAsyncData(() => fetchAuditEvents(api), [api]);

  const tList = tournaments.data ?? [];
  const active = useMemo(
    () =>
      tList
        .filter((t) => !FINISHED_STATUSES.has(t.status))
        .sort((a, b) => {
          const ar = ACTIVE_STATUSES.has(a.status) ? 0 : 1;
          const br = ACTIVE_STATUSES.has(b.status) ? 0 : 1;
          if (ar !== br) return ar - br;
          const at = a.start_at ? Date.parse(a.start_at) : Number.MAX_SAFE_INTEGER;
          const bt = b.start_at ? Date.parse(b.start_at) : Number.MAX_SAFE_INTEGER;
          return at - bt;
        }),
    [tList],
  );
  const watched = active.slice(0, 6);
  const watchedKey = watched.map((t) => t.id).join(",");

  // Lock-in progress for the tournaments on screen (bounded to six, so at most
  // six extra reads). A failed read leaves that row without a bar, not blank.
  const entrants = useAsyncData<EntrantsById>(async () => {
    const pairs = await Promise.all(
      watched.map(async (t): Promise<[string, EntrantResponse[] | null]> => {
        try {
          return [t.id, await api.tournaments.entrants(t.id)];
        } catch {
          return [t.id, null];
        }
      }),
    );
    return new Map(pairs);
  }, [api, watchedKey]);
  const entrantsById: EntrantsById = entrants.data ?? new Map();

  const totalTournaments = tournaments.error || tList.length === 0 && tournaments.data === null ? null : tList.length;
  const activeTournaments = tournaments.data === null ? null : tList.filter((t) => ACTIVE_STATUSES.has(t.status)).length;
  const totalTeams = teams.error || teams.data === null ? null : teams.data.length;
  const totalPlayers = players.error || players.data === null ? null : players.data.length;
  const verifiedPlayers = players.data === null ? null : players.data.filter((p) => p.verified).length;
  const openDisputes = disputes.error || disputes.data === null ? null : disputes.data.length;

  const attention = attentionItems(watched, entrantsById, openDisputes, now);
  const recentGames = (history.data ?? []).slice(0, 5);
  const recentEvents = (audit.data ?? []).slice(0, 6);
  const auditAvailable = !audit.error;

  return (
    <>
      <StatCardGrid label="Key metrics">
        <StatCard
          label="Tournaments"
          icon="tournaments"
          loading={tournaments.loading}
          value={totalTournaments}
          sublabel={
            !tournaments.loading && activeTournaments !== null
              ? `${activeTournaments} running`
              : undefined
          }
          href="/tournaments"
        />
        <StatCard
          label="Teams"
          icon="teams"
          loading={teams.loading}
          value={totalTeams}
          unavailableNote={totalTeams === null && !teams.loading ? "Requires admin access" : undefined}
          href="/teams"
        />
        <StatCard
          label="Linked players"
          icon="accounts"
          loading={players.loading}
          value={totalPlayers}
          sublabel={
            !players.loading && verifiedPlayers !== null ? `${verifiedPlayers} verified` : undefined
          }
          unavailableNote={totalPlayers === null && !players.loading ? "Requires admin access" : undefined}
          href="/accounts"
        />
        <StatCard
          label="Open disputes"
          icon="disputes"
          loading={disputes.loading}
          value={openDisputes}
          unavailableNote={openDisputes === null && !disputes.loading ? "Not available yet" : undefined}
          href="/disputes"
        />
      </StatCardGrid>

      <div data-dash-grid>
        {/* Needs attention */}
        <section aria-labelledby="attention-heading" data-panel data-dash-panel data-attention>
          <div data-dash-panel-head>
            <h2 id="attention-heading">Needs attention</h2>
            {tournaments.loading || entrants.loading ? <span data-muted>Checking…</span> : null}
          </div>
          {attention.length === 0 ? (
            <div data-attention-clear>
              <span data-attention-clear-icon aria-hidden="true">
                <Icon name="check" size={20} />
              </span>
              <div>
                <strong>All clear</strong>
                <p>No disputes waiting, nothing about to start without its entrants.</p>
              </div>
            </div>
          ) : (
            <ul data-attention-list>
              {attention.map((a) => (
                <li key={a.key}>
                  <Link to={a.to} data-attention-item data-tone={a.tone}>
                    <span data-attention-icon aria-hidden="true">
                      <Icon name={a.icon} size={16} />
                    </span>
                    <span data-attention-body>
                      <strong>{a.title}</strong>
                      <span>{a.detail}</span>
                    </span>
                    <Icon name="chevron-right" size={16} aria-hidden="true" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Live & upcoming */}
        <section aria-labelledby="live-heading" data-panel data-dash-panel data-live>
          <div data-dash-panel-head>
            <h2 id="live-heading">Live &amp; upcoming</h2>
            <Link to="/tournaments" data-dash-more>
              All tournaments <Icon name="arrow-right" size={14} aria-hidden="true" />
            </Link>
          </div>
          {tournaments.loading && tournaments.data === null ? (
            <p data-muted>Loading tournaments…</p>
          ) : watched.length === 0 ? (
            <div data-dash-empty>
              <p>Nothing scheduled. Create a tournament to mint a join code and open entries.</p>
              <Link to="/tournaments?create=1" data-variant="primary" data-size="sm">
                <Icon name="plus" size={16} aria-hidden="true" />
                New tournament
              </Link>
            </div>
          ) : (
            <ul data-live-list>
              {watched.map((t) => {
                const list = entrantsById.get(t.id);
                const locked = lockedCount(list);
                const pct = t.max_teams > 0 ? Math.min(100, Math.round((locked / t.max_teams) * 100)) : 0;
                const running = ACTIVE_STATUSES.has(t.status);
                return (
                  <li key={t.id} data-live-row data-running={running || undefined}>
                    <div data-live-main>
                      <div data-live-title>
                        <Link to={`/tournaments?open=${encodeURIComponent(t.id)}`} data-live-name>
                          {t.name}
                        </Link>
                        <StatusBadge status={t.status} labels={TOURNAMENT_STATUS_LABELS} />
                      </div>
                      <div data-live-meta>
                        <span>{labelFor(GAME_MODE_LABELS_SHORT, t.game_mode)}</span>
                        <span>·</span>
                        <span>{entrantsText(t.max_teams, t.game_mode)}</span>
                        {t.start_at ? (
                          <>
                            <span>·</span>
                            <span>{timeUntil(t.start_at, now) ?? shortDateTime(t.start_at)}</span>
                          </>
                        ) : null}
                      </div>
                      {list !== undefined ? (
                        <div data-live-progress-row>
                          <div data-progress data-tone={running ? "success" : undefined} aria-hidden="true">
                            <span style={{ width: `${pct}%` }} />
                          </div>
                          <span data-live-progress-label>
                            {list === null ? "—" : `${locked}/${t.max_teams} locked in`}
                          </span>
                        </div>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Recent results */}
        <section aria-labelledby="results-heading" data-panel data-dash-panel data-results data-span={auditAvailable ? undefined : "2"}>
          <div data-dash-panel-head>
            <h2 id="results-heading">Recent results</h2>
            <Link to="/history" data-dash-more>
              Match history <Icon name="arrow-right" size={14} aria-hidden="true" />
            </Link>
          </div>
          {history.loading && history.data === null ? (
            <p data-muted>Loading results…</p>
          ) : recentGames.length === 0 ? (
            <p data-muted>No games have been played yet. Scoreboards appear here as matches finish.</p>
          ) : (
            <ul data-results-list>
              {recentGames.map((g) => (
                <ResultRow key={`${g.match_id}:${g.lcu_game_id}`} game={g} now={now} />
              ))}
            </ul>
          )}
        </section>

        {/* Recent activity (operator feed; hidden when the caller cannot read it) */}
        {auditAvailable ? (
          <section aria-labelledby="activity-heading" data-panel data-dash-panel data-activity>
            <div data-dash-panel-head>
              <h2 id="activity-heading">Recent activity</h2>
              <Link to="/audit" data-dash-more>
                Audit log <Icon name="arrow-right" size={14} aria-hidden="true" />
              </Link>
            </div>
            {audit.loading && audit.data === null ? (
              <p data-muted>Loading activity…</p>
            ) : recentEvents.length === 0 ? (
              <p data-muted>No administrative actions recorded yet.</p>
            ) : (
              <ul data-activity-list>
                {recentEvents.map((e) => (
                  <ActivityRow key={e.id} event={e} now={now} />
                ))}
              </ul>
            )}
          </section>
        ) : null}
      </div>
    </>
  );
}

function ResultRow({ game, now }: { game: HistoryGame; now: number }): JSX.Element {
  const winner = game.teams.find((t) => t.is_winner);
  const loser = game.teams.find((t) => !t.is_winner);
  const names = (players: HistoryGame["teams"][number]["players"] | undefined): string => {
    if (!players || players.length === 0) return "—";
    const shown = players.slice(0, 2).map(playerLabel).join(", ");
    return players.length > 2 ? `${shown} +${players.length - 2}` : shown;
  };
  return (
    <li data-result-row>
      <span data-result-outcome aria-hidden="true">
        <Icon name="tournaments" size={16} />
      </span>
      <div data-result-body>
        <div data-result-title>
          <strong>{names(winner?.players)}</strong>
          <span data-result-vs>beat</span>
          <span>{names(loser?.players)}</span>
        </div>
        <div data-result-meta>
          {game.tournament_name ?? "Match"}
          {game.round != null ? ` · Round ${game.round}` : ""}
          {` · ${timeAgo(game.played_at, now)}`}
        </div>
      </div>
    </li>
  );
}

function ActivityRow({ event, now }: { event: AuditEvent; now: number }): JSX.Element {
  return (
    <li data-activity-row>
      <span data-audit-action>{event.action}</span>
      <div data-activity-body>
        <span data-activity-who>{event.actor_name ?? "System"}</span>
        <span data-activity-what>
          {humanize(event.entity_type)}
          {event.detail ? ` — ${event.detail}` : ""}
        </span>
      </div>
      <time dateTime={event.created_at} data-activity-when>
        {timeAgo(event.created_at, now)}
      </time>
    </li>
  );
}

export default function HomeScreen(): JSX.Element {
  return (
    <section aria-labelledby="home-heading">
      <ScreenHeader
        id="home-heading"
        title="Dashboard"
        intro="Where your tournaments stand right now, and what needs you next."
        actions={
          <Link to="/tournaments?create=1" data-variant="primary">
            <Icon name="plus" size={16} aria-hidden="true" />
            New tournament
          </Link>
        }
      />

      <nav aria-label="Sections" data-dash-links>
        {DASH_ITEMS.map((item) => (
          <Link key={item.to} to={item.to} data-dash-link>
            <Icon name={item.icon} size={15} aria-hidden="true" />
            {item.label}
          </Link>
        ))}
      </nav>

      <ScreenBoundary>
        <DashboardBody />
      </ScreenBoundary>

      {/* Admins hand the installer link to their players; keep it one click
          from the dashboard rather than only on the sign-in page. */}
      <div data-dashboard-downloads>
        <DownloadClient />
      </div>
    </section>
  );
}
