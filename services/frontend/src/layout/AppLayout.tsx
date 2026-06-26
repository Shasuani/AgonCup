// Authenticated app chrome: a responsive SIDEBAR + TOPBAR shell.
//
// Layout:
//   - Left <aside data-app-sidebar>: brand logo + the Primary nav (icon + label
//     NavLinks, RoleGate-gated). A CSS grid sidebar at >=900px; below that it
//     becomes an off-canvas drawer toggled by the topbar hamburger (focus-trapped
//     overlay, Escape-to-close).
//   - <header data-app-header>: mobile hamburger + breadcrumbs (left), and the
//     header actions (right): command-palette trigger, theme toggle, the acting-
//     guild selector, and the user menu (which holds Sign out).
//   - <main id="main-content" data-app-main>: the routed <Outlet>.
//
// LOAD-BEARING (do not remove): data-app-shell, data-app-header, data-app-main +
// id="main-content", data-skip-link ("Skip to content" -> "#main-content"),
// data-brand, data-app-header-actions, <nav aria-label="Primary"> with its
// <ul>/<li>/<NavLink> structure, the ActingGuildSelector, and the Sign out
// action (now inside UserMenu) wired to useAuth().logout. The full NAV_ITEMS
// array (to/label/anyOf) is preserved verbatim.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  { to: "/tournaments", label: "Tournaments", icon: "tournaments", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/teams", label: "Teams", icon: "teams", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/accounts", label: "Accounts", icon: "accounts", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/disputes", label: "Disputes", icon: "disputes", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/audit", label: "Audit log", icon: "audit", anyOf: [PLATFORM_OPERATOR] },
  { to: "/guilds", label: "Guilds", icon: "teams", anyOf: [PLATFORM_OPERATOR] },
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

/** Matches the AppLayout.css mobile-drawer breakpoint (max-width: 899px). */
const MOBILE_QUERY = "(max-width: 899px)";

/** Focusable selectors for the drawer focus trap (mirrors CommandPalette). */
const DRAWER_FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

export function AppLayout(): JSX.Element {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const sidebarRef = useRef<HTMLElement | null>(null);
  const hamburgerRef = useRef<HTMLButtonElement | null>(null);

  const closeDrawer = useCallback(() => setDrawerOpen(false), []);

  // Track the mobile breakpoint so the off-canvas drawer's focusability can
  // follow its visibility. Guarded for environments without matchMedia (jsdom).
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mql = window.matchMedia(MOBILE_QUERY);
    const apply = (matches: boolean): void => setIsMobile(matches);
    apply(mql.matches);
    const onChange = (e: MediaQueryListEvent): void => apply(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  // The drawer is only a contained modal on mobile while open.
  const drawerActive = isMobile && drawerOpen;

  // Keep the off-canvas sidebar out of the tab order when the drawer is closed
  // on mobile (WCAG 2.4.3 / 2.4.7): an off-screen, sighted-invisible nav must
  // not be keyboard-focusable. `inert` is set imperatively so it works
  // regardless of React's DOM typings; CSS `visibility` is a belt-and-braces
  // fallback (see AppLayout.css).
  useEffect(() => {
    const aside = sidebarRef.current;
    if (aside === null) return;
    const hidden = isMobile && !drawerOpen;
    if (hidden) {
      aside.setAttribute("inert", "");
      aside.setAttribute("aria-hidden", "true");
    } else {
      aside.removeAttribute("inert");
      aside.removeAttribute("aria-hidden");
    }
  }, [isMobile, drawerOpen]);

  // On open (mobile), move focus into the drawer so the modal nav is the next
  // tab stop rather than the topbar/content behind the scrim.
  useEffect(() => {
    if (!drawerActive) return;
    const aside = sidebarRef.current;
    if (aside === null) return;
    const first = aside.querySelector<HTMLElement>(DRAWER_FOCUSABLE);
    first?.focus();
  }, [drawerActive]);

  // Escape closes the mobile drawer; restore focus to the hamburger. While open
  // on mobile, trap Tab within the sidebar so focus cannot wander behind the
  // scrim (mirrors the CommandPalette modal trap).
  useEffect(() => {
    if (!drawerOpen) return;
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") {
        setDrawerOpen(false);
        hamburgerRef.current?.focus();
        return;
      }
      if (e.key !== "Tab" || !drawerActive) return;
      const aside = sidebarRef.current;
      if (aside === null) return;
      const nodes = Array.from(aside.querySelectorAll<HTMLElement>(DRAWER_FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );
      if (nodes.length === 0) {
        e.preventDefault();
        return;
      }
      const firstNode = nodes[0];
      const lastNode = nodes[nodes.length - 1];
      const active = document.activeElement;
      if (e.shiftKey) {
        if (active === firstNode || !aside.contains(active)) {
          e.preventDefault();
          lastNode?.focus();
        }
      } else if (active === lastNode || !aside.contains(active)) {
        e.preventDefault();
        firstNode?.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen, drawerActive]);

  return (
    <CommandPaletteProvider>
      <div data-app-shell data-drawer-open={drawerOpen || undefined}>
        <a href="#main-content" data-skip-link>
          Skip to content
        </a>

        {/* Scrim behind the mobile drawer. */}
        <div
          data-app-scrim
          aria-hidden="true"
          onClick={closeDrawer}
        />

        <aside data-app-sidebar ref={sidebarRef}>
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
                    <NavLink to={item.to} onClick={closeDrawer}>
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
              <button
                type="button"
                data-variant="secondary"
                data-app-hamburger
                aria-label="Open navigation"
                aria-expanded={drawerOpen}
                onClick={() => setDrawerOpen((v) => !v)}
                ref={hamburgerRef}
              >
                <Icon name={drawerOpen ? "close" : "menu"} size={18} />
              </button>
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
