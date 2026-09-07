// Inline SVG icon set for the desktop renderer (24-grid, 1.6px strokes,
// currentColor). Replaces the Unicode glyphs (◆ ▸ ❖ ⬡ ⬢) the nav used to draw,
// which rendered at different sizes per platform font and could not be tinted
// as a group. Decorative by default (aria-hidden); pass `title` for a labelled
// image.
import React from 'react';

export type IconName =
  | 'home' | 'swords' | 'trophy' | 'users' | 'id' | 'check' | 'chevron' | 'copy'
  | 'logout' | 'link' | 'alert' | 'info' | 'discord' | 'bolt' | 'shield' | 'clock'
  | 'x' | 'plus' | 'hex' | 'crown' | 'map' | 'refresh';

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
  hex: <><path d="M12 2.5 20.2 7.25v9.5L12 21.5 3.8 16.75v-9.5L12 2.5Z" /><path d="M9 9v6h3.2" /><path d="M15 9v6" opacity="0.55" /><path d="M12 12h3" /></>,
  crown: <><path d="m4 8 4 4 4-6 4 6 4-4-1.5 10h-13L4 8Z" /><path d="M6 20h12" /></>,
  map: <><path d="m3 6 6-2 6 2 6-2v14l-6 2-6-2-6 2V6Z" /><path d="M9 4v14" /><path d="M15 6v14" /></>,
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

/** The hex sigil brand mark — same geometry as the web console's logo, in gold. */
export function BrandMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden focusable={false}>
      <defs>
        <linearGradient id="llMark" x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
          <stop stopColor="#e2c98c" />
          <stop offset="1" stopColor="#c8aa6e" />
        </linearGradient>
      </defs>
      <path d="M16 2.5 27.7 9.25v13.5L16 29.5 4.3 22.75V9.25L16 2.5Z" fill="#0a1626" stroke="url(#llMark)" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M11.5 10v12h4.5" stroke="url(#llMark)" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M20.5 10v12" stroke="#c8aa6e" strokeWidth="2.1" strokeLinecap="round" opacity="0.5" />
      <path d="M16 16h4.5" stroke="url(#llMark)" strokeWidth="2.1" strokeLinecap="round" />
    </svg>
  );
}
