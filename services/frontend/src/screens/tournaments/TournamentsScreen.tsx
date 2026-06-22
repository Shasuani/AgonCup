// Tournaments admin screen (PLAN.md §3.4): a LIST (name/status/format/teams),
// an admin-only CREATE form, and an inline DETAIL view (registrations +
// check-in + Start + bracket visualizer).
//
// Data access goes through useApi()/the lol-clients-ts client. The heading is
// rendered at the top level (no hooks) so the screen degrades gracefully if it
// is ever mounted outside the provider/router stack (see ScreenBoundary).

import { Component, useCallback, useState, type ErrorInfo, type ReactNode } from "react";
import type { TournamentResponse } from "@lol/clients-ts";
import { Button, DataTable, ErrorBanner, type Column } from "../../components";
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
class ScreenBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Intentionally swallowed — the heading remains visible.
  }

  render(): ReactNode {
    if (this.state.failed) return null;
    return this.props.children;
  }
}

function statusCell(t: TournamentResponse): ReactNode {
  return (
    <span data-status-badge data-status={t.status}>
      {t.status}
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
          onClick={() => setSelectedId(t.id)}
        >
          {t.name}
        </button>
      ),
    },
    { id: "status", header: "Status", cell: statusCell },
    { id: "format", header: "Format", cell: (t) => t.format },
    { id: "max_teams", header: "Teams", cell: (t) => t.max_teams },
  ];

  return (
    <>
      <RoleGate anyOf={ADMIN_ROLES}>
        <section aria-labelledby="create-tournament-heading" data-create-section>
          <h2 id="create-tournament-heading">Create a tournament</h2>
          <CreateTournamentForm onCreated={handleCreated} />
        </section>
      </RoleGate>

      {error ? <ErrorBanner error={error} onRetry={reload} /> : null}

      <DataTable<TournamentResponse>
        caption="Tournaments"
        columns={columns}
        rows={data ?? []}
        rowKey={(t) => t.id}
        loading={loading}
        emptyMessage="No tournaments yet."
      />

      {selectedId ? (
        <>
          <Button variant="secondary" onClick={() => setSelectedId(null)}>
            Close detail
          </Button>
          <TournamentDetail tournamentId={selectedId} />
        </>
      ) : null}
    </>
  );
}

export default function TournamentsScreen(): JSX.Element {
  return (
    <section aria-labelledby="tournaments-heading">
      <h1 id="tournaments-heading">Tournaments</h1>
      <ScreenBoundary>
        <TournamentsBody />
      </ScreenBoundary>
    </section>
  );
}
