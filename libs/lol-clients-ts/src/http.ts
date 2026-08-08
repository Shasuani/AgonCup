/**
 * Dependency-light, fetch-based transport core.
 *
 * Frameworks-agnostic (no React, no axios). Uses the global `fetch` so it runs
 * unchanged in the browser, the desktop renderer, and Node >= 22. The client
 * factory ({@link ./client}) layers typed resource methods on top of this.
 */

import { ApiError } from './errors.js';

/** Mounted under `/api/v1` by the backend app factory (main.py). */
export const API_BASE_PATH = '/api/v1';

/**
 * Config for {@link createClient}. Token + acting-guild are pulled lazily on
 * every request so the client picks up refreshes without being rebuilt.
 */
export interface ClientConfig {
  /** Backend origin, e.g. `https://api.example.com` (no trailing slash needed). */
  baseUrl: string;
  /**
   * Returns the current access JWT, or null/undefined when unauthenticated.
   * May be sync or async. Used as `Authorization: Bearer <token>`.
   */
  getToken?: () => string | null | undefined | Promise<string | null | undefined>;
  /**
   * Returns the acting guild id, or null/undefined for guild-less calls.
   * May be sync or async. Used as the `X-Guild-Id` tenant header.
   */
  getActingGuild?: () => string | null | undefined | Promise<string | null | undefined>;
  /**
   * Injectable fetch (defaults to global `fetch`). Useful for tests and for
   * non-browser hosts that polyfill fetch.
   */
  fetch?: typeof fetch;
}

/** Per-request options handled by {@link RequestRunner}. */
export interface RequestOptions {
  method: 'GET' | 'POST' | 'DELETE' | 'PUT' | 'PATCH';
  /** Path under the API base, e.g. `/tournaments/{id}`. Already encoded. */
  path: string;
  /** JSON request body (serialized when present). */
  body?: unknown;
  /**
   * Whether to attach the `Authorization` header (default true). `/auth/*`
   * routes that issue tokens pass false.
   */
  auth?: boolean;
  /**
   * Whether to attach the `X-Guild-Id` tenant header when an acting guild is
   * available (default true). Global routes (auth, accounts) pass false.
   */
  tenant?: boolean;
}

/** Joins the base URL and API path without duplicate slashes. */
function buildUrl(baseUrl: string, path: string): string {
  const trimmedBase = baseUrl.replace(/\/+$/, '');
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${trimmedBase}${API_BASE_PATH}${normalizedPath}`;
}

async function resolveMaybeAsync<T>(
  fn: (() => T | Promise<T>) | undefined,
): Promise<T | undefined> {
  if (!fn) return undefined;
  return await fn();
}

/**
 * Bound request executor. Returns parsed JSON typed as `T`, or throws
 * {@link ApiError} on any non-2xx response. `204 No Content` resolves to
 * `undefined` (cast to `T` for void-returning routes).
 */
export type RequestRunner = <T>(opts: RequestOptions) => Promise<T>;

/** Builds the {@link RequestRunner} bound to a {@link ClientConfig}. */
export function createRequestRunner(config: ClientConfig): RequestRunner {
  const doFetch = config.fetch ?? globalThis.fetch;
  if (typeof doFetch !== 'function') {
    throw new Error('createClient: no global fetch available; pass config.fetch');
  }

  return async function request<T>(opts: RequestOptions): Promise<T> {
    const url = buildUrl(config.baseUrl, opts.path);
    const headers: Record<string, string> = { Accept: 'application/json' };

    if (opts.body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }

    if (opts.auth !== false) {
      const token = await resolveMaybeAsync(config.getToken);
      if (token) headers['Authorization'] = `Bearer ${token}`;
    }

    if (opts.tenant !== false) {
      const guild = await resolveMaybeAsync(config.getActingGuild);
      if (guild) headers['X-Guild-Id'] = guild;
    }

    // `same-origin` so the browser attaches the HttpOnly session cookie that
    // carries the refresh token — /auth/refresh and /auth/logout read it, and
    // without this the SPA could not restore a session after a reload. It is not
    // the default: `fetch` omits credentials unless asked, even same-origin.
    const init: RequestInit = { method: opts.method, headers, credentials: 'same-origin' };
    if (opts.body !== undefined) {
      init.body = JSON.stringify(opts.body);
    }

    const res = await doFetch(url, init);

    if (res.status === 204) {
      return undefined as T;
    }

    const text = await res.text();
    let parsed: unknown = undefined;
    if (text.length > 0) {
      try {
        parsed = JSON.parse(text);
      } catch {
        // Non-JSON body (e.g. an HTML gateway error). Keep raw text as detail.
        if (!res.ok) throw new ApiError(res.status, text, text);
        parsed = undefined;
      }
    }

    if (!res.ok) {
      const detail =
        parsed !== null && typeof parsed === 'object' && 'detail' in parsed
          ? (parsed as { detail: unknown }).detail
          : parsed;
      throw new ApiError(res.status, detail, parsed);
    }

    return parsed as T;
  };
}
