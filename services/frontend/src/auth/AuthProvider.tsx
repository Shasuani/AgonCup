// Auth context for the admin SPA.
//
// The SHORT-LIVED access token lives in React state; the long-lived refresh
// token is never in JavaScript at all. The backend puts it in an HttpOnly,
// SameSite session cookie (a signed JWT — see app/auth/session_cookie.py), so an
// injected script cannot read it, and the browser attaches it automatically to
// /auth/refresh and /auth/logout.
//
// That cookie is also what makes a session survive a page reload. The provider
// used to hold BOTH tokens in React state and restore nothing on mount, so every
// refresh — every new tab, every restart — logged the user out and sent them
// back through Discord. On mount we now attempt one silent refresh: if the
// cookie is good the session is restored, otherwise the login screen appears.
//
// `logout` calls the backend so the refresh token is REVOKED and the cookie
// cleared. Clearing React state alone (what it used to do) left the session
// alive server-side until it expired — invisible while the token died with the
// tab, but a real hole now that the cookie outlives it.
//
// That mount-time refresh MUST happen at most once per page load — see
// `silentRefresh` below for why firing it twice logs the user straight back out.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { AccessTokenClaims, TokenPair } from "@lol/clients-ts";
import { decodeAccessToken } from "./jwt";
import { config } from "../config";

/** Absolute URL of an `/api/v1/auth/*` endpoint. */
function authEndpoint(path: string): string {
  return `${config.apiBaseUrl.replace(/\/+$/, "")}/api/v1/auth/${path}`;
}

/** The backend `/auth/login` URL the browser is redirected to. */
function backendLoginUrl(): string {
  return authEndpoint("login");
}

/**
 * The in-flight cold-start refresh, shared by every caller in this document.
 *
 * `/auth/refresh` ROTATES: the presented token is revoked and a successor
 * issued, and re-presenting a revoked token is read as theft, which burns the
 * whole token family (see `rotate_refresh_token` in app/auth/tokens.py). Two
 * concurrent boot refreshes both send the SAME cookie — the second one arrives
 * before the first's `Set-Cookie` lands — so the second is indistinguishable
 * from a replay and revokes the session that was just restored.
 *
 * `React.StrictMode` double-invokes effects in development, so without this
 * guard EVERY page reload signed the user out and bounced them back through
 * Discord. Module-level (not a ref) because StrictMode's second mount gets fresh
 * refs, and because a second provider anywhere in the tree must join the same
 * request rather than start a competing one.
 */
let inFlightRestore: Promise<TokenPair | null> | null = null;

/** POST /auth/refresh at most once concurrently. `null` = no usable cookie. */
function silentRefresh(url: string): Promise<TokenPair | null> {
  if (inFlightRestore) return inFlightRestore;
  const attempt = (async (): Promise<TokenPair | null> => {
    try {
      const res = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      return res.ok ? ((await res.json()) as TokenPair) : null;
    } catch {
      // Offline / backend down: fall through to the login screen.
      return null;
    }
  })();
  inFlightRestore = attempt;
  // Released only once settled, and only if nothing newer claimed the slot. The
  // reset must not run inside the async body: were `fetch` to throw
  // synchronously the body would finish before the assignment above and cache a
  // settled promise forever, so every later mount would replay that one failure.
  const release = (): void => {
    if (inFlightRestore === attempt) inFlightRestore = null;
  };
  void attempt.then(release, release);
  return attempt;
}

export interface AuthSession {
  accessToken: string;
  /** Legacy/desktop-shaped field. The browser no longer receives one — the token
   *  is in the HttpOnly cookie — so this is absent in the SPA. */
  refreshToken?: string;
}

export interface AuthContextValue {
  /** True once a non-expired access token is present. */
  isAuthenticated: boolean;
  /** True while the mount-time silent refresh is still in flight. Render a
   *  spinner rather than the login screen, or a returning user sees a flash of
   *  "signed out" before their session is restored. */
  isRestoring: boolean;
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
  // Tests hand us a session directly; only a real cold start needs restoring.
  const [isRestoring, setIsRestoring] = useState(initialSession === null);

  // Keep a ref in sync so `getToken` is a stable identity yet always reads the
  // latest token (the API client calls it lazily on every request).
  const sessionRef = useRef<AuthSession | null>(initialSession);
  sessionRef.current = session;

  const setSession = useCallback((next: AuthSession) => {
    setSessionState(next);
    setIsRestoring(false);
  }, []);

  // Cold start: ask the backend to rotate the session cookie into a fresh access
  // token. 401 simply means "no usable cookie" — show the login screen.
  useEffect(() => {
    if (initialSession !== null) return;
    let cancelled = false;
    void silentRefresh(authEndpoint("refresh")).then((pair) => {
      if (cancelled) return;
      if (pair) setSessionState({ accessToken: pair.access_token });
      setIsRestoring(false);
    });
    return () => {
      cancelled = true;
    };
  }, [initialSession]);

  const logout = useCallback(() => {
    // Revoke server-side and drop the cookie, then clear locally regardless: a
    // failed call must not strand the user in a session they asked to leave.
    void fetch(authEndpoint("logout"), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).catch(() => undefined);
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
      isRestoring,
      claims,
      getToken,
      getRefreshToken,
      login,
      setSession,
      logout,
    }),
    [session?.accessToken, isRestoring, claims, getToken, getRefreshToken, login, setSession, logout],
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

/** Convert a backend {@link TokenPair} into the in-memory session shape.
 *
 * The refresh token is intentionally dropped: the browser's copy is the HttpOnly
 * cookie the same response set, and keeping a second copy in JS would hand XSS
 * exactly what the cookie exists to protect. */
export function sessionFromTokenPair(pair: TokenPair): AuthSession {
  return { accessToken: pair.access_token };
}
