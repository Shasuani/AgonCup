// Tournaments admin screen (PLAN.md §3.4): a LIST (name/status/format/teams),
// an admin-only CREATE form, and an inline DETAIL view (registrations +
// check-in + Start + bracket visualizer).
//
// Data access goes through useApi()/the lol-clients-ts client. The heading is
// rendered at the top level (no hooks) so the screen degrades gracefully if it
// is ever mounted outside the provider/router stack (see ScreenBoundary).

import {
  Component,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ErrorInfo,
  type ReactNode,
} from "react";
import { useSearchParams } from "react-router-dom";
import type { TournamentResponse } from "@lol/clients-ts";
import {
  Button,
  DataTable,
  Drawer,
  ErrorBanner,
  Icon,
  ScreenHeader,
  SectionHeader,
  Segmented,
  StatusBadge,
  Toolbar,
  ToolbarCount,
  ToolbarSearch,
  countLabel,
  type Column,
} from "../../components";
import {
  FORMAT_LABELS_SHORT,
  GAME_MODE_LABELS_SHORT,
  TOURNAMENT_STATUS_LABELS,
  entrantsText,
  label as labelFor,
  regionName,
  seriesShort,
} from "../../lib/labels";
import { RoleGate } from "../../auth/RequireRole";
import { GUILD_ADMIN, PLATFORM_OPERATOR } from "../../auth/roles";
import { useApi } from "../../api/ApiProvider";
import { useAsyncData } from "../../api/useAsync";
import { CreateTournamentForm } from "./CreateTournamentForm";
import { TournamentDetail } from "./TournamentDetail";

const ADMIN_ROLES = [GUILD_ADMIN, PLATFORM_OPERATOR] as const;

/** The toolbar's status buckets. Raw enum values grouped by what an admin is
 *  looking for: what still needs entrants, what is being played, what is over. */
type StatusFilter = "all" | "open" | "running" | "finished";

const STATUS_FILTERS: ReadonlyArray<{ id: StatusFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "open", label: "Open" },
  { id: "running", label: "Running" },
  { id: "finished", label: "Finished" },
];

const OPEN_STATUSES = new Set(["draft", "open", "registration", "check_in", "scheduled"]);
const RUNNING_STATUSES = new Set(["running", "in_progress", "active", "live"]);
const FINISHED_STATUSES = new Set(["completed", "cancelled", "ended", "done"]);

function inStatusBucket(status: string, bucket: StatusFilter): boolean {
  if (bucket === "all") return true;
  if (bucket === "open") return OPEN_STATUSES.has(status);
  if (bucket === "running") return RUNNING_STATUSES.has(status);
  return FINISHED_STATUSES.has(status);
}

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
  // Deep links from the dashboard: `?open=<id>` opens that tournament's
  // drawer, `?create=1` opens the create form. Read once, then dropped from
  // the URL so Back does not re-open them.
  const [params, setParams] = useSearchParams();
  const openParam = params.get("open");
  const createParam = params.get("create");
  const [selectedId, setSelectedId] = useState<string | null>(openParam);
  const closeDetail = useCallback(() => setSelectedId(null), []);
  // The create form used to sit permanently ABOVE the list, so every visit
  // opened on a wall of inputs instead of on the tournaments themselves.
  const [creating, setCreating] = useState(createParam === "1");
  useEffect(() => {
    if (openParam === null && createParam === null) return;
    setParams({}, { replace: true });
  }, [openParam, createParam, setParams]);

  // Client-side search + status bucket over the loaded list (it is small and
  // fully loaded; the list route takes no query parameters).
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const clearFilters = useCallback(() => {
    setQuery("");
    setStatus("all");
  }, []);

  const handleCreated = useCallback(() => {
    reload();
  }, [reload]);

  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Confirmed before it happens: the row and everything under it go for good.
  // Past draft that includes other people's registrations, bracket and results,
  // so the warning says so instead of pretending it's a tidy-up.
  const handleDelete = useCallback(
    async (t: TournamentResponse) => {
      const message =
        t.status === "draft"
          ? `Delete the draft "${t.name}"? This cannot be undone.`
          : `Delete "${t.name}"? Its registrations, bracket and results are erased for everyone. This cannot be undone.`;
      if (!window.confirm(message)) return;
      setDeletingId(t.id);
      setDeleteError(null);
      try {
        await api.tournaments.remove(t.id);
        if (selectedId === t.id) setSelectedId(null);
        reload();
      } catch (err) {
        setDeleteError(err instanceof Error ? err.message : String(err));
      } finally {
        setDeletingId(null);
      }
    },
    [api, reload, selectedId],
  );

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
      id: "region",
      header: "Region",
      // Raw code as the visible text (matches the accounts roster), full server
      // name as the tooltip. "—" = created before regions / unrestricted.
      cell: (t) =>
        t.region ? <span title={regionName(t.region)}>{t.region.toUpperCase()}</span> : "—",
    },
    {
      id: "max_teams",
      header: "Size",
      cell: (t) => entrantsText(t.max_teams, t.game_mode),
    },
    { id: "schedule", header: "Schedule", cell: scheduleCell },
    {
      id: "actions",
      header: <span data-visually-hidden>Actions</span>,
      // A quiet icon button: a bordered "Delete" on every row turned the list
      // into a wall of buttons. It goes red only under the pointer.
      cell: (t) => (
        <RoleGate anyOf={ADMIN_ROLES}>
          <Button
            variant="ghost"
            size="sm"
            data-delete-tournament
            data-icon-only
            data-tone="danger"
            aria-label={`Delete ${t.name}`}
            title="Delete"
            busy={deletingId === t.id}
            onClick={() => {
              void handleDelete(t);
            }}
          >
            <Icon name="trash" size={16} />
          </Button>
        </RoleGate>
      ),
    },
  ];

  const rows = data ?? [];
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter(
      (t) => inStatusBucket(t.status, status) && (needle === "" || t.name.toLowerCase().includes(needle)),
    );
  }, [rows, query, status]);
  const filtering = filtered.length !== rows.length;

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
      {deleteError ? <ErrorBanner error={deleteError} /> : null}

      {rows.length > 0 ? (
        <Toolbar>
          <ToolbarSearch
            label="Search tournaments by name"
            placeholder="Search tournaments…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Segmented
            label="Filter by status"
            options={STATUS_FILTERS}
            value={status}
            onChange={setStatus}
          />
          <ToolbarCount>{countLabel(filtered.length, rows.length, "tournament")}</ToolbarCount>
        </Toolbar>
      ) : null}

      <DataTable<TournamentResponse>
        caption="Tournaments"
        hideCaption
        columns={columns}
        rows={filtered}
        rowKey={(t) => t.id}
        loading={loading}
        loadingRows={4}
        emptyMessage={
          filtering
            ? "No tournaments match those filters."
            : "No tournaments yet. Create one to mint a join code and open entries."
        }
        emptyAction={
          filtering ? (
            <Button variant="secondary" onClick={clearFilters}>
              Clear filters
            </Button>
          ) : creating ? null : (
            <RoleGate anyOf={ADMIN_ROLES}>
              <Button variant="primary" onClick={() => setCreating(true)}>
                New tournament
              </Button>
            </RoleGate>
          )
        }
      />

      {/* The detail arrives beside the row that opened it, instead of below a
          table that may be twenty rows tall. */}
      <Drawer open={selectedId !== null} onClose={closeDetail} labelledBy="tournament-detail-heading">
        {selectedId ? <TournamentDetail tournamentId={selectedId} onClose={closeDetail} /> : null}
      </Drawer>
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
