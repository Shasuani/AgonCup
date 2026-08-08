// Status pill. Separates the two jobs a status string was doing everywhere:
// the RAW enum value drives the color family (global.css keys the amber/blue/
// green/red palettes off [data-status~="…"]), while the HUMAN label is what the
// user reads. Screens used to render `{t.status}` directly, so admins saw
// "check_in" and "in_progress" in the UI.
//
// Unknown values still render: `label()` falls back to a humanized form of the
// raw string, and the badge keeps the neutral default palette.

import { label as lookup } from "../lib/labels";

export interface StatusBadgeProps {
  /** The raw enum value from the API. Drives both color and the fallback text. */
  status: string;
  /**
   * Label map to read the display text from (e.g. `TOURNAMENT_STATUS_LABELS`).
   * Omit to show the humanized raw value.
   */
  labels?: Readonly<Record<string, string>>;
  /** Optional override for the visible text (rare — prefer a label map). */
  children?: string;
}

export function StatusBadge({ status, labels, children }: StatusBadgeProps): JSX.Element {
  const text = children ?? (labels ? lookup(labels, status) : lookup({}, status));
  return (
    <span data-status-badge data-status={status}>
      {text}
    </span>
  );
}
