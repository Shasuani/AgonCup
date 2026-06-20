// Authenticated app chrome: a skip link, primary nav, the acting-guild
// selector, a sign-out control, and an <Outlet> for the routed screen. Admin-
// only nav links are hidden (RoleGate) for users who can't use them — but the
// routes themselves are still gated server-side and by RequireRole.

import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { RoleGate } from "../auth/RequireRole";
import { GUILD_ADMIN, PLATFORM_OPERATOR } from "../auth/roles";
import { ActingGuildSelector } from "./ActingGuildSelector";
import { Button } from "../components/Button";

interface NavItem {
  to: string;
  label: string;
  /** When set, the link is only shown if the user holds one of these roles. */
  anyOf?: readonly string[];
}

const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { to: "/tournaments", label: "Tournaments", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/teams", label: "Teams", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/accounts", label: "Accounts" },
  { to: "/disputes", label: "Disputes", anyOf: [GUILD_ADMIN, PLATFORM_OPERATOR] },
  { to: "/audit", label: "Audit log", anyOf: [PLATFORM_OPERATOR] },
];

export function AppLayout(): JSX.Element {
  const { logout } = useAuth();

  return (
    <div data-app-shell>
      <a href="#main-content" data-skip-link>
        Skip to content
      </a>
      <header data-app-header>
        <span data-brand>LeagueOfLeagues — Admin</span>
        <nav aria-label="Primary">
          <ul>
            {NAV_ITEMS.map((item) => {
              const link = (
                <li key={item.to}>
                  <NavLink to={item.to}>{item.label}</NavLink>
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
        <div data-app-header-actions>
          <ActingGuildSelector />
          <Button variant="secondary" onClick={logout}>
            Sign out
          </Button>
        </div>
      </header>
      <main id="main-content" data-app-main>
        <Outlet />
      </main>
    </div>
  );
}
