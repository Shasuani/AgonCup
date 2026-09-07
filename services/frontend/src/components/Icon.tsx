// Inline 24x24 line-icon set. Every glyph is drawn on a 24x24 viewBox with
// stroke="currentColor" so it inherits the surrounding text color (works in any
// of the Cobalt Slate surfaces without per-icon color overrides). No icon
// library — plain inline SVG keeps the bundle framework-light.
//
// Accessibility: icons are decorative by default (aria-hidden + focusable=false).
// Pass `title` to promote an icon to a labelled image (role="img" + the title is
// linked via aria-labelledby for AT). See global.css :focus-visible for rings on
// any interactive ancestor.

import { useId } from "react";
import type { SVGProps } from "react";

import "./Icon.css";

/** Canonical icon names. Keep in sync with PATHS below. */
export type IconName =
  | "dashboard"
  | "tournaments"
  | "teams"
  | "accounts"
  | "disputes"
  | "audit"
  | "search"
  | "command"
  | "sun"
  | "moon"
  | "menu"
  | "close"
  | "chevron-right"
  | "logout"
  | "bell"
  | "plus"
  | "check"
  | "alert"
  | "download";

export interface IconProps
  extends Omit<SVGProps<SVGSVGElement>, "name" | "children"> {
  /** Which glyph to render. */
  name: IconName;
  /** Edge length in px (square). Defaults to 24 to match the native viewBox. */
  size?: number;
  /**
   * Optional accessible name. When provided the icon becomes a labelled image
   * (role="img"); when omitted it is hidden from assistive tech (decorative).
   */
  title?: string;
}

// Path geometry per icon. Each entry is the inner markup of a 24x24 line icon.
// Strokes are 1.5px, rounded joins/caps — crisp on the 24-grid. Authored as a
// render function so each call gets its own elements (no shared node identity).
const PATHS: Record<IconName, () => JSX.Element> = {
  // Grid of four panels.
  dashboard: () => (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
    </>
  ),
  // Trophy.
  tournaments: () => (
    <>
      <path d="M7 4h10v4a5 5 0 0 1-10 0V4Z" />
      <path d="M7 5H4v2a3 3 0 0 0 3 3" />
      <path d="M17 5h3v2a3 3 0 0 1-3 3" />
      <path d="M12 13v4" />
      <path d="M8.5 21h7" />
      <path d="M9.5 21a2.5 2.5 0 0 1 5 0" />
    </>
  ),
  // Two people (users).
  teams: () => (
    <>
      <circle cx="9" cy="8" r="3.25" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
      <path d="M16 5.5a3.25 3.25 0 0 1 0 6.4" />
      <path d="M17 14.6a5.5 5.5 0 0 1 3.5 5.4" />
    </>
  ),
  // ID badge.
  accounts: () => (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2.5" />
      <circle cx="12" cy="10" r="2.5" />
      <path d="M8 17a4 4 0 0 1 8 0" />
      <path d="M10 6h4" />
    </>
  ),
  // Gavel.
  disputes: () => (
    <>
      <path d="m13.5 6.5 4 4" />
      <path d="m7 13 4 4" />
      <path d="M15.5 4.5 19.5 8.5 16 12 12 8z" />
      <path d="M9.5 10.5 4 16l4 4 5.5-5.5" />
      <path d="M14 20.5h7" />
    </>
  ),
  // Scroll / list.
  audit: () => (
    <>
      <path d="M6 3h11a2 2 0 0 1 2 2v12a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V5" />
      <path d="M4 5a2 2 0 0 1 4 0v2H4" />
      <path d="M9 9h7" />
      <path d="M9 13h7" />
      <path d="M9 17h4" />
    </>
  ),
  // Magnifier.
  search: () => (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m20 20-4.8-4.8" />
    </>
  ),
  // Command (⌘) key.
  command: () => (
    <path d="M9 9V7.5a2.5 2.5 0 1 0-2.5 2.5H9Zm0 0v6m0-6h6m-6 6H7.5A2.5 2.5 0 1 0 10 17.5V15Zm6 0h1.5A2.5 2.5 0 1 0 15 17.5V15Zm0 0V9m0 0h1.5A2.5 2.5 0 1 0 15 6.5V9Z" />
  ),
  // Sun.
  sun: () => (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2.5" />
      <path d="M12 19.5V22" />
      <path d="M2 12h2.5" />
      <path d="M19.5 12H22" />
      <path d="m4.9 4.9 1.8 1.8" />
      <path d="m17.3 17.3 1.8 1.8" />
      <path d="m19.1 4.9-1.8 1.8" />
      <path d="m6.7 17.3-1.8 1.8" />
    </>
  ),
  // Moon (crescent).
  moon: () => <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" />,
  // Hamburger menu.
  menu: () => (
    <>
      <path d="M4 7h16" />
      <path d="M4 12h16" />
      <path d="M4 17h16" />
    </>
  ),
  // X close.
  close: () => (
    <>
      <path d="m6 6 12 12" />
      <path d="m18 6-12 12" />
    </>
  ),
  // Right chevron.
  "chevron-right": () => <path d="m9 5 7 7-7 7" />,
  // Logout (door + arrow out).
  logout: () => (
    <>
      <path d="M14 4H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h7" />
      <path d="M16 8.5 19.5 12 16 15.5" />
      <path d="M19.5 12H10" />
    </>
  ),
  // Notification bell.
  bell: () => (
    <>
      <path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z" />
      <path d="M10 19a2 2 0 0 0 4 0" />
    </>
  ),
  // Plus.
  plus: () => (
    <>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>
  ),
  // Checkmark.
  check: () => <path d="m5 12.5 4.5 4.5L19 6.5" />,
  // Alert triangle.
  alert: () => (
    <>
      <path d="M12 4 2.5 20.5h19L12 4Z" />
      <path d="M12 10v4" />
      <path d="M12 17.5h.01" />
    </>
  ),
  // Arrow into a tray.
  download: () => (
    <>
      <path d="M12 4v11" />
      <path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
      <path d="M4 16.5v2A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5v-2" />
    </>
  ),
};

export function Icon({
  name,
  size = 24,
  title,
  ...rest
}: IconProps): JSX.Element {
  // useId gives a stable, collision-free id for aria-labelledby even when many
  // labelled icons render on one page.
  const titleId = useId();
  const labelled = title !== undefined && title !== "";

  return (
    <svg
      data-icon={name}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={labelled ? "img" : undefined}
      aria-hidden={labelled ? undefined : true}
      aria-labelledby={labelled ? titleId : undefined}
      focusable={false}
      {...rest}
    >
      {labelled ? <title id={titleId}>{title}</title> : null}
      {PATHS[name]()}
    </svg>
  );
}
