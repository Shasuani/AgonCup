// Admin SPA shell (PLAN.md §3.4).
//
// Composes the providers (auth -> acting guild -> API client), the router, and
// the route table. SCREEN modules are lazy-loaded (code-split) and filled in by
// the Screens phase; admin-only screens are wrapped in RequireRole. Everything
// under the app layout requires authentication (RequireAuth).

import { Suspense, lazy, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, type AuthSession } from "./auth/AuthProvider";
import { ActingGuildProvider } from "./auth/ActingGuildProvider";
import { ApiProvider } from "./api/ApiProvider";
import { RequireAuth, RequireRole } from "./auth/RequireRole";
import { GUILD_ADMIN, PLATFORM_OPERATOR } from "./auth/roles";
import { AppLayout } from "./layout/AppLayout";
import { Spinner } from "./components/Spinner";
import { ThemeProvider } from "./theme";
import { ToastProvider } from "./components/Toast";
import type { ApiClient } from "@lol/clients-ts";

// Eager (tiny) auth screens.
import LoginScreen from "./screens/auth/LoginScreen";
import AuthCallback from "./screens/auth/AuthCallback";
import NotAuthorizedScreen from "./screens/auth/NotAuthorizedScreen";
import HomeScreen from "./screens/HomeScreen";

// Lazily code-split the heavy feature screens (filled by the Screens phase).
const TournamentsScreen = lazy(() => import("./screens/tournaments/TournamentsScreen"));
const TeamsScreen = lazy(() => import("./screens/teams/TeamsScreen"));
const AccountsScreen = lazy(() => import("./screens/accounts/AccountsScreen"));
const DisputeQueueScreen = lazy(() => import("./screens/results/DisputeQueueScreen"));
const MatchHistoryScreen = lazy(() => import("./screens/results/MatchHistoryScreen"));
const AuditLogScreen = lazy(() => import("./screens/audit/AuditLogScreen"));
const GuildsScreen = lazy(() => import("./screens/guilds/GuildsScreen"));
const ServersScreen = lazy(() => import("./screens/servers/ServersScreen"));
const NotFound = lazy(() => import("./screens/NotFound"));

const ADMIN_ROLES = [GUILD_ADMIN, PLATFORM_OPERATOR] as const;
const OPERATOR_ROLES = [PLATFORM_OPERATOR] as const;

/** Provider stack shared by the app and tests. */
export interface AppProvidersProps {
  children: ReactNode;
  initialSession?: AuthSession | null;
  initialGuildId?: string | null;
  /** Inject a client (tests) instead of constructing one. */
  client?: ApiClient;
}

export function AppProviders({
  children,
  initialSession = null,
  initialGuildId,
  client,
}: AppProvidersProps): JSX.Element {
  return (
    <ThemeProvider>
      <AuthProvider initialSession={initialSession}>
        <ActingGuildProvider initialGuildId={initialGuildId}>
          <ApiProvider client={client}>
            <ToastProvider>{children}</ToastProvider>
          </ApiProvider>
        </ActingGuildProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}

/** The route table (router-agnostic so tests can mount it in a MemoryRouter). */
export function AppRoutes(): JSX.Element {
  return (
    <Suspense fallback={<Spinner label="Loading screen" />}>
      <Routes>
        <Route path="/login" element={<LoginScreen />} />
        <Route path="/auth/callback" element={<AuthCallback />} />
        <Route path="/not-authorized" element={<NotAuthorizedScreen />} />

        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route index element={<HomeScreen />} />

            {/* Deliberately UNGATED: installing the bot is how a user becomes a
                guild_admin, so requiring that role here would be circular and
                would leave every new account with nowhere to go. */}
            <Route path="servers" element={<ServersScreen />} />

            <Route element={<RequireRole anyOf={ADMIN_ROLES} />}>
              <Route path="accounts" element={<AccountsScreen />} />
              <Route path="tournaments" element={<TournamentsScreen />} />
              <Route path="teams" element={<TeamsScreen />} />
              <Route path="history" element={<MatchHistoryScreen />} />
              <Route path="disputes" element={<DisputeQueueScreen />} />
            </Route>

            <Route element={<RequireRole anyOf={OPERATOR_ROLES} />}>
              <Route path="audit" element={<AuditLogScreen />} />
              <Route path="guilds" element={<GuildsScreen />} />
            </Route>

            {/* Unknown paths under the authed shell render an in-layout 404. */}
            <Route path="*" element={<NotFound />} />
          </Route>
        </Route>

        {/* Genuinely unknown public paths fall back to home (RequireAuth then
            redirects unauthenticated users to /login). */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}

export function App(): JSX.Element {
  return (
    <AppProviders>
      <BrowserRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <AppRoutes />
      </BrowserRouter>
    </AppProviders>
  );
}
