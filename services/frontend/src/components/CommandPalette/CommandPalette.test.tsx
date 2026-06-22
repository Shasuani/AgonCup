import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  CommandPaletteProvider,
  useCommandPalette,
  useCommandRegistration,
} from "./CommandPalette";
import { buildActionCommands, buildNavCommands } from "./commands";
import type { Command } from "./types";

function Trigger(): JSX.Element {
  const { open } = useCommandPalette();
  return (
    <button type="button" onClick={open}>
      Open palette
    </button>
  );
}

function Register({ commands }: { commands: readonly Command[] }): JSX.Element {
  useCommandRegistration(commands, [commands]);
  return <Trigger />;
}

function setup(commands: readonly Command[]): void {
  render(
    <CommandPaletteProvider>
      <Register commands={commands} />
    </CommandPaletteProvider>,
  );
}

describe("CommandPalette", () => {
  it("opens via the trigger and renders an accessible dialog + combobox", async () => {
    setup([{ id: "a", label: "Alpha", run: vi.fn() }]);
    await userEvent.click(screen.getByRole("button", { name: "Open palette" }));

    const dialog = screen.getByRole("dialog", { name: /command palette/i });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /search commands/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Alpha/ })).toBeInTheDocument();
  });

  it("opens with Cmd/Ctrl+K and closes with Escape", async () => {
    const user = userEvent.setup();
    setup([{ id: "a", label: "Alpha", run: vi.fn() }]);

    await user.keyboard("{Control>}k{/Control}");
    expect(screen.getByRole("dialog", { name: /command palette/i })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("filters commands by typed query", async () => {
    const user = userEvent.setup();
    setup([
      { id: "a", label: "Go to Tournaments", run: vi.fn() },
      { id: "b", label: "Go to Teams", run: vi.fn() },
    ]);
    await user.click(screen.getByRole("button", { name: "Open palette" }));

    await user.type(screen.getByRole("combobox"), "tourn");
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("Go to Tournaments");
  });

  it("runs the active command on Enter and closes", async () => {
    const run = vi.fn();
    const user = userEvent.setup();
    setup([{ id: "a", label: "Alpha", run }]);
    await user.click(screen.getByRole("button", { name: "Open palette" }));

    await user.keyboard("{Enter}");
    expect(run).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("moves the active option with arrow keys (aria-activedescendant)", async () => {
    const user = userEvent.setup();
    setup([
      { id: "a", label: "Alpha", run: vi.fn() },
      { id: "b", label: "Bravo", run: vi.fn() },
    ]);
    await user.click(screen.getByRole("button", { name: "Open palette" }));

    const combobox = screen.getByRole("combobox");
    const first = screen.getByRole("option", { name: /Alpha/ });
    const second = screen.getByRole("option", { name: /Bravo/ });

    expect(first).toHaveAttribute("aria-selected", "true");
    expect(combobox.getAttribute("aria-activedescendant")).toBe(first.id);

    await user.keyboard("{ArrowDown}");
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(combobox.getAttribute("aria-activedescendant")).toBe(second.id);
  });

  it("runs a command on click", async () => {
    const run = vi.fn();
    const user = userEvent.setup();
    setup([{ id: "a", label: "Alpha", run }]);
    await user.click(screen.getByRole("button", { name: "Open palette" }));

    await user.click(screen.getByRole("option", { name: /Alpha/ }));
    expect(run).toHaveBeenCalledOnce();
  });

  it("shows the empty message when nothing matches", async () => {
    const user = userEvent.setup();
    setup([{ id: "a", label: "Alpha", run: vi.fn() }]);
    await user.click(screen.getByRole("button", { name: "Open palette" }));

    await user.type(screen.getByRole("combobox"), "zzzzz");
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(screen.getByText(/no matching commands/i)).toBeInTheDocument();
  });

  it("throws when the hook is used outside the provider", () => {
    const Bare = (): JSX.Element => {
      useCommandPalette();
      return <div />;
    };
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Bare />)).toThrow(/CommandPaletteProvider/);
    spy.mockRestore();
  });
});

describe("command builders", () => {
  it("buildNavCommands produces Go-to entries that navigate", () => {
    const navigate = vi.fn();
    const commands = buildNavCommands({
      items: [{ to: "/teams", label: "Teams", icon: "teams" }],
      navigate,
    });
    expect(commands).toHaveLength(1);
    expect(commands[0]?.label).toBe("Go to Teams");
    commands[0]?.run();
    expect(navigate).toHaveBeenCalledWith("/teams");
  });

  it("buildActionCommands wires theme + sign out and omits absent handlers", () => {
    const onSignOut = vi.fn();
    const commands = buildActionCommands({ onSignOut });
    expect(commands.map((c) => c.id)).toEqual(["action:sign-out"]);
    commands[0]?.run();
    expect(onSignOut).toHaveBeenCalledOnce();
  });

  it("registers built commands and runs them through the palette", async () => {
    const navigate = vi.fn();
    const user = userEvent.setup();
    const commands = [
      ...buildNavCommands({ items: [{ to: "/audit", label: "Audit log" }], navigate }),
    ];
    setup(commands);
    await user.click(screen.getByRole("button", { name: "Open palette" }));

    const list = screen.getByRole("listbox", { name: /commands/i });
    await user.click(within(list).getByRole("option", { name: /Audit log/ }));
    expect(navigate).toHaveBeenCalledWith("/audit");
  });
});
