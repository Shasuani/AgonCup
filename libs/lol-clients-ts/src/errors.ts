/**
 * Typed transport error + a narrowing type guard.
 *
 * Every non-2xx response from the backend is surfaced as an {@link ApiError}
 * carrying the HTTP `status` and the parsed FastAPI `detail` (when present).
 */

/** Shape of a FastAPI error body: `{ "detail": ... }`. */
export interface ApiErrorBody {
  detail?: unknown;
}

/**
 * Thrown by every client method on a non-2xx response.
 *
 * `detail` mirrors FastAPI's `detail` field (a string for most routes, or the
 * 422 validation-error array). `body` retains the full parsed payload for
 * callers that need more than `detail`.
 */
export class ApiError extends Error {
  /** Discriminator used by {@link isApiError} for cross-realm-safe narrowing. */
  readonly __isApiError = true as const;

  /** HTTP status code of the failed response. */
  readonly status: number;

  /** Parsed FastAPI `detail` (string, validation array, or undefined). */
  readonly detail: unknown;

  /** The full parsed response body, when JSON-decodable. */
  readonly body: unknown;

  constructor(status: number, detail: unknown, body?: unknown) {
    super(
      typeof detail === 'string' && detail.length > 0
        ? detail
        : `Request failed with status ${status}`,
    );
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.body = body ?? null;
    // Restore the prototype chain (downleveled class extends Error).
    Object.setPrototypeOf(this, ApiError.prototype);
  }
}

/**
 * Type guard for {@link ApiError}. Uses a branded property so it stays correct
 * even when the error crosses module/realm boundaries (where `instanceof` can
 * fail). Use this in `catch` blocks to read `status`/`detail` safely.
 */
export function isApiError(err: unknown): err is ApiError {
  return (
    typeof err === 'object' &&
    err !== null &&
    (err as { __isApiError?: unknown }).__isApiError === true
  );
}
