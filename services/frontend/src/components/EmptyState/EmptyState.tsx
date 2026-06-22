// EmptyState / ErrorState — presentational placeholders for "no data" and
// "load failed" surfaces. Each centers an illustration (the shipped Cobalt
// Slate SVGs), a title, an optional description, and an optional action slot
// (e.g. a "Create tournament" Button or a "Retry" Button passed by the caller).
//
// Integrity: these are chrome only — they never fabricate data. The illustration
// is decorative (the visible title carries the meaning), so the imported SVG is
// rendered via an <img alt=""> and hidden from assistive tech; the text content
// is what AT reads.
//
// Accessibility:
//  - EmptyState root is a <section> labelled by its own title (aria-labelledby).
//  - ErrorState root uses role="alert" so screen readers announce the failure
//    when it appears (matching the ErrorBanner pattern in this codebase).

import type { ReactNode } from "react";

import "./EmptyState.css";
import emptyStateArt from "../../assets/empty-state.svg";
import errorStateArt from "../../assets/error-state.svg";

export interface EmptyStateProps {
  /** Headline, e.g. "No tournaments yet". Required — carries the meaning. */
  title: string;
  /** Optional supporting copy. */
  description?: ReactNode;
  /** Optional action node (typically a <Button>). Rendered below the copy. */
  action?: ReactNode;
  /**
   * Override the illustration source. Defaults to the shipped empty-state art.
   * Pass undefined-friendly: any imported SVG URL string.
   */
  illustrationSrc?: string;
}

let emptyStateSeq = 0;

function nextId(prefix: string): string {
  emptyStateSeq += 1;
  return `${prefix}-${emptyStateSeq}`;
}

export function EmptyState({
  title,
  description,
  action,
  illustrationSrc = emptyStateArt,
}: EmptyStateProps): JSX.Element {
  const titleId = nextId("lol-empty-title");
  return (
    <section
      data-empty-state
      aria-labelledby={titleId}
      className="lol-empty-state"
    >
      <img
        src={illustrationSrc}
        alt=""
        aria-hidden="true"
        className="lol-empty-state-art"
        data-empty-state-art
      />
      <h3 id={titleId} className="lol-empty-state-title">
        {title}
      </h3>
      {description !== undefined && description !== null ? (
        <p className="lol-empty-state-desc">{description}</p>
      ) : null}
      {action ? (
        <div className="lol-empty-state-action" data-empty-state-action>
          {action}
        </div>
      ) : null}
    </section>
  );
}

export interface ErrorStateProps {
  /** Headline, e.g. "Couldn't load tournaments". */
  title?: string;
  /** Optional supporting copy (e.g. errorMessage(error)). */
  description?: ReactNode;
  /** Optional action node (typically a "Retry" <Button>). */
  action?: ReactNode;
  /** Override the illustration source. Defaults to the shipped error-state art. */
  illustrationSrc?: string;
}

export function ErrorState({
  title = "Something went wrong",
  description,
  action,
  illustrationSrc = errorStateArt,
}: ErrorStateProps): JSX.Element {
  return (
    <section
      data-error-state
      role="alert"
      className="lol-empty-state lol-error-state"
    >
      <img
        src={illustrationSrc}
        alt=""
        aria-hidden="true"
        className="lol-empty-state-art"
        data-empty-state-art
      />
      <h3 className="lol-empty-state-title">{title}</h3>
      {description !== undefined && description !== null ? (
        <p className="lol-empty-state-desc">{description}</p>
      ) : null}
      {action ? (
        <div className="lol-empty-state-action" data-empty-state-action>
          {action}
        </div>
      ) : null}
    </section>
  );
}
