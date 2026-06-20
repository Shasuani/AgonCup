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
  FormField,
  type Column,
} from "../../components";
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
          <span data-actor-name>{e.actor_name ?? e.actor_id}</span>
          <code data-actor-id>{e.actor_id}</code>
        </div>
      ),
    },
    {
      id: "action",
      header: "Action",
      cell: (e) => <span data-audit-action>{e.action}</span>,
    },
    {
      id: "entity",
      header: "Entity",
      cell: (e) => (
        <div data-audit-entity>
          <span data-entity-type>{e.entity_type}</span>
          {" "}
          <code data-entity-id>{e.entity_id}</code>
        </div>
      ),
    },
    {
      id: "guild",
      header: "Guild",
      cell: (e) => (e.guild_id ? <code data-audit-guild>{e.guild_id}</code> : <span data-empty>—</span>),
    },
    {
      id: "detail",
      header: "Detail",
      cell: (e) => (e.detail ? <span data-audit-detail>{e.detail}</span> : <span data-empty>—</span>),
    },
  ];

  return (
    <section aria-labelledby="audit-heading">
      <header data-screen-header>
        <h1 id="audit-heading">Audit log</h1>
        <Button onClick={reload} disabled={loading}>
          Refresh
        </Button>
      </header>

      <p data-screen-intro>
        Append-only record of administrative actions — including dispute-queue
        overrides. Filter by guild to scope the view.
      </p>

      <div data-audit-filter>
        <FormField label="Filter by guild">
          {({ inputId, describedBy }) => (
            <select
              id={inputId}
              aria-describedby={describedBy}
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
          )}
        </FormField>
      </div>

      <ErrorBanner error={error} onRetry={reload} />

      <DataTable<AuditEvent>
        caption="Audit events"
        columns={columns}
        rows={rows}
        rowKey={(e) => e.id}
        loading={loading}
        emptyMessage="No audit events match the current filter."
      />
    </section>
  );
}
