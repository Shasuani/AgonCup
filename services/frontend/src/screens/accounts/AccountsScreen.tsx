// Admin player roster (PLAN.md §3.4).
//
// Lists everyone who has linked a Riot account: Discord display name, canonical
// Riot ID (game name + tag), region/server, and verification state. Linking
// itself happens out-of-band via the desktop client + Discord /register flow
// (server-side Account-V1 resolution), so this screen is read-only.
//
// Accounts routes are GLOBAL (no acting-guild header) — the client omits
// X-Guild-Id. All server-provided strings render as React children (escaped).
//
// PRESENTATION: header + KPI row + a search/status toolbar over the roster
// table. Every number in the KPI row is COUNTED FROM THE LOADED ROWS — nothing
// is fetched twice and nothing is estimated; when the roster has not loaded the
// cards show their loading state rather than a placeholder figure. Search and
// the status filter are purely client-side over the already-fetched list (the
// roster endpoint takes no query parameters).

import { useCallback, useMemo, useState } from "react";
import { useApi } from "../../api/ApiProvider";
import { useAsyncData } from "../../api/useAsync";
import {
  Button,
  EmptyState,
  ErrorBanner,
  Icon,
  ScreenHeader,
  SkeletonTable,
  StatCard,
  StatCardGrid,
} from "../../components";
import { regionName } from "../../lib/labels";
import type { RosterEntryResponse } from "@lol/clients-ts";

import "./AccountsScreen.css";

/** Which rows the status segmented control lets through. */
type StatusFilter = "all" | "verified" | "pending";

const STATUS_FILTERS: ReadonlyArray<{ id: StatusFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "verified", label: "Verified" },
  { id: "pending", label: "Pending" },
];

/** Stable hue per player so the monogram chips are distinguishable but calm. */
function monogramHue(seed: string): number {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  }
  return hash;
}

/** First character of a display name, upper-cased; "?" when there is none. */
function monogram(name: string): string {
  const first = name.trim().slice(0, 1);
  return first === "" ? "?" : first.toUpperCase();
}

