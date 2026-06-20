// Onboarded guilds management (PLAN.md §3.4, §9 lifecycle).
//
// platform_operator-only. The backend does not (yet) expose a guild-list
// endpoint in lol-clients-ts, so the set of "onboarded guilds" is sourced from
// the acting-guild context plus any guild ids the operator adds locally for the
// session. Each guild has a settings view (read-only summary of what we know)
// and a destructive PURGE action (lifecycle.purgeGuild) behind a ConfirmDialog.
//
// The whole screen is wrapped in <RoleGate anyOf={[PLATFORM_OPERATOR]}> so the
// purge affordance only renders for operators (the backend still enforces it).
// All server-provided strings render as React children (escaped) — no raw HTML.

import { useCallback, useMemo, useState, type FormEvent } from "react";
import { useApi } from "../../api/ApiProvider";
import { useActingGuild } from "../../auth/ActingGuildProvider";
import { useAuth } from "../../auth/AuthProvider";
import { RoleGate } from "../../auth/RequireRole";
import { PLATFORM_OPERATOR } from "../../auth/roles";
import { useAsyncAction } from "../../api/useAsync";
import {
  Button,
  ConfirmDialog,
  DataTable,
  ErrorBanner,
  TextInput,
  type Column,
} from "../../components";
import type { PurgeResponse } from "@lol/clients-ts";

interface GuildRow {
  guildId: string;
  /** Where we learned about this guild (for the settings summary). */
  source: string;
}

/** Default component is exported for the shell to wire a `/guilds` route. */
export default function GuildsScreen(): JSX.Element {
  return (
    <RoleGate
      anyOf={[PLATFORM_OPERATOR]}
      fallback={
        <section aria-labelledby="guilds-heading">
          <h1 id="guilds-heading">Guilds</h1>
          <p role="alert">
            Guild administration is restricted to platform operators.
          </p>
        </section>
      }
    >
      <GuildsAdmin />
    </RoleGate>
  );
}

function GuildsAdmin(): JSX.Element {
  const { actingGuildId } = useActingGuild();
  const { claims } = useAuth();

  // Seed the onboarded-guild list from context we already hold (no network IO).
  const seededRows = useMemo<GuildRow[]>(() => {
    const rows: GuildRow[] = [];
    const seen = new Set<string>();
    if (actingGuildId) {
      rows.push({ guildId: actingGuildId, source: "acting guild" });
      seen.add(actingGuildId);
    }
    const tokenGuild = typeof claims?.guild_id === "string" ? claims.guild_id : null;
    if (tokenGuild && !seen.has(tokenGuild)) {
      rows.push({ guildId: tokenGuild, source: "token claim" });
      seen.add(tokenGuild);
    }
    return rows;
  }, [actingGuildId, claims?.guild_id]);

  const [extraRows, setExtraRows] = useState<GuildRow[]>([]);
  const [newGuildId, setNewGuildId] = useState("");
  const [selected, setSelected] = useState<GuildRow | null>(null);

  const rows = useMemo<GuildRow[]>(() => {
    const merged: GuildRow[] = [...seededRows];
    const seen = new Set(merged.map((r) => r.guildId));
    for (const r of extraRows) {
      if (!seen.has(r.guildId)) {
        merged.push(r);
        seen.add(r.guildId);
      }
    }
    return merged;
  }, [seededRows, extraRows]);

  const onAddGuild = useCallback(
    (e: FormEvent) => {
      e.preventDefault();
      const id = newGuildId.trim();
      if (id.length === 0) return;
      setExtraRows((prev) =>
        prev.some((r) => r.guildId === id)
          ? prev
          : [...prev, { guildId: id, source: "added manually" }],
      );
      setNewGuildId("");
    },
    [newGuildId],
  );

  const columns: ReadonlyArray<Column<GuildRow>> = [
    { id: "guild", header: "Guild ID", cell: (r) => r.guildId },
    { id: "source", header: "Onboarded via", cell: (r) => r.source },
    {
      id: "actions",
      header: "Actions",
      cell: (r) => (
        <Button
          onClick={() => setSelected(r)}
          aria-label={`Settings for ${r.guildId}`}
        >
          Settings
        </Button>
      ),
    },
  ];

  return (
    <section aria-labelledby="guilds-heading">
      <h1 id="guilds-heading">Guilds</h1>
      <p>Onboarded guilds. Select a guild to view its settings or purge it.</p>

      <DataTable
        caption="Onboarded guilds"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.guildId}
        emptyMessage="No guilds known in this session. Add one by ID below."
      />

      <form onSubmit={onAddGuild} aria-label="Add guild by ID">
        <TextInput
          label="Guild ID"
          hint="Track another onboarded guild for this session."
          value={newGuildId}
          onChange={(e) => setNewGuildId(e.target.value)}
        />
        <Button
          variant="secondary"
          type="submit"
          disabled={newGuildId.trim().length === 0}
        >
          Add guild
        </Button>
      </form>

      {selected ? (
        <GuildSettings
          row={selected}
          onClose={() => setSelected(null)}
          onPurged={(purgedId) => {
            setSelected(null);
            setExtraRows((prev) => prev.filter((r) => r.guildId !== purgedId));
          }}
        />
      ) : null}
    </section>
  );
}

interface GuildSettingsProps {
  row: GuildRow;
  onClose: () => void;
  onPurged: (guildId: string) => void;
}

function GuildSettings({ row, onClose, onPurged }: GuildSettingsProps): JSX.Element {
  const api = useApi();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const purge = useAsyncAction(
    useCallback(
      (guildId: string): Promise<PurgeResponse> =>
        api.lifecycle.purgeGuild(guildId),
      [api],
    ),
  );

  const result = purge.data;

  return (
    <section aria-labelledby="guild-settings-heading" data-guild-settings>
      <header>
        <h2 id="guild-settings-heading">Settings — {row.guildId}</h2>
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      </header>

      <dl>
        <dt>Guild ID</dt>
        <dd>{row.guildId}</dd>
        <dt>Onboarded via</dt>
        <dd>{row.source}</dd>
      </dl>

      <h3>Danger zone</h3>
      <p>
        Purging permanently deletes all data for this guild. This cannot be
        undone.
      </p>
      <ErrorBanner error={purge.error} />
      <Button
        variant="danger"
        onClick={() => setConfirmOpen(true)}
        disabled={purge.loading}
      >
        Purge guild
      </Button>

      {result ? (
        <p role="status" data-purge-result>
          Purge {result.status}: {result.total_deleted} rows removed.
        </p>
      ) : null}

      <ConfirmDialog
        open={confirmOpen}
        title="Purge guild"
        message={`Permanently delete all data for guild ${row.guildId}? This cannot be undone.`}
        confirmLabel="Purge"
        destructive
        busy={purge.loading}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={async () => {
          await purge.run(row.guildId);
          setConfirmOpen(false);
          onPurged(row.guildId);
        }}
      />
    </section>
  );
}
