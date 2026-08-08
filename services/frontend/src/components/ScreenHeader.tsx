// The one screen header. Every routed screen opens with a title, an optional
// one-line explanation of what the screen is for, and optional actions
// (Refresh, Create, …) aligned to the right.
//
// This existed as three different shapes before: `<div data-screen-header>` with
// a nested wrapper, `<header data-screen-header>` with the intro floating BELOW
// it, and a bare `<h1>`. They aligned differently and only some had an intro, so
// the app's top-of-page rhythm changed as you moved between tabs.
//
// The `id` is required because screens label their landmark with it
// (`<section aria-labelledby={id}>`) — the heading is the accessible name of
// the whole screen, so it must stay addressable.

import type { ReactNode } from "react";

export interface ScreenHeaderProps {
  /** Id for the <h1>; the owning <section> references it via aria-labelledby. */
  id: string;
  title: ReactNode;
  /** One-line explanation of the screen. Optional but strongly encouraged. */
  intro?: ReactNode;
  /** Right-aligned action slot, typically <Button>s. */
  actions?: ReactNode;
}

export function ScreenHeader({
  id,
  title,
  intro,
  actions,
}: ScreenHeaderProps): JSX.Element {
  return (
    <div data-screen-header>
      <div data-screen-heading>
        <h1 id={id}>{title}</h1>
        {intro !== undefined && intro !== null ? (
          <p data-screen-intro>{intro}</p>
        ) : null}
      </div>
      {actions !== undefined && actions !== null ? (
        <div data-screen-actions>{actions}</div>
      ) : null}
    </div>
  );
}

export interface SectionHeaderProps {
  /** Id for the <h2>; the owning <section> references it via aria-labelledby. */
  id: string;
  title: ReactNode;
  /** Optional supporting line under the title. */
  intro?: ReactNode;
  /** Right-aligned action slot. */
  actions?: ReactNode;
}

/**
 * The same shape one level down, for sections WITHIN a screen (a detail panel,
 * a "Danger zone", an entrants list). Renders an <h2> instead of an <h1> so a
 * screen keeps exactly one top-level heading.
 */
export function SectionHeader({
  id,
  title,
  intro,
  actions,
}: SectionHeaderProps): JSX.Element {
  return (
    <div data-screen-header data-section-header>
      <div data-screen-heading>
        <h2 id={id}>{title}</h2>
        {intro !== undefined && intro !== null ? (
          <p data-screen-intro>{intro}</p>
        ) : null}
      </div>
      {actions !== undefined && actions !== null ? (
        <div data-screen-actions>{actions}</div>
      ) : null}
    </div>
  );
}
