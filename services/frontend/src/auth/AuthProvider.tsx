// Auth context for the admin SPA.
//
// Holds the access + refresh tokens IN MEMORY (React state). Login is a browser
// redirect to the backend's Discord-OAuth start; the backend redirects back to
// `/auth/callback#access_token=...&refresh_token=...`, which AuthCallback (see
// AuthCallback.tsx) parses and feeds to `setSession`.
//
// SECURITY TRADE-OFF (follow-up): keeping the refresh token in JS memory exposes
// it to XSS (any injected script can read it) but it never touches disk and is
// gone on reload. The hardened alternative is an httpOnly, Secure, SameSite
// cookie for the refresh token (unreadable by JS) with the access token kept in
// memory. That requires backend cookie support + CSRF protection on the refresh
// endpoint. Tracked as a required follow-up below.
//
// REQUIRED FOLLOW-UPS (backend coordination):
//   1. Move the refresh token to an httpOnly cookie (XSS hardening, above).
//
// (The callback redirect shape was previously ASSUMED here and unimplemented on
// the backend — `/auth/callback` returned the TokenPair as JSON, so signing in
// dead-ended on a page of raw tokens. The backend now 303s to
// `<FRONTEND_BASE_URL>/auth/callback#access_token=...&refresh_token=...`.)

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { AccessTokenClaims, TokenPair } from "@lol/clients-ts";
import { decodeAccessToken } from "./jwt";
import { config } from "../config";

/** The backend `/auth/login` URL the browser is redirected to. */
function backendLoginUrl(): string {
  return `${config.apiBaseUrl.replace(/\/+$/, "")}/api/v1/auth/login`;
}

export interface AuthSession {
  accessToken: string;
  refreshToken: string;
}

export interface AuthContextValue {
  /** True once a non-expired access token is present. */
  isAuthenticated: boolean;
  /** Decoded (UNVERIFIED) claims of the current access token, or null. */
  claims: AccessTokenClaims | null;
  /** Lazy accessor passed to the API client's `getToken`. */
  getToken: () => string | null;
  /**
   * The current refresh token, for callers that need to re-mint the ACCESS token
   * mid-session — roles are baked into the access JWT at issue time, so after an
   * action that grants a role (e.g. installing the bot into a new server, which
   * makes the installer a `guild_admin`) the old token still reads `roles: []`
   * until it is rotated. See `screens/servers/ServersScreen`.
   */
  getRefreshToken: () => string | null;
  /** Start Discord OAuth by navigating the browser to the backend. */
  login: () => void;
  /** Install a session (used by the OAuth callback handler). */
  setSession: (session: AuthSession) => void;
  /** Clear the in-memory session. */
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export interface AuthProviderProps {
  children: ReactNode;
  /**
   * Optional initial session — primarily for tests so a provider can render an
   * already-authenticated tree without going through the redirect dance.
   */
  initialSession?: AuthSession | null;
  /** Injectable redirect for tests (defaults to a real `window.location` nav). */
  redirect?: (url: string) => void;
}

export function AuthProvider({
  children,
  initialSession = null,
  redirect,
}: AuthProviderProps): JSX.Element {
  const [session, setSessionState] = useState<AuthSession | null>(initialSession);

  // Keep a ref in sync so `getToken` is a stable identity yet always reads the
  // latest token (the API client calls it lazily on every request).
  const sessionRef = useRef<AuthSession | null>(initialSession);
  sessionRef.current = session;

  const setSession = useCallback((next: AuthSession) => {
    setSessionState(next);
  }, []);

  const logout = useCallback(() => {
    setSessionState(null);
  }, []);

  const login = useCallback(() => {
    const url = backendLoginUrl();
    if (redirect) {
      redirect(url);
    } else {
      window.location.assign(url);
    }
  }, [redirect]);

  const getToken = useCallback((): string | null => {
    return sessionRef.current?.accessToken ?? null;
  }, []);

  const getRefreshToken = useCallback((): string | null => {
    return sessionRef.current?.refreshToken ?? null;
  }, []);

  const claims = useMemo(
    () => decodeAccessToken(session?.accessToken),
    [session?.accessToken],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      isAuthenticated: Boolean(session?.accessToken),
      claims,
      getToken,
      getRefreshToken,
      login,
      setSession,
      logout,
    }),
    [session?.accessToken, claims, getToken, getRefreshToken, login, setSession, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Access the auth context. Throws when used outside an AuthProvider. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an <AuthProvider>");
  }
  return ctx;
}

/** Convert a backend {@link TokenPair} into the in-memory session shape. */
export function sessionFromTokenPair(pair: TokenPair): AuthSession {
  return { accessToken: pair.access_token, refreshToken: pair.refresh_token };
}
