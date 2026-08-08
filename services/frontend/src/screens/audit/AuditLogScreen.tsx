// Audit log viewer (PLAN.md §3.4 / §9).
//
// Append-only stream of administrative events: actor, action, entity, timestamp
// (+ optional reason/detail). Read-only, filterable by guild. The route is
// gated to platform_operator (App.tsx). Server strings render as escaped React
// children — no dangerouslySetInnerHTML.

import { useMemo, useState } from "react";
import { useApi } from "../../api/ApiProvider";
import { useAsyncData } from "../../api/useAsync";
import {
  Button,
  DataTable,
  ErrorBanner,
  IdChip,
  ScreenHeader,
  type Column,
} from "../../components";
import { humanize } from "../../lib/labels";

import "./AuditLogScreen.css";
import {
  fetchAuditEvents,
  formatTimestamp,
  guildOptions,
  type AuditEvent,
} from "./audit";

/** Sentinel for the "all guilds" filter option. */
const ALL_GUILDS = "__all__";

export default function AuditLogScreen(): JSX.Element {
  const api = useApi();

  const {
    data: events,
    loading,
    error,
    reload,
  } = useAsyncData<AuditEvent[]>(() => fetchAuditEvents(api), [api]);

  const [guildFilter, setGuildFilter] = useState<string>(ALL_GUILDS);

  const all = useMemo(() => events ?? [], [events]);
  const guilds = useMemo(() => guildOptions(all), [all]);

  const rows = useMemo(() => {
    if (guildFilter === ALL_GUILDS) return all;
    return all.filter((e) => e.guild_id === guildFilter);
  }, [all, guildFilter]);

  const columns: ReadonlyArray<Column<AuditEvent>> = [
    {
      id: "time",
      header: "Time",
      cell: (e) => <time dateTime={e.created_at}>{formatTimestamp(e.created_at)}</time>,
    },
    {
      id: "actor",
      header: "Actor",
      cell: (e) => (
        <div data-audit-actor>
          {e.actor_name ? <span data-actor-name>{e.actor_name}</span> : null}
          <IdChip value={e.actor_id} kind="actor id" />
        </div>
      ),
    },
    {
      id: "action",
      header: "Action",
      // Rendered VERBATIM on purpose. Actions are namespaced codes
      // (`result.override`) and this is the record you cite when tracing what
      // happened — prettifying it would make the log disagree with the API.
      // Styling carries the polish instead (see AuditLogScreen.css).
      cell: (e) => <span data-audit-action>{e.action}</span>,
    },
    {
      id: "entity",
      header: "Entity",
      cell: (e) => (
        <div data-audit-entity>
          <span data-entity-type>{humanize(e.entity_type)}</span>
          <IdChip value={e.entity_id} kind="entity id" />
        </div>
      ),
    },
    {
      id: "guild",
      header: "Guild",
      cell: (e) =>
        e.guild_id ? (
          <IdChip value={e.guild_id} kind="guild id" />
        ) : (
          <span data-empty>—</span>
        ),
    },
    {
      id: "detail",
      header: "Detail",
      cell: (e) => (e.detail ? <span data-audit-detail>{e.detail}</span> : <span data-empty>—</span>),
    },
  ];

  return (
    <section aria-labelledby="audit-heading">
      <ScreenHeader
        id="audit-heading"
        title="Audit log"
        intro="Append-only record of administrative actions — including dispute-queue overrides. Filter by guild to scope the view."
        actions={
          <Button variant="secondary" onClick={reload} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {/* A full-width stacked FormField for one select made the filter read as
          a form to fill in; it is a toolbar control. */}
      <div data-audit-filter>
        <label htmlFor="audit-guild-filter">Filter by guild</label>
        <select
          id="audit-guild-filter"
          value={guildFilter}
          onChange={(e) => setGuildFilter(e.target.value)}
          data-guild-filter
        >
          <option value={ALL_GUILDS}>All guilds</option>
          {guilds.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
        <p data-audit-count>
          {rows.length === all.length
            ? `${all.length} ${all.length === 1 ? "event" : "events"}`
            : `${rows.length} of ${all.length} events`}
        </p>
      </div>

      <ErrorBanner error={error} onRetry={reload} />

      <DataTable<AuditEvent>
        caption="Audit events"
        hideCaption
        columns={columns}
        rows={rows}
        rowKey={(e) => e.id}
        loading={loading}
        loadingRows={6}
        emptyMessage="No audit events match the current filter."
      />
    </section>
  );
}
