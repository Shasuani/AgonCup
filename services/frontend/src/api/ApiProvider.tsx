// API client provider.
//
// Constructs a single lol-clients-ts client wired to the auth + acting-guild
// providers: `getToken` reads the in-memory access token, `getActingGuild`
// reads the selected tenant. Both are resolved lazily per request by the
// client, so the instance is stable across token refreshes / guild switches and
// only needs to be rebuilt when the accessor identities or baseUrl change.

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { createClient, type ApiClient } from "@lol/clients-ts";
import { useAuth } from "../auth/AuthProvider";
import { useActingGuild } from "../auth/ActingGuildProvider";
import { config } from "../config";

const ApiContext = createContext<ApiClient | null>(null);

export interface ApiProviderProps {
  children: ReactNode;
  /** Inject a prebuilt client (tests / storybook). Bypasses construction. */
  client?: ApiClient;
}

export function ApiProvider({ children, client }: ApiProviderProps): JSX.Element {
  const { getToken } = useAuth();
  const { getActingGuild } = useActingGuild();

  const value = useMemo<ApiClient>(() => {
    if (client) return client;
    return createClient({
      baseUrl: config.apiBaseUrl,
      getToken,
      getActingGuild,
    });
  }, [client, getToken, getActingGuild]);

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

/** Access the constructed API client. Throws outside an ApiProvider. */
export function useApi(): ApiClient {
  const ctx = useContext(ApiContext);
  if (!ctx) {
    throw new Error("useApi must be used within an <ApiProvider>");
  }
  return ctx;
}
