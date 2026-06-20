// Shared types + data access for the audit log viewer (PLAN.md §3.4 / §9).
//
// The audit log is an append-only stream of administrative/manual events: who
// (actor) did what (action) to which entity (entity_type + entity_id) and when
// (created_at). Admin overrides from the dispute queue land here, along with
// other privileged mutations. The viewer is read-only and filterable by guild.
//
// READ PATH: like the dispute queue, the typed client has no generated audit
// endpoint yet (that backend router ships later), so the read goes through the
// client's documented low-level `request` escape hatch at a stable path. When
// the backend ships the route + OpenAPI, this becomes a typed client method.
// All fields render as escaped React children — never raw HTML.

import type { ApiClient } from "@lol/clients-ts";

/** A single append-only audit event. */
export interface AuditEvent {
  id: string;
  /** Acting principal id (server-provided). */
  actor_id: string;
  /** Optional actor display name (server-provided; escaped). */
  actor_name?: string | null;
  /** The action performed, e.g. "result.override", "team.delete". */
  action: string;
  /** The kind of entity acted upon, e.g. "match", "team". */
  entity_type: string;
  /** The acted-upon entity's id. */
  entity_id: string;
  /** Guild the event occurred in (for cross-guild filtering). */
  guild_id?: string | null;
  /** Optional free-text reason/detail (server-provided; escaped). */
  detail?: string | null;
  /** ISO-8601 timestamp the event was recorded. */
  created_at: string;
}

/** Read path under the API base (escape-hatch GET; see file header). */
export const AUDIT_LOG_PATH = "/audit/events";

/**
 * Fetch audit events. Optionally narrowed to a single guild via the `guild_id`
 * query param. Goes through the client's low-level request runner so it carries
 * the standard Bearer auth header (the audit feed is operator-scoped).
 */
export function fetchAuditEvents(
  client: ApiClient,
  guildId?: string | null,
): Promise<AuditEvent[]> {
  const path =
    guildId && guildId.trim()
      ? `${AUDIT_LOG_PATH}?guild_id=${encodeURIComponent(guildId.trim())}`
      : AUDIT_LOG_PATH;
  return client.request<AuditEvent[]>({ method: "GET", path });
}

/** Best-effort, locale-stable formatting of an ISO timestamp for display. */
export function formatTimestamp(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toISOString().replace("T", " ").replace(".000Z", "Z");
}

/** Distinct, sorted guild ids present in the loaded events (for the filter). */
export function guildOptions(events: ReadonlyArray<AuditEvent>): string[] {
  const ids = new Set<string>();
  for (const e of events) {
    if (e.guild_id && e.guild_id.trim()) ids.add(e.guild_id);
  }
  return Array.from(ids).sort();
}
