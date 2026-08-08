// Authenticated landing page: an operations dashboard.
//
// Layout: (1) a KPI stat-card row backed by REAL API data, then (2) the existing
// nav cards into the primary sections.
//
// INTEGRITY (hard rule): KPI values come only from real endpoints. The two solid,
// typed sources are tournaments.list() (count + status/shared breakdown) and
// teams.list() (count, admin-gated). Disputes is best-effort via the escape-hatch
// request and tolerates 404. Any metric that errors/404s/has-no-source renders
// the neutral "—" placeholder — never an invented number.
//
// TEST CONSTRAINT: HomeScreen is unit-tested with ONLY a MemoryRouter (no
// ApiProvider). So all data fetching lives inside <DashboardKpis>, wrapped in a
// <ScreenBoundary> that swallows the throw when useApi() is absent — the
// "Dashboard" h1 and the "Tournaments" nav link always render regardless.

import {
  Component,
  type ErrorInfo,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import type { TournamentResponse } from "@lol/clients-ts";
import { useApi } from "../api/ApiProvider";
import { useAsyncData } from "../api/useAsync";
import { ScreenHeader } from "../components/ScreenHeader";
import { StatCard, StatCardGrid } from "../components/StatCard/StatCard";
import { Icon, type IconName } from "../components/Icon";
import { fetchDisputeQueue } from "./results/disputes";

import "./HomeScreen.css";

interface DashItem {
  to: string;
  label: string;
  description: string;
  icon: IconName;
}

const DASH_ITEMS: ReadonlyArray<DashItem> = [
  {
    to: "/servers",
    label: "Servers",
    description: "Add the bot to a Discord server you administer.",
    icon: "teams",
  },
  {
    to: "/tournaments",
    label: "Tournaments",
    description: "Create brackets, seed teams, and run custom matches.",
    icon: "tournaments",
  },
  {
    to: "/teams",
    label: "Teams",
    description: "Manage rosters and registrations across your guild.",
    icon: "teams",
  },
  {
    to: "/accounts",
    label: "Accounts",
    description: "Verify Riot IDs and review linked player identities.",
    icon: "accounts",
  },
  {
    to: "/disputes",
    label: "Dispute / Override queue",
    description: "Adjudicate contested results and apply admin overrides.",
    icon: "disputes",
  },
  {
    to: "/audit",
    label: "Audit log",
    description: "Trace privileged actions across the platform.",
    icon: "audit",
  },
];

/** Status strings that indicate a tournament is actively running. */
const ACTIVE_STATUSES = new Set(["running", "in_progress", "active", "live"]);

/**
 * Swallows render-time throws (e.g. useApi() outside ApiProvider in the bare
 * MemoryRouter test mount) so the surrounding heading + nav cards still render.
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
    // Intentionally swallowed — the dashboard heading + nav cards remain.
  }

  render(): ReactNode {
    if (this.state.failed) return null;
    return this.props.children;
  }
}

function DashboardKpis(): JSX.Element {
  const api = useApi();

  // Tournaments: total + active + shared-to-central, all from one list call.
  const tournaments = useAsyncData<TournamentResponse[]>(
    () => api.tournaments.list(),
    [api],
  );

  // Teams: admin-gated; on 403 useAsyncData surfaces an error -> value={null}.
  const teams = useAsyncData(() => api.teams.list(), [api]);

  // Open disputes: best-effort via the escape hatch; tolerate 404/non-2xx.
  const disputes = useAsyncData(() => fetchDisputeQueue(api), [api]);

  const tList = tournaments.data;
  const totalTournaments =
    tournaments.error || tList === null ? null : tList.length;
  const activeTournaments =
    tList === null
      ? null
      : tList.filter((t) => ACTIVE_STATUSES.has(t.status)).length;
  const sharedToCentral =
    tList === null
      ? null
      : tList.filter((t) => t.is_shared_to_central === true).length;

  const totalTeams = teams.error || teams.data === null ? null : teams.data.length;
  const openDisputes =
    disputes.error || disputes.data === null ? null : disputes.data.length;

  return (
    <StatCardGrid label="Key metrics">
      <StatCard
        label="Tournaments"
        icon="tournaments"
        loading={tournaments.loading}
        value={totalTournaments}
        sublabel={
          !tournaments.loading && activeTournaments !== null
            ? `${activeTournaments} active`
            : undefined
        }
        href="/tournaments"
      />
      <StatCard
        label="Teams"
        icon="teams"
        loading={teams.loading}
        value={totalTeams}
        unavailableNote={
          totalTeams === null && !teams.loading ? "Requires admin access" : undefined
        }
        href="/teams"
      />
      <StatCard
        label="Open disputes"
        icon="disputes"
        loading={disputes.loading}
        value={openDisputes}
        unavailableNote={
          openDisputes === null && !disputes.loading ? "Not available yet" : undefined
        }
        href="/disputes"
      />
      <StatCard
        label="Shared to central"
        icon="audit"
        loading={tournaments.loading}
        value={sharedToCentral}
      />
    </StatCardGrid>
  );
}

export default function HomeScreen(): JSX.Element {
  return (
    <section aria-labelledby="home-heading">
      <ScreenHeader
        id="home-heading"
        title="Dashboard"
        intro="Where your tournaments stand right now, and the way in to everything else."
      />

      <ScreenBoundary>
        <DashboardKpis />
      </ScreenBoundary>

      <h2 data-dashboard-heading>Jump to</h2>
      <ul data-dashboard>
        {DASH_ITEMS.map((item) => (
          <li key={item.to}>
            <Link to={item.to} data-dash-card aria-label={item.label}>
              <span data-dash-card-icon aria-hidden="true">
                <Icon name={item.icon} size={20} />
              </span>
              <h3>{item.label}</h3>
              <p>{item.description}</p>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
