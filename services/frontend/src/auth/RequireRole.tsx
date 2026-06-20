// Client-side route gate.
//
// RequireAuth: redirects unauthenticated users to the login screen.
// RequireRole: renders children only when the token carries one of the required
// roles; otherwise shows the NotAuthorized screen.
//
// This is a UX gate only. The backend independently enforces authorization on
// every request — a user who bypasses this still cannot call privileged APIs.

import type { ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";
import { hasAnyRole } from "./roles";

export interface RequireAuthProps {
  children?: ReactNode;
}

/** Gate that requires an authenticated session. */
export function RequireAuth({ children }: RequireAuthProps): JSX.Element {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children ?? <Outlet />}</>;
}

export interface RequireRoleProps {
  /** The user needs AT LEAST ONE of these roles to pass. */
  anyOf: readonly string[];
  children?: ReactNode;
}

/**
 * Gate that requires one of `anyOf` roles. Assumes an authenticated session
 * (wrap in or compose with {@link RequireAuth} for the redirect-to-login case);
 * an unauthenticated user has no roles and is sent to NotAuthorized.
 */
export function RequireRole({ anyOf, children }: RequireRoleProps): JSX.Element {
  const { isAuthenticated, claims } = useAuth();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  if (!hasAnyRole(claims, anyOf)) {
    return <Navigate to="/not-authorized" replace />;
  }
  return <>{children ?? <Outlet />}</>;
}

/** Inline (non-routing) variant: render children only when the role is held. */
export function RoleGate({
  anyOf,
  fallback = null,
  children,
}: {
  anyOf: readonly string[];
  fallback?: ReactNode;
  children: ReactNode;
}): JSX.Element {
  const { claims } = useAuth();
  return <>{hasAnyRole(claims, anyOf) ? children : fallback}</>;
}
