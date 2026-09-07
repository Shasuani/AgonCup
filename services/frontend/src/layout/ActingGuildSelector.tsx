// Acting-guild selector (drives the X-Guild-Id header via ActingGuildProvider).
//
// The topbar used to show the raw guild UUID in a text box. The server's NAME is
// what an admin recognises, so this asks the backend which Discord servers the
// operator administers (the same `/auth/guilds` the Servers screen uses) and,
// when the bot is installed in at least one, offers them by name in a select.
//
// The lookup is best-effort and must never break the shell: an account that
// signed in before the `guilds` scope existed gets a 409, a dev-login session
// has no Discord token at all, and tests inject clients with no `auth` surface.
// Every one of those falls back to the editable id pill, which is also what
// shows when the current id is not one of the named guilds (a platform
// operator working on a guild they are not a member of).

import { useMemo } from "react";
import type { AdminGuild } from "@lol/clients-ts";
import { useApi } from "../api/ApiProvider";
import { useAsyncData } from "../api/useAsync";
import { useActingGuild } from "../auth/ActingGuildProvider";
import { FormField } from "../components/FormField";
import { Icon } from "../components/Icon";

/** Sentinel `<option>` for "the current id is not one of the named guilds". */
const OTHER = "__other__";

interface NamedGuild {
  id: string;
  name: string;
}

/** Guilds the operator can act as: installed AND carrying our internal id. */
function namedGuilds(list: ReadonlyArray<AdminGuild> | null): NamedGuild[] {
  if (list === null) return [];
  return list
    .filter((g) => g.installed && typeof g.guild_id === "string" && g.guild_id.length > 0)
    .map((g) => ({ id: g.guild_id as string, name: g.name }));
}

/** First 8 characters of an id — enough to tell two guilds apart in a pill. */
function shortId(id: string): string {
  return id.length > 10 ? `${id.slice(0, 8)}…` : id;
}

export function ActingGuildSelector(): JSX.Element {
  const api = useApi();
  const { actingGuildId, setActingGuild } = useActingGuild();

  const guilds = useAsyncData<AdminGuild[]>(
    () => {
      const fetchGuilds = api.auth?.adminGuilds;
      if (typeof fetchGuilds !== "function") return Promise.resolve([]);
      return fetchGuilds.call(api.auth).catch(() => []);
    },
    [api],
  );

  const named = useMemo(() => namedGuilds(guilds.data), [guilds.data]);
  const current = actingGuildId !== null ? named.find((g) => g.id === actingGuildId) : undefined;

  if (named.length > 0) {
    const value = current ? current.id : actingGuildId === null ? "" : OTHER;
    return (
      <div data-acting-guild data-acting-guild-named title={current ? current.name : actingGuildId ?? undefined}>
        <Icon name="guild" size={15} />
        <FormField label="Acting guild">
          {({ inputId, describedBy }) => (
            <select
              id={inputId}
              aria-describedby={describedBy}
              value={value}
              onChange={(e) => {
                const v = e.target.value;
                if (v === OTHER) return;
                setActingGuild(v.length > 0 ? v : null);
              }}
            >
              {actingGuildId === null ? <option value="">Choose a server</option> : null}
              {named.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
              {value === OTHER && actingGuildId !== null ? (
                <option value={OTHER}>Guild {shortId(actingGuildId)}</option>
              ) : null}
            </select>
          )}
        </FormField>
      </div>
    );
  }

  return (
    <div data-acting-guild title={actingGuildId ?? "No acting guild"}>
      <Icon name="guild" size={15} />
      <FormField label="Acting guild">
        {({ inputId, describedBy }) => (
          <input
            id={inputId}
            aria-describedby={describedBy}
            value={actingGuildId ?? ""}
            placeholder="guild id"
            spellCheck={false}
            onChange={(e) => {
              const v = e.target.value.trim();
              setActingGuild(v.length > 0 ? v : null);
            }}
          />
        )}
      </FormField>
    </div>
  );
}
