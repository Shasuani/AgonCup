// Tournaments admin screen (PLAN.md §3.4): a LIST (name/status/format/teams),
// an admin-only CREATE form, and an inline DETAIL view (registrations +
// check-in + Start + bracket visualizer).
//
// Data access goes through useApi()/the lol-clients-ts client. The heading is
// rendered at the top level (no hooks) so the screen degrades gracefully if it
// is ever mounted outside the provider/router stack (see ScreenBoundary).

import { Component, useCallback, useState, type ErrorInfo, type ReactNode } from "react";
import type { TournamentResponse } from "@lol/clients-ts";
import {
  Button,
  DataTable,
  ErrorBanner,
  ScreenHeader,
  SectionHeader,
  StatusBadge,
  type Column,
} from "../../components";
import {
  FORMAT_LABELS_SHORT,
  GAME_MODE_LABELS_SHORT,
  TOURNAMENT_STATUS_LABELS,
  entrantsText,
  label as labelFor,
  seriesShort,
} from "../../lib/labels";
import { RoleGate } from "../../auth/RequireRole";
import { GUILD_ADMIN, PLATFORM_OPERATOR } from "../../auth/roles";
import { useApi } from "../../api/ApiProvider";
import { useAsyncData } from "../../api/useAsync";
import { CreateTournamentForm } from "./CreateTournamentForm";
import { TournamentDetail } from "./TournamentDetail";

const ADMIN_ROLES = [GUILD_ADMIN, PLATFORM_OPERATOR] as const;

/**
 * Isolates the data-bound portion of the screen. If the providers/router are
 * absent (e.g. a bare smoke-test mount), the hooks throw; this boundary swallows
 * the error so the surrounding heading still renders. In the real app the
 * providers are always present and this never trips.
 */
interface ScreenBoundaryProps {
  children: ReactNode;
  /**
   * Rendered instead of the children when the data-bound body throws. The
   * screen's <h1> now lives INSIDE the body (it is part of the ScreenHeader),
   * so the fallback must still supply a heading — the surrounding
   * `<section aria-labelledby>` would otherwise point at nothing.
   */
  fallback: ReactNode;
}

class ScreenBoundary extends Component<ScreenBoundaryProps, { failed: boolean }> {
  constructor(props: ScreenBoundaryProps) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Intentionally swallowed — the fallback heading remains visible.
  }

  render(): ReactNode {
    if (this.state.failed) return this.props.fallback;
    return this.props.children;
  }
}

function statusCell(t: TournamentResponse): ReactNode {
  return <StatusBadge status={t.status} labels={TOURNAMENT_STATUS_LABELS} />;
}

/** Short local date-time ("3 Sep, 18:00"), or the raw ISO if unparseable. */
function shortDateTime(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The scheduling columns are empty for most tournaments, so both times share
 *  one column: "opens … / starts …", or an em dash when unscheduled. */
function scheduleCell(t: TournamentResponse): ReactNode {
  if (!t.registration_opens_at && !t.start_at) {
    return <span data-muted>—</span>;
  }
  return (
    <span data-schedule>
      {t.registration_opens_at ? (
        <span data-schedule-row>
          <span data-schedule-key>Opens</span>
          {shortDateTime(t.registration_opens_at)}
        </span>
      ) : null}
      {t.start_at ? (
        <span data-schedule-row>
          <span data-schedule-key>Starts</span>
          {shortDateTime(t.start_at)}
        </span>
      ) : null}
    </span>
  );
}

function TournamentsBody(): JSX.Element {
  const api = useApi();
  const { data, loading, error, reload } = useAsyncData<TournamentResponse[]>(
    () => api.tournaments.list(),
    [],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // The create form used to sit permanently ABOVE the list, so every visit
  // opened on a wall of inputs instead of on the tournaments themselves.
  const [creating, setCreating] = useState(false);

  const handleCreated = useCallback(() => {
    reload();
  }, [reload]);

  const columns: ReadonlyArray<Column<TournamentResponse>> = [
    {
      id: "name",
      header: "Name",
      cell: (t) => (
        <button
          type="button"
          data-select-tournament
          data-active={selectedId === t.id ? "true" : undefined}
          aria-pressed={selectedId === t.id}
          onClick={() => setSelectedId(selectedId === t.id ? null : t.id)}
        >
          {t.name}
        </button>
      ),
    },
    { id: "status", header: "Status", cell: statusCell },
    {
      id: "game_mode",
      header: "Mode",
      cell: (t) => labelFor(GAME_MODE_LABELS_SHORT, t.game_mode),
    },
    { id: "format", header: "Format", cell: (t) => labelFor(FORMAT_LABELS_SHORT, t.format) },
    { id: "best_of", header: "Series", cell: (t) => seriesShort(t.best_of) },
    {
      id: "max_teams",
      header: "Size",
      cell: (t) => entrantsText(t.max_teams, t.game_mode),
    },
    { id: "schedule", header: "Schedule", cell: scheduleCell },
  ];

  const rows = data ?? [];

  return (
    <>
      <ScreenHeader
        id="tournaments-heading"
        title="Tournaments"
        intro="Brackets you run in this Discord server. Create one to mint a join code, then start it once entrants have locked in."
        actions={
          <>
            <Button variant="secondary" onClick={reload} disabled={loading}>
              {loading ? "Refreshing…" : "Refresh"}
            </Button>
            <RoleGate anyOf={ADMIN_ROLES}>
              <Button variant="primary" onClick={() => setCreating((v) => !v)}>
                {creating ? "Close form" : "New tournament"}
              </Button>
            </RoleGate>
          </>
        }
      />

      {creating ? (
        <RoleGate anyOf={ADMIN_ROLES}>
          <section aria-labelledby="create-tournament-heading" data-create-section>
            <SectionHeader
              id="create-tournament-heading"
              title="Create a tournament"
              intro="The join code is minted here and returned nowhere else — copy it before you leave."
            />
            <CreateTournamentForm onCreated={handleCreated} />
          </section>
        </RoleGate>
      ) : null}

      {error ? <ErrorBanner error={error} onRetry={reload} /> : null}

      <DataTable<TournamentResponse>
        caption="Tournaments"
        hideCaption
        columns={columns}
        rows={rows}
        rowKey={(t) => t.id}
        loading={loading}
        loadingRows={4}
        emptyMessage="No tournaments yet. Create one to mint a join code and open entries."
        emptyAction={
          creating ? null : (
            <RoleGate anyOf={ADMIN_ROLES}>
              <Button variant="primary" onClick={() => setCreating(true)}>
                New tournament
              </Button>
            </RoleGate>
          )
        }
      />

      {selectedId ? <TournamentDetail tournamentId={selectedId} onClose={() => setSelectedId(null)} /> : null}
    </>
  );
}

export default function TournamentsScreen(): JSX.Element {
  return (
    <section aria-labelledby="tournaments-heading">
      <ScreenBoundary
        fallback={<h1 id="tournaments-heading">Tournaments</h1>}
      >
        <TournamentsBody />
      </ScreenBoundary>
    </section>
  );
}
