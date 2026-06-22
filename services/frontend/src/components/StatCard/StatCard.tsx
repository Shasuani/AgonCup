// Dashboard KPI cards. Pure presentation: a StatCard shows a single metric
// (icon + label + value, with optional delta/trend and deep-link), and a
// StatCardGrid lays cards out responsively.
//
// INTEGRITY: this component never fetches. It only DISPLAYS values handed to it
// by the caller (the Integrator wires real API counts here). It supports three
// honest states so a metric without a real source is never faked:
//   - loading   -> shimmer skeleton (no number shown)
//   - value     -> the real number/string
//   - "unavailable" (value === null/undefined while not loading) -> renders the
//     neutral em-dash placeholder "—" plus an aria description, NEVER a guess.
//
// Framework-light: plain React + inline SVG <Icon>; no UI/chart/icon library.
// Styling is token-driven via the co-located StatCard.css. Tokens come from the
// Cobalt Slate :root in styles/global.css (var(--surface), var(--border), ...).

import { useId } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Icon } from "../Icon";
import type { IconName } from "../Icon";

import "./StatCard.css";

/** Direction of a delta/trend chip. "neutral" shows no color emphasis. */
export type StatTrend = "up" | "down" | "neutral";

/**
 * Optional delta chip describing change over time. Purely cosmetic — pass it
 * only when the caller has a REAL comparison value; never synthesize one.
 */
export interface StatDelta {
  /** Rendered text, e.g. "+3", "12%", "−2". Caller formats it. */
  label: string;
  /**
   * Trend direction. Controls accent color + an arrow glyph. Color is not the
   * sole signal: the arrow glyph and the label text also convey direction, so
   * the chip is intelligible without color (AA / colorblind safe).
   */
  trend?: StatTrend;
}

export interface StatCardProps {
  /** Short metric name, e.g. "Tournaments". Becomes the card's accessible name. */
  label: string;
  /**
   * The metric value to display. Pass a number/string when a REAL source exists.
   * Pass null/undefined to render the neutral "—" unavailable state (used when a
   * metric has no backing endpoint or a best-effort fetch failed/404'd).
   */
  value?: number | string | null;
  /** Glyph shown beside the label (from the shared inline icon set). */
  icon?: IconName;
  /** Optional secondary line under the value, e.g. "3 active". Omit if unknown. */
  sublabel?: ReactNode;
  /** Optional change chip. Render only with a genuine comparison value. */
  delta?: StatDelta;
  /**
   * When true, show the shimmer skeleton instead of the value. While loading the
   * value is intentionally hidden so no stale/placeholder number is implied.
   */
  loading?: boolean;
  /**
   * Optional in-app deep link (react-router path). When set the whole card is a
   * <Link>; otherwise it is a non-interactive <div>. External/absolute hrefs are
   * not supported here (admin SPA navigates within the router).
   */
  href?: string;
  /**
   * Overrides the placeholder shown in the unavailable state. Defaults to "—".
   * Kept configurable but ALWAYS neutral — do not pass invented data.
   */
  unavailablePlaceholder?: string;
  /**
   * Optional short note explaining WHY a value is unavailable (e.g. "No
   * endpoint"). Shown muted under the placeholder and announced to AT. Only
   * meaningful in the unavailable state.
   */
  unavailableNote?: ReactNode;
}

const TREND_GLYPH: Record<StatTrend, string> = {
  up: "↑", // ↑
  down: "↓", // ↓
  neutral: "→", // →
};

/**
 * A single KPI card. Display-only; see {@link StatCardProps} for the loading /
 * value / unavailable contract.
 */
export function StatCard({
  label,
  value,
  icon,
  sublabel,
  delta,
  loading = false,
  href,
  unavailablePlaceholder = "—", // em dash
  unavailableNote,
}: StatCardProps): JSX.Element {
  const labelId = useId();
  const valueId = useId();

  // A metric is "unavailable" only when it is NOT loading and has no real value.
  // Empty string is treated as unavailable too (no honest value to show).
  const isUnavailable =
    !loading && (value === null || value === undefined || value === "");

  const displayValue: ReactNode = loading
    ? null
    : isUnavailable
      ? unavailablePlaceholder
      : value;

  const body = (
    <>
      <div data-statcard-top>
        <span data-statcard-label id={labelId}>
          {label}
        </span>
        {icon ? (
          <span data-statcard-icon aria-hidden="true">
            <Icon name={icon} size={18} />
          </span>
        ) : null}
      </div>

      <div data-statcard-value-row>
        {loading ? (
          // Shimmer stands in for the value; role=status keeps it announced.
          <span
            data-statcard-skeleton
            role="status"
            aria-live="polite"
            aria-label={`Loading ${label}`}
          />
        ) : (
          <strong
            data-statcard-value
            id={valueId}
            data-unavailable={isUnavailable || undefined}
          >
            {displayValue}
          </strong>
        )}

        {!loading && delta ? (
          <span
            data-statcard-delta
            data-trend={delta.trend ?? "neutral"}
          >
            <span aria-hidden="true">{TREND_GLYPH[delta.trend ?? "neutral"]}</span>
            {delta.label}
          </span>
        ) : null}
      </div>

      {!loading && isUnavailable && unavailableNote ? (
        <span data-statcard-sublabel data-muted>
          {unavailableNote}
        </span>
      ) : !loading && sublabel ? (
        <span data-statcard-sublabel>{sublabel}</span>
      ) : null}
    </>
  );

  // The accessible name combines the metric label with its value (or a spoken
  // "unavailable" for the placeholder) so the card reads as one coherent stat.
  const spokenValue = loading
    ? "loading"
    : isUnavailable
      ? "unavailable"
      : String(value);
  const ariaLabel = `${label}: ${spokenValue}`;

  if (href !== undefined) {
    return (
      <Link
        to={href}
        data-statcard
        data-statcard-link
        aria-label={ariaLabel}
      >
        {body}
      </Link>
    );
  }

  return (
    <div
      data-statcard
      role="group"
      aria-labelledby={labelId}
      aria-label={ariaLabel}
    >
      {body}
    </div>
  );
}

export interface StatCardGridProps {
  /** The StatCard elements (or any nodes) to lay out. */
  children: ReactNode;
  /**
   * Accessible label for the grouping landmark, e.g. "Key metrics". When
   * provided the grid is exposed as a labelled group to assistive tech.
   */
  label?: string;
}

/**
 * Responsive grid wrapper for KPI cards. Auto-fills columns down to a single
 * column on narrow viewports (see StatCard.css). Renders an aria group when a
 * label is supplied so the metric set is navigable as a unit.
 */
export function StatCardGrid({
  children,
  label,
}: StatCardGridProps): JSX.Element {
  return (
    <div
      data-statcard-grid
      role={label ? "group" : undefined}
      aria-label={label}
    >
      {children}
    </div>
  );
}
