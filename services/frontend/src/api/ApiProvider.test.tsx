import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { createClient } from "@lol/clients-ts";
import { AuthProvider } from "../auth/AuthProvider";
import { ActingGuildProvider } from "../auth/ActingGuildProvider";
import { ApiProvider, useApi } from "./ApiProvider";
import { makeSession } from "../test/jwt";

function Caller(): JSX.Element {
  const api = useApi();
  // Kick off a request on mount so we can inspect the headers fetch saw.
  api.tournaments.list().catch(() => {});
  return <span>called</span>;
}

describe("ApiProvider", () => {
  it("constructs a client that stamps the token + acting guild onto requests", async () => {
    const fetchMock = vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );

    // Build the client exactly as ApiProvider does, but with an injected fetch,
    // and pass it in so we can observe the outgoing request.
    const session = makeSession({ sub: "u1", roles: ["guild_admin"], guild_id: "g1" });
    const client = createClient({
      baseUrl: "https://api.test",
      getToken: () => session.accessToken,
      getActingGuild: () => "g1",
      fetch: fetchMock as unknown as typeof fetch,
    });

    render(
      <AuthProvider initialSession={session}>
        <ActingGuildProvider initialGuildId="g1">
          <ApiProvider client={client}>
            <Caller />
          </ApiProvider>
        </ActingGuildProvider>
      </AuthProvider>,
    );

    expect(screen.getByText("called")).toBeInTheDocument();

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe("https://api.test/api/v1/tournaments");
    const headers = (init ?? {}).headers as Record<string, string>;
    expect(headers["Authorization"]).toBe(`Bearer ${session.accessToken}`);
    expect(headers["X-Guild-Id"]).toBe("g1");
  });

  it("wires getToken/getActingGuild from the providers when constructing its own client", async () => {
    // No injected client → ApiProvider builds one from config + providers. We
    // assert the wiring by reading the token through the client's request path
    // using a stubbed global fetch.
    const fetchMock = vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    const original = globalThis.fetch;
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      const session = makeSession({ sub: "u1", roles: ["guild_admin"] });
      render(
        <AuthProvider initialSession={session}>
          <ActingGuildProvider initialGuildId="guild-xyz">
            <ApiProvider>
              <Caller />
            </ApiProvider>
          </ActingGuildProvider>
        </AuthProvider>,
      );
      await waitFor(() => expect(fetchMock).toHaveBeenCalled());
      const init = fetchMock.mock.calls[0]![1] ?? {};
      const headers = init.headers as Record<string, string>;
      expect(headers["Authorization"]).toBe(`Bearer ${session.accessToken}`);
      expect(headers["X-Guild-Id"]).toBe("guild-xyz");
    } finally {
      globalThis.fetch = original;
    }
  });
});
