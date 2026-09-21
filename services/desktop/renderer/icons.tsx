// Inline SVG icon set for the desktop renderer (24-grid, 1.6px strokes,
// currentColor). Replaces the Unicode glyphs (◆ ▸ ❖ ⬡ ⬢) the nav used to draw,
// which rendered at different sizes per platform font and could not be tinted
// as a group. Decorative by default (aria-hidden); pass `title` for a labelled
// image.
import React from 'react';

export type IconName =
  | 'home' | 'swords' | 'trophy' | 'users' | 'id' | 'check' | 'chevron' | 'copy'
  | 'logout' | 'link' | 'alert' | 'info' | 'discord' | 'bolt' | 'shield' | 'clock'
  | 'x' | 'plus' | 'amphora' | 'crown' | 'map' | 'refresh' | 'eye';

const PATHS: Record<IconName, React.ReactNode> = {
  home: <><path d="M4 11.5 12 4l8 7.5" /><path d="M6.5 10v10h11V10" /><path d="M10 20v-5h4v5" /></>,
  swords: <><path d="m4 4 6 6" /><path d="M4 4h3.5L14 10.5" /><path d="m20 4-6 6" /><path d="M20 4h-3.5L10 10.5" /><path d="m7 13 4 4-3 3-4-4z" /><path d="m17 13-4 4 3 3 4-4z" /><path d="m10 17 4-4" /></>,
  trophy: <><path d="M7 4h10v4a5 5 0 0 1-10 0V4Z" /><path d="M7 5H4v2a3 3 0 0 0 3 3" /><path d="M17 5h3v2a3 3 0 0 1-3 3" /><path d="M12 13v4" /><path d="M8.5 21h7" /><path d="M9.5 21a2.5 2.5 0 0 1 5 0" /></>,
  users: <><circle cx="9" cy="8" r="3.25" /><path d="M3.5 20a5.5 5.5 0 0 1 11 0" /><path d="M16 5.5a3.25 3.25 0 0 1 0 6.4" /><path d="M17 14.6a5.5 5.5 0 0 1 3.5 5.4" /></>,
  id: <><rect x="4" y="3" width="16" height="18" rx="2.5" /><circle cx="12" cy="10" r="2.5" /><path d="M8 17a4 4 0 0 1 8 0" /><path d="M10 6h4" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 6.5" />,
  chevron: <path d="m9 5 7 7-7 7" />,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h9" /></>,
  logout: <><path d="M14 4H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h7" /><path d="M16 8.5 19.5 12 16 15.5" /><path d="M19.5 12H10" /></>,
  link: <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></>,
  alert: <><path d="M12 4 2.5 20.5h19L12 4Z" /><path d="M12 10v4" /><path d="M12 17.5h.01" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><path d="M12 8h.01" /></>,
  discord: <path d="M18.9 6.3A15 15 0 0 0 15.2 5l-.5.9a14 14 0 0 0-5.4 0L8.8 5a15 15 0 0 0-3.7 1.3C2.8 9.8 2.2 13.2 2.5 16.5a15 15 0 0 0 4.6 2.3l1-1.5a9 9 0 0 1-1.5-.7l.4-.3a10.6 10.6 0 0 0 10 0l.4.3-1.5.7 1 1.5a15 15 0 0 0 4.6-2.3c.4-3.8-.6-7.2-2.6-10.2ZM9.2 14.5c-.9 0-1.6-.8-1.6-1.8s.7-1.8 1.6-1.8 1.7.8 1.6 1.8c0 1-.7 1.8-1.6 1.8Zm5.6 0c-.9 0-1.6-.8-1.6-1.8s.7-1.8 1.6-1.8 1.7.8 1.6 1.8c0 1-.7 1.8-1.6 1.8Z" />,
  bolt: <path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12L13 2Z" />,
  shield: <><path d="M12 3 5 6v5c0 4.5 3 8.5 7 10 4-1.5 7-5.5 7-10V6l-7-3Z" /><path d="m9.5 12 2 2 3.5-4" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  x: <><path d="m6 6 12 12" /><path d="m18 6-12 12" /></>,
  plus: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
  amphora: <><path d="M9 3.5h6" /><path d="M10 3.5v3c0 1.6-3.5 2-3.5 5.2 0 3 2.8 4.6 3.8 7.8h3.4c1-3.2 3.8-4.8 3.8-7.8 0-3.2-3.5-3.6-3.5-5.2v-3" /><path d="M10 4.8C7 4 5.2 6.6 7.4 9.2" /><path d="M14 4.8c3-.8 4.8 1.8 2.6 4.4" /><path d="M9 21h6" /></>,
  crown: <><path d="m4 8 4 4 4-6 4 6 4-4-1.5 10h-13L4 8Z" /><path d="M6 20h12" /></>,
  map: <><path d="m3 6 6-2 6 2 6-2v14l-6 2-6-2-6 2V6Z" /><path d="M9 4v14" /><path d="M15 6v14" /></>,
  eye: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="3" /></>,
  refresh: <><path d="M20 12a8 8 0 0 1-13.7 5.6" /><path d="M4 12a8 8 0 0 1 13.7-5.6" /><path d="M4 7v5h5" /><path d="M20 17v-5h-5" /></>,
};

export function Icon({ name, size = 18, title, style, className }: { name: IconName; size?: number; title?: string; style?: React.CSSProperties; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      focusable={false}
      style={style}
      className={className}
    >
      {title && <title>{title}</title>}
      {PATHS[name]}
    </svg>
  );
}

/**
 * The AgonCup brand mark — same geometry as the app icon (build/icon.svg, the
 * source of truth) and the web console's logo, in gold: a solid "A" whose
 * counter is a Panathenaic amphora, the prize cup of the ancient agon. The
 * amphora is a real hole (a mask — its handles are strokes), so the mark sits on
 * any background; its foot runs out of the bottom edge and splits the A's legs.
 * Ids come from useId: the mark is drawn more than once per window.
 */
export function BrandMark({ size = 22 }: { size?: number }) {
  const id = React.useId();
  const grad = `${id}-gold`;
  const mask = `${id}-mark`;
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none" aria-hidden focusable={false}>
      <defs>
        <linearGradient id={grad} x1="25" y1="8" x2="80" y2="92" gradientUnits="userSpaceOnUse">
          <stop stopColor="#f0e6d2" />
          <stop offset="0.45" stopColor="#c8aa6e" />
          <stop offset="1" stopColor="#785a28" />
        </linearGradient>
        <mask id={mask} maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="100">
          <path d="M50 12 87 85H13Z" fill="#fff" stroke="#fff" strokeWidth="6" strokeLinejoin="round" />
          <g transform="translate(19 35.18) scale(0.62)">
            <path d="M38 12H62V17H56V27C56 33 74 35 74 48C74 62 58 70 55 80L61 86H39L45 80C42 70 26 62 26 48C26 35 44 33 44 27V17H38Z" fill="#000" />
            <path d="M56 19.5C72 13 79 30 68 37.5M44 19.5C28 13 21 30 32 37.5" stroke="#000" strokeWidth="5.5" strokeLinecap="round" />
          </g>
        </mask>
      </defs>
      <rect width="100" height="100" fill={`url(#${grad})`} mask={`url(#${mask})`} />
    </svg>
  );
}
