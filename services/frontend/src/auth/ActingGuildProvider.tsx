// Acting-guild context (the `X-Guild-Id` tenant selector).
//
// Most backend data is guild-scoped, so the admin picks an "acting guild" and
// the API client stamps `X-Guild-Id` onto every tenant-scoped request (the
// client resolves it lazily via `getActingGuild`). The initial value seeds from
// the access token's `guild_id` claim when present.
//
// The set of selectable guilds is intentionally NOT fetched here (no network IO
// at import/mount time) — the guild list belongs to a screen/data hook in a
// later phase. This provider only holds the current selection.

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useAuth } from "./AuthProvider";

export interface ActingGuildContextValue {
  /** Currently selected acting guild id, or null for guild-less context. */
  actingGuildId: string | null;
  /** Set/clear the acting guild. */
  setActingGuild: (guildId: string | null) => void;
  /** Stable lazy accessor for the API client's `getActingGuild`. */
  getActingGuild: () => string | null;
}

const ActingGuildContext = createContext<ActingGuildContextValue | null>(null);

export interface ActingGuildProviderProps {
  children: ReactNode;
  /** Optional explicit initial selection (tests / deep links). */
  initialGuildId?: string | null;
}

export function ActingGuildProvider({
  children,
  initialGuildId,
}: ActingGuildProviderProps): JSX.Element {
  const { claims } = useAuth();

  // Seed from explicit prop, else the token claim, else null.
  const seed =
    initialGuildId !== undefined
      ? initialGuildId
      : (typeof claims?.guild_id === "string" ? claims.guild_id : null);

  const [actingGuildId, setActingGuildId] = useState<string | null>(seed);

  const guildRef = useRef<string | null>(seed);
  guildRef.current = actingGuildId;

  const setActingGuild = useCallback((guildId: string | null) => {
    setActingGuildId(guildId);
  }, []);

  const getActingGuild = useCallback((): string | null => guildRef.current, []);

  const value = useMemo<ActingGuildContextValue>(
    () => ({ actingGuildId, setActingGuild, getActingGuild }),
    [actingGuildId, setActingGuild, getActingGuild],
  );

  return (
    <ActingGuildContext.Provider value={value}>{children}</ActingGuildContext.Provider>
  );
}

/** Access the acting-guild context. Throws when used outside the provider. */
export function useActingGuild(): ActingGuildContextValue {
  const ctx = useContext(ActingGuildContext);
  if (!ctx) {
    throw new Error("useActingGuild must be used within an <ActingGuildProvider>");
  }
  return ctx;
}
