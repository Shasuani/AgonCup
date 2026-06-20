// Error banner with role=alert. Accepts an Error (incl. ApiError) or a string.
// Server-provided messages are rendered as TEXT (React escapes) — never HTML.

import { isApiError } from "@lol/clients-ts";

export interface ErrorBannerProps {
  error: unknown;
  /** Optional retry affordance. */
  onRetry?: () => void;
}

/** Best-effort human-readable message from an unknown thrown value. */
export function errorMessage(error: unknown): string {
  if (error == null) return "";
  if (isApiError(error)) {
    if (typeof error.detail === "string" && error.detail.length > 0) {
      return error.detail;
    }
    return `Request failed (HTTP ${error.status}).`;
  }
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "An unexpected error occurred.";
}

export function ErrorBanner({ error, onRetry }: ErrorBannerProps): JSX.Element | null {
  if (error == null) return null;
  return (
    <div role="alert" data-error-banner>
      <span>{errorMessage(error)}</span>
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}
