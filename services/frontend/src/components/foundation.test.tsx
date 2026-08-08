// The shared screen primitives every tab is built from: ScreenHeader,
// StatusBadge, IdChip, and DataTable's loading/empty states.

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DataTable, type Column } from "./DataTable";
import { IdChip } from "./IdChip";
import { ScreenHeader, SectionHeader } from "./ScreenHeader";
import { StatusBadge } from "./StatusBadge";
import { TOURNAMENT_STATUS_LABELS } from "../lib/labels";

describe("ScreenHeader", () => {
  it("renders an addressable h1 with an intro and actions", () => {
    render(
      <ScreenHeader
        id="x-heading"
        title="Tournaments"
        intro="Everything you run."
        actions={<button type="button">Refresh</button>}
      />,
    );
    const heading = screen.getByRole("heading", { level: 1, name: "Tournaments" });
    // Screens label their landmark with this id, so it must survive.
    expect(heading).toHaveAttribute("id", "x-heading");
    expect(screen.getByText("Everything you run.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  });

  it("omits the intro and action slots when not supplied", () => {
    const { container } = render(<ScreenHeader id="y-heading" title="Teams" />);
    expect(container.querySelector("[data-screen-intro]")).toBeNull();
    expect(container.querySelector("[data-screen-actions]")).toBeNull();
  });

  it("drops a section header to h2 so a screen keeps one h1", () => {
    render(<SectionHeader id="s-heading" title="Danger zone" />);
    expect(screen.getByRole("heading", { level: 2, name: "Danger zone" })).toBeInTheDocument();
  });
});

describe("StatusBadge", () => {
  it("shows the human label but keeps the raw value as the color key", () => {
    render(<StatusBadge status="check_in" labels={TOURNAMENT_STATUS_LABELS} />);
    const badge = screen.getByText("Check-in");
    expect(badge).toHaveAttribute("data-status", "check_in");
  });

  it("humanizes an unmapped status rather than rendering the raw enum", () => {
    render(<StatusBadge status="in_progress" />);
    expect(screen.getByText("In progress")).toBeInTheDocument();
  });
});

describe("IdChip", () => {
  it("truncates the id on screen but copies it in full", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

    const id = "3f2b9c14-77aa-4c2e-9f31-0d1e2a3b4c5d";
    render(<IdChip value={id} kind="team id" />);

    // Only the first block is shown; the full value stays reachable as the title.
    expect(screen.getByText("3f2b9c14")).toBeInTheDocument();
    expect(screen.queryByText(id)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: `Copy team id ${id}` }));
    expect(writeText).toHaveBeenCalledWith(id);
    await waitFor(() => expect(screen.getByText("Copied")).toBeInTheDocument());

    vi.unstubAllGlobals();
  });

  it("says so instead of pretending when the clipboard is unavailable", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    render(<IdChip value="abcdefgh-1234" />);

    await userEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByText("Copy failed")).toBeInTheDocument());

    vi.unstubAllGlobals();
  });
});

interface Row {
  id: string;
  name: string;
}

const columns: Column<Row>[] = [{ id: "name", header: "Name", cell: (r) => r.name }];

describe("DataTable loading + empty", () => {
  it("draws shimmer rows under the real header on the first load", () => {
    const { container } = render(
      <DataTable
        caption="Teams"
        columns={columns}
        rows={[]}
        rowKey={(r) => r.id}
        loading
        loadingRows={3}
      />,
    );
    // Header stays put so the layout does not shift when data lands.
    expect(screen.getByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    expect(container.querySelectorAll(".lol-skeleton").length).toBeGreaterThan(0);
    expect(container.querySelector("table")).toHaveAttribute("aria-busy", "true");
  });

  it("keeps existing rows visible while refreshing", () => {
    render(
      <DataTable
        caption="Teams"
        columns={columns}
        rows={[{ id: "1", name: "Alpha" }]}
        rowKey={(r) => r.id}
        loading
      />,
    );
    // Blanking a table the admin is reading, to show a spinner, loses their place.
    expect(screen.getByText("Alpha")).toBeInTheDocument();
  });

  it("offers an action alongside the empty message", () => {
    render(
      <DataTable
        caption="Teams"
        columns={columns}
        rows={[]}
        rowKey={(r) => r.id}
        emptyMessage="No teams yet."
        emptyAction={<button type="button">Create team</button>}
      />,
    );
    expect(screen.getByText("No teams yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create team" })).toBeInTheDocument();
  });
});
