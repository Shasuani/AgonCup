// Authenticated app chrome: a responsive SIDEBAR + TOPBAR shell.
//
// Layout:
//   - Left <aside data-app-sidebar>: brand logo + the Primary nav (icon + label
//     NavLinks, RoleGate-gated). A CSS grid sidebar at >=900px; below that it
//     lays out as a horizontal, scrollable nav strip above the content. It is
//     ALWAYS visible — there is no drawer and no hamburger. Both were removed:
//     the button lost a specificity fight with `button[data-variant]` and so
//     rendered on desktop, where nothing responds to it, and the machinery it
//     needed (focus trap, `inert` toggling, scrim, Escape handling, a
//     matchMedia listener) served exactly one breakpoint.
//   - <header data-app-header>: breadcrumbs (left), and the header actions
//     (right): command-palette trigger, theme toggle, the acting-guild
//     selector, and the user menu (which holds Sign out).
//   - <main id="main-content" data-app-main>: the routed <Outlet>.
//
// LOAD-BEARING (do not remove): data-app-shell, data-app-header, data-app-main +
// id="main-content", data-skip-link ("Skip to content" -> "#main-content"),
// data-brand, data-app-header-actions, <nav aria-label="Primary"> with its
// <ul>/<li>/<NavLink> structure, the ActingGuildSelector, and the Sign out
// action (now inside UserMenu) wired to useAuth().logout. The full NAV_ITEMS
// array (to/label/anyOf) is preserved verbatim.

import { useMemo } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { RoleGate } from "../auth/RequireRole";
import { GUILD_ADMIN, PLATFORM_OPERATOR, hasAnyRole } from "../auth/roles";
import { ActingGuildSelector } from "./ActingGuildSelector";
import { Button } from "../components/Button";
import { Icon, type IconName } from "../components/Icon";
import { ThemeToggle, useTheme } from "../theme";
import {
  CommandPaletteProvider,
  useCommandPalette,
  useCommandRegistration,
  buildNavCommands,
  buildActionCommands,
} from "../components/CommandPalette";
import { Breadcrumbs } from "./Breadcrumbs";
import { UserMenu } from "./UserMenu";
import { BrandLogo, BrandMark } from "./Logo";

import "./AppLayout.css";

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  /** When set, the link is only shown if the user holds one of these roles. */
  anyOf?: readonly string[];
}

const NAV_ITEMS: ReadonlyArray<NavItem> = [
  // No `anyOf`: this is where a role-less new user installs the bot and thereby
  // GETS a role. Gating it would hide the only door in.
  { to: "/servers", label: "Servers", icon: "server" },
  { to: "/tournaments", label: "Tournaments", icon: "tournaments", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/teams", label: "Teams", icon: "teams", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/accounts", label: "Accounts", icon: "accounts", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/history", label: "Match history", icon: "history", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/disputes", label: "Disputes", icon: "disputes", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/audit", label: "Audit log", icon: "audit", anyOf: [PLATFORM_OPERATOR] },
  { to: "/guilds", label: "Guilds", icon: "guild", anyOf: [PLATFORM_OPERATOR] },
];

/** Registers the standard command set + renders the topbar palette trigger. */
function CommandRegistrar(): JSX.Element {
  const navigate = useNavigate();
  const { logout, claims } = useAuth();
  const { toggleTheme } = useTheme();
  const { open } = useCommandPalette();

  const commands = useMemo(() => {
    const visible = NAV_ITEMS.filter((i) => hasAnyRole(claims, i.anyOf ?? []));
    return [
      {
        id: "nav:/",
        label: "Go to Dashboard",
        hint: "/",
        group: "Navigation",
        icon: "dashboard" as IconName,
        keywords: ["home", "dashboard"],
        run: () => navigate("/"),
      },
      ...buildNavCommands({
        items: visible.map((i) => ({ to: i.to, label: i.label, icon: i.icon })),
        navigate: (to) => navigate(to),
      }),
      ...buildActionCommands({ onToggleTheme: toggleTheme, onSignOut: logout }),
    ];
  }, [navigate, logout, toggleTheme, claims]);

  useCommandRegistration(commands, [commands]);

  return (
    <Button
      variant="secondary"
      data-command-trigger
      aria-label="Open command palette"
      aria-keyshortcuts="Control+K Meta+K"
      onClick={open}
    >
      <Icon name="search" size={16} />
      <span data-command-trigger-label>Search</span>
      <kbd data-command-trigger-kbd>Ctrl K</kbd>
    </Button>
  );
}

export function AppLayout(): JSX.Element {
  return (
    <CommandPaletteProvider>
      <div data-app-shell>
        <a href="#main-content" data-skip-link>
          Skip to content
        </a>

        <aside data-app-sidebar>
          <div data-app-sidebar-head>
            <span data-brand>
              <BrandLogo data-brand-logo aria-label="LeagueOfLeagues — Admin" />
              <BrandMark data-brand-mark aria-label="LeagueOfLeagues — Admin" />
            </span>
          </div>
          <nav aria-label="Primary">
            <ul>
              {NAV_ITEMS.map((item) => {
                const link = (
                  <li key={item.to}>
                    <NavLink to={item.to}>
                      <Icon name={item.icon} size={18} aria-hidden="true" />
                      <span data-nav-label>{item.label}</span>
                    </NavLink>
                  </li>
                );
                return item.anyOf ? (
                  <RoleGate key={item.to} anyOf={item.anyOf}>
                    {link}
                  </RoleGate>
                ) : (
                  link
                );
              })}
            </ul>
          </nav>
        </aside>

        <div data-app-content>
          <header data-app-header>
            <div data-app-header-left>
              <Breadcrumbs />
            </div>
            <div data-app-header-actions>
              <CommandRegistrar />
              <ThemeToggle />
              <ActingGuildSelector />
              <UserMenu />
            </div>
          </header>

          <main id="main-content" data-app-main>
            <Outlet />
          </main>
        </div>
      </div>
    </CommandPaletteProvider>
  );
}
