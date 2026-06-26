// Admin player roster (PLAN.md §3.4).
//
// Lists everyone who has linked a Riot account: Discord display name, canonical
// Riot ID (game name + tag), region/server, and verification state. Linking
// itself happens out-of-band via the desktop client + Discord /register flow
// (server-side Account-V1 resolution), so this screen is read-only.
//
// Accounts routes are GLOBAL (no acting-guild header) — the client omits
// X-Guild-Id. All server-provided strings render as React children (escaped).

import { useCallback, useEffect, useState } from "react";
import { useApi } from "../../api/ApiProvider";
import { toError } from "../../api/useAsync";
import { ErrorBanner, Spinner } from "../../components";
import type { RosterEntryResponse } from "@lol/clients-ts";

export default function AccountsScreen(): JSX.Element {
  const api = useApi();
  const [rows, setRows] = useState<RosterEntryResponse[] | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const load = useCallback(async (): Promise<void> => {
    try {
      const data = await api.accounts.list();
      setRows(data);
      setError(null);
    } catch (err: unknown) {
      setError(toError(err));
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section aria-labelledby="accounts-heading">
      <h1 id="accounts-heading">Linked accounts</h1>
      <p>Players who have linked a Riot account via the desktop client.</p>

      <ErrorBanner error={error} />

      {rows === null && error === null ? (
        <p data-roster-loading>
          <Spinner label="Loading accounts" /> Loading…
        </p>
      ) : null}

      {rows !== null ? (
        rows.length === 0 ? (
          <p data-roster-empty>No accounts have been linked yet.</p>
        ) : (
          <table data-roster-table>
            <thead>
              <tr>
                <th scope="col">Discord</th>
                <th scope="col">Riot ID</th>
                <th scope="col">Region</th>
                <th scope="col">Joined from</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.account_id} data-roster-row>
                  <td>{r.discord_name}</td>
                  <td data-riot-id>
                    {r.game_name ?? "—"}
                    {r.tag_line ? <span data-riot-tag>#{r.tag_line}</span> : null}
                  </td>
                  <td>{r.region ? r.region.toUpperCase() : "—"}</td>
                  <td data-origin-guild>{r.origin_guild_name ?? "—"}</td>
                  <td>
                    {r.verified ? (
                      <span data-verified="true">✅ Verified</span>
                    ) : (
                      <span data-verified="false">⏳ Pending</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : null}
    </section>
  );
}
