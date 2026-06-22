// Helpers to assemble the standard admin command set.
//
// Kept separate from the palette component so the host (topbar/AppLayout) owns
// the router + auth + theme wiring and the palette stays a pure presenter. None
// of these helpers touch the network.

import type { IconName } from "../Icon";
import type { Command } from "./types";

export interface NavCommandSpec {
  /** Route target passed to the host's navigate(). */
  to: string;
  /** Human label (also the palette search target). */
  label: string;
  /** Optional leading glyph. */
  icon?: IconName;
}

export interface BuildNavCommandsOptions {
  items: readonly NavCommandSpec[];
  navigate: (to: string) => void;
  /** Group header for these commands. Defaults to "Navigation". */
  group?: string;
}

/** Build "Go to <screen>" navigation commands from a nav spec list. */
export function buildNavCommands({
  items,
  navigate,
  group = "Navigation",
}: BuildNavCommandsOptions): Command[] {
  return items.map((item) => ({
    id: `nav:${item.to}`,
    label: `Go to ${item.label}`,
    hint: item.to,
    group,
    icon: item.icon,
    keywords: [item.label, item.to],
    run: () => navigate(item.to),
  }));
}

export interface BuildActionCommandsOptions {
  onToggleTheme?: () => void;
  onSignOut?: () => void;
  /** Group header for these commands. Defaults to "Actions". */
  group?: string;
}

/** Build the cross-cutting action commands (theme toggle, sign out). */
export function buildActionCommands({
  onToggleTheme,
  onSignOut,
  group = "Actions",
}: BuildActionCommandsOptions): Command[] {
  const commands: Command[] = [];
  if (onToggleTheme) {
    commands.push({
      id: "action:toggle-theme",
      label: "Toggle theme",
      hint: "Switch between light and dark",
      group,
      icon: "sun",
      keywords: ["dark", "light", "appearance", "color scheme"],
      run: onToggleTheme,
    });
  }
  if (onSignOut) {
    commands.push({
      id: "action:sign-out",
      label: "Sign out",
      hint: "End your session",
      group,
      icon: "logout",
      keywords: ["logout", "log out", "exit"],
      run: onSignOut,
    });
  }
  return commands;
}