/** Render an ISO timestamp as a short local date, or null when unusable. */
function shortDate(iso: string | null): string | null {
  if (iso === null) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  return at.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * The fields the search box is allowed to match, as SEPARATE strings.
 *
 * Two things were wrong with the previous version. It joined every field into
 * one string, so a query could match ACROSS a boundary that does not exist —
 * "shasuani chama" hit because the haystack happened to read
 * "Shasuani Chama Shasu …", which is not a value anyone has. And it searched
 * the region and the bare tag, so typing "eun1" filtered the roster by
 * something nobody thinks of as a search term.
 *
 * Scope is now exactly two things: WHO the player is (their Discord name and
 * their Riot ID) and WHERE they joined from (the Discord server). Each is
 * matched on its own, so a hit always corresponds to one real value.
 */
function searchFields(r: RosterEntryResponse): string[] {
  const riotId =
    r.game_name !== null && r.tag_line !== null ? `${r.game_name}#${r.tag_line}` : null;
  return [r.discord_name, r.game_name, riotId, r.origin_guild_name]
    .filter((v): v is string => v !== null && v !== "")
    .map((v) => v.toLowerCase());
}

/** True when any single searchable field contains the needle. */
function matchesQuery(r: RosterEntryResponse, needle: string): boolean {
  return searchFields(r).some((field) => field.includes(needle));
}

export default function AccountsScreen(): JSX.Element {
  const api = useApi();
  const roster = useAsyncData<RosterEntryResponse[]>(
    useCallback(() => api.accounts.list(), [api]),
    [api],
  );

  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");

  const rows = roster.data;
  /** True only before the FIRST successful load (a refresh keeps the table up). */
  const firstLoad = roster.loading && rows === null;

  const verifiedCount = useMemo(
    () => (rows === null ? null : rows.filter((r) => r.verified).length),
    [rows],
  );
  const serverCount = useMemo(() => {
    if (rows === null) return null;
    const ids = new Set(
      rows.map((r) => r.origin_guild_id).filter((id): id is string => id !== null),
    );
    return ids.size;
  }, [rows]);

  const filtered = useMemo(() => {
    if (rows === null) return null;
    const needle = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (status === "verified" && !r.verified) return false;
      if (status === "pending" && r.verified) return false;
      if (needle === "") return true;
      return matchesQuery(r, needle);
    });
  }, [rows, query, status]);

  const clearFilters = useCallback(() => {
    setQuery("");
    setStatus("all");
  }, []);

  return (
    <section aria-labelledby="accounts-heading">
      <ScreenHeader
        id="accounts-heading"
        title="Linked accounts"
        intro={
          <>
            Every player who has linked a Riot account through the desktop
            client. Read-only — linking happens with a one-time code from{" "}
            <code>/register</code> in Discord.
          </>
        }
        actions={
          <Button variant="secondary" onClick={roster.reload} disabled={roster.loading}>
            {roster.loading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      <StatCardGrid label="Roster totals">
        <StatCard
          label="Linked players"
          icon="accounts"
          loading={firstLoad}
          value={rows === null ? null : rows.length}
          sublabel={
            serverCount === null
              ? undefined
              : `${serverCount} Discord ${serverCount === 1 ? "server" : "servers"}`
          }
          unavailableNote="Roster not loaded"
        />
        <StatCard
          label="Verified"
          icon="check"
          loading={firstLoad}
          value={verifiedCount}
          sublabel={
            rows === null || verifiedCount === null || rows.length === 0
              ? undefined
              : `${Math.round((verifiedCount / rows.length) * 100)}% of the roster`
          }
          unavailableNote="Roster not loaded"
        />
        <StatCard
          label="Pending"
          icon="alert"
          loading={firstLoad}
          value={rows === null || verifiedCount === null ? null : rows.length - verifiedCount}
          sublabel={rows === null ? undefined : "Awaiting the desktop check"}
          unavailableNote="Roster not loaded"
        />
      </StatCardGrid>

      <ErrorBanner error={roster.error} onRetry={roster.reload} />

      {firstLoad ? (
        <>
          <p data-roster-loading role="status">
            Loading accounts…
          </p>
          <SkeletonTable rows={6} columns={5} caption="Loading linked accounts" />
        </>
      ) : null}

      {rows !== null && rows.length > 0 ? (
        <div data-roster-toolbar data-toolbar>
          <div data-roster-search data-toolbar-search>
            <Icon name="search" size={16} />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by player name or Discord server…"
              aria-label="Search linked accounts by player name or Discord server"
            />
          </div>

          <div data-roster-filters data-segmented role="group" aria-label="Filter by verification status">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                data-roster-filter
                data-segmented-option
                data-active={status === f.id ? "true" : undefined}
                aria-pressed={status === f.id}
                onClick={() => setStatus(f.id)}
              >
                {f.label}
              </button>
            ))}
          </div>

          <p data-roster-count data-toolbar-count>
            {filtered !== null && filtered.length === rows.length
              ? `${rows.length} ${rows.length === 1 ? "player" : "players"}`
              : `${filtered?.length ?? 0} of ${rows.length} players`}
          </p>
        </div>
      ) : null}

      {rows !== null && rows.length === 0 ? (
        <EmptyState
          title="No accounts have been linked yet"
          description="Players run /register in Discord to get a one-time code, then enter it in the desktop client while the League client is open. Verified accounts show up here."
        />
      ) : null}

      {filtered !== null && rows !== null && rows.length > 0 && filtered.length === 0 ? (
        <EmptyState
          title="No players match those filters"
          description="Search matches a player's Discord name, their Riot ID, or the Discord server they joined from — nothing else."
          action={
            <Button variant="secondary" onClick={clearFilters}>
              Clear filters
            </Button>
          }
        />
      ) : null}

      {filtered !== null && filtered.length > 0 ? (
        <div data-roster-table-wrap>
          <table data-roster-table data-table>
            <thead>
              <tr>
                <th scope="col">Player</th>
                <th scope="col">Riot ID</th>
                <th scope="col">Region</th>
                <th scope="col">Joined from</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <RosterRow key={r.account_id} entry={r} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

/** One roster row: identity chip, Riot ID, region, origin server, status. */
function RosterRow({ entry: r }: { entry: RosterEntryResponse }): JSX.Element {
  const regionCode = r.region !== null ? r.region.toUpperCase() : null;
  const regionTitle = regionName(r.region);
  const verifiedOn = shortDate(r.verified_at);

  return (
    <tr data-roster-row>
      <td>
        <div data-roster-player>
          <span
            data-roster-monogram
            aria-hidden="true"
            style={{ "--monogram-hue": monogramHue(r.discord_name) } as React.CSSProperties}
          >
            {monogram(r.discord_name)}
          </span>
          <span data-roster-discord>{r.discord_name}</span>
        </div>
      </td>

      <td data-riot-id>
        {r.game_name !== null ? (
          <span data-riot-name>{r.game_name}</span>
        ) : (
          <span data-roster-none>—</span>
        )}
        {r.tag_line !== null ? <span data-riot-tag>#{r.tag_line}</span> : null}
      </td>

      <td>
        {regionCode !== null ? (
          <span data-roster-region title={regionTitle}>
            {regionCode}
          </span>
        ) : (
          <span data-roster-none>—</span>
        )}
      </td>

      <td data-origin-guild>
        {r.origin_guild_name !== null ? (
          r.origin_guild_name
        ) : (
          <span data-roster-none>—</span>
        )}
      </td>

      <td>
        <div data-roster-status>
          {r.verified ? (
            <span data-verified="true" data-status-badge data-status="verified">
              Verified
            </span>
          ) : (
            <span data-verified="false" data-status-badge data-status="pending">
              Pending
            </span>
          )}
          {r.verified && verifiedOn !== null ? (
            <span data-roster-when>{verifiedOn}</span>
          ) : null}
        </div>
      </td>
    </tr>
  );
}
