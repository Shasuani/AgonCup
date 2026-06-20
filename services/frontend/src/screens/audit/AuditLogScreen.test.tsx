import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ApiClient } from "@lol/clients-ts";
import { AppProviders } from "../../App";
import { makeSession } from "../../test/jwt";
import AuditLogScreen from "./AuditLogScreen";
import type { AuditEvent } from "./audit";

const EVENTS: AuditEvent[] = [
  {
    id: "e-1",
    actor_id: "admin-1",
    actor_name: "Operator One",
    action: "result.override",
    entity_type: "match",
    entity_id: "m-1",
    guild_id: "guild-alpha",
    detail: "replay confirmed",
    created_at: "2026-06-20T10:00:00.000Z",
  },
  {
    id: "e-2",
    actor_id: "admin-2",
    actor_name: null,
    action: "team.delete",
    entity_type: "team",
    entity_id: "team-9",
    guild_id: "guild-beta",
    detail: null,
    created_at: "2026-06-20T11:30:00.000Z",
  },
];

function fakeClient(events: AuditEvent[] = EVENTS): ApiClient {
  return {
    request: vi.fn(async () => events),
  } as unknown as ApiClient;
}

function renderAudit(client: ApiClient): void {
  render(
    <AppProviders
      initialSession={makeSession({
        sub: "op-1",
        roles: ["platform_operator"],
        guild_id: "guild-alpha",
      })}
      initialGuildId="guild-alpha"
      client={client}
    >
      <MemoryRouter>
        <AuditLogScreen />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("AuditLogScreen", () => {
  it("renders audit events from the mocked client (actor, action, entity, time)", async () => {
    const client = fakeClient();
    renderAudit(client);

    expect(await screen.findByText("Operator One")).toBeInTheDocument();
    expect(screen.getByText("result.override")).toBeInTheDocument();
    expect(screen.getByText("team.delete")).toBeInTheDocument();
    // Entity ids surfaced.
    expect(screen.getByText("m-1")).toBeInTheDocument();
    expect(screen.getByText("team-9")).toBeInTheDocument();
    // Falls back to actor_id when no name.
    expect(screen.getAllByText("admin-2").length).toBeGreaterThan(0);

    expect(client.request).toHaveBeenCalledWith(
      expect.objectContaining({ method: "GET", path: "/audit/events" }),
    );
  });

  it("filters events by guild", async () => {
    const client = fakeClient();
    renderAudit(client);
    const user = userEvent.setup();

    await screen.findByText("result.override");
    // Both actions visible initially.
    expect(screen.getByText("team.delete")).toBeInTheDocument();

    const select = screen.getByLabelText("Filter by guild");
    await user.selectOptions(select, "guild-alpha");

    await waitFor(() =>
      expect(screen.queryByText("team.delete")).not.toBeInTheDocument(),
    );
    // guild-alpha event remains.
    expect(screen.getByText("result.override")).toBeInTheDocument();

    // Switching back to all guilds restores the beta event.
    await user.selectOptions(select, screen.getByRole("option", { name: "All guilds" }));
    await waitFor(() => expect(screen.getByText("team.delete")).toBeInTheDocument());
  });

  it("shows the empty state when no events match the filter", async () => {
    const client = fakeClient([]);
    renderAudit(client);
    expect(
      await screen.findByText("No audit events match the current filter."),
    ).toBeInTheDocument();
  });

  it("escapes server-provided strings (no HTML injection)", async () => {
    const client = fakeClient([
      {
        id: "e-x",
        actor_id: "a",
        actor_name: "<img src=x onerror=alert(1)>",
        action: "<b>evil</b>",
        entity_type: "match",
        entity_id: "m",
        guild_id: "guild-alpha",
        detail: "<script>bad()</script>",
        created_at: "2026-06-20T10:00:00.000Z",
      },
    ]);
    renderAudit(client);

    expect(
      await screen.findByText("<img src=x onerror=alert(1)>"),
    ).toBeInTheDocument();
    expect(screen.getByText("<b>evil</b>")).toBeInTheDocument();
    expect(screen.getByText("<script>bad()</script>")).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });
});
