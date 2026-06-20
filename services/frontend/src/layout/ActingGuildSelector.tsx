// Acting-guild selector (drives the X-Guild-Id header via ActingGuildProvider).
//
// The list of guilds the operator can act as is NOT fetched here (the shell does
// no network IO at mount). A later screen/data hook can pass a richer guild
// list in; for now this is a free-text-backed control seeded from the token and
// exposed as a labeled input so the value is editable in dev. When a guild-list
// data source lands, swap this for a <select> populated from it.

import { FormField } from "../components/FormField";
import { useActingGuild } from "../auth/ActingGuildProvider";

export function ActingGuildSelector(): JSX.Element {
  const { actingGuildId, setActingGuild } = useActingGuild();

  return (
    <div data-acting-guild>
      <FormField label="Acting guild">
        {({ inputId, describedBy }) => (
          <input
            id={inputId}
            aria-describedby={describedBy}
            value={actingGuildId ?? ""}
            placeholder="guild id"
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
