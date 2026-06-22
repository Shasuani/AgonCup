// Shared types for the command palette.
//
// A "command" is a single actionable entry rendered in the palette list. The
// palette is framework-light: commands are plain data + a `run` callback. Route
// navigation, theme toggling, sign-out, etc. are all just commands the host
// registers — the palette itself never touches the network or the router
// directly (the caller wires those via `run`).

import type { IconName } from "../Icon";

export interface Command {
  /** Stable unique id (used as React key and for de-duplication). */
  id: string;
  /** Primary label shown in the list and matched by type-to-filter. */
  label: string;
  /** Optional secondary text (also matched by the filter). */
  hint?: string;
  /** Optional grouping header (e.g. "Navigation", "Account"). */
  group?: string;
  /** Optional leading glyph from the shared Icon set. */
  icon?: IconName;
  /** Extra keywords matched by the filter but not displayed. */
  keywords?: readonly string[];
  /**
   * Invoked when the command is chosen (Enter or click). The palette closes
   * automatically afterwards. Must not throw; side effects are the host's.
   */
  run: () => void;
}

export interface CommandPaletteContextValue {
  /** Open the palette (e.g. from a topbar "Search" button). */
  open: () => void;
  /** Close the palette. */
  close: () => void;
  /** Toggle open/closed. */
  toggle: () => void;
  /** Whether the palette is currently open. */
  isOpen: boolean;
  /**
   * Register commands for the lifetime of the calling component. Returns an
   * unregister function. Re-register (via effect deps) when the command set
   * changes. Later registrations with the same id override earlier ones.
   */
  register: (commands: readonly Command[]) => () => void;
}
