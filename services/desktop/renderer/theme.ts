// LeagueOfLeagues — hextech theme tokens (desktop renderer).
import type { CSSProperties } from 'react';
// Cinzel + Barlow are bundled as data: URIs (fonts.ts) — the Electron CSP
// (default-src 'self', font-src 'self' data:) blocks every CDN, so this is the
// only way the real faces reach the renderer. The stacks below still name a
// fallback for the split second before the @font-face parses.
export const theme = {
  bg: '#010a13',
  panel: '#0a1626',
  panel2: '#0e1e33',
  panel3: '#12253d',
  gold: '#c8aa6e',
  goldBright: '#f0e6d2',
  goldDim: '#785a28',
  teal: '#0ac8b9',
  tealDeep: '#0596aa',
  red: '#c44a4a',
  text: '#a09b8c',
  textStrong: '#f0e6d2',
  textDim: '#6b6a5c',
  line: 'rgba(200,170,110,.22)',
  lineSoft: 'rgba(200,170,110,.10)',
  display: "'Cinzel', Georgia, 'Times New Roman', serif",
  body: "'Barlow', 'Helvetica Neue', Helvetica, Arial, sans-serif",
  mono: "ui-monospace, 'SF Mono', Menlo, Consolas, monospace",
} as const;

/** True under the macOS build, where the native title bar is hidden
 *  (`titleBarStyle: 'hiddenInset'` in main.ts) and the traffic lights sit
 *  inside our own title bar — the bar needs a left inset for them. */
export const isMac = typeof navigator !== 'undefined' && navigator.userAgent.includes('Macintosh');
/** Width reserved on the left of the title bar for the macOS traffic lights. */
export const TRAFFIC_LIGHT_INSET = 94;
/** Height of the in-app title bar (also the drag strip on the pair screen). */
export const TITLE_BAR_HEIGHT = 44;

/** Chrome (title bar, sidebar) is not selectable text — dragging the window
 *  must never leave a text-selection highlight behind. */
export const chromeStyle = { userSelect: 'none', WebkitUserSelect: 'none' } as CSSProperties;
/** Marks an element as a window drag handle (frameless macOS title bar). */
export const dragRegion = { WebkitAppRegion: 'drag' } as CSSProperties;
/** Opt an interactive child out of the drag region so it stays clickable. */
export const noDrag = { WebkitAppRegion: 'no-drag' } as CSSProperties;

/** Kept for any caller that still injects it; the keyframes now live in
 *  styles.ts (globalCss), which App mounts once. */
export const keyframesCss = `
@keyframes llBlink { 0%,100%{opacity:1;} 50%{opacity:.35;} }
@keyframes llBar { 0%{background-position:0 0;} 100%{background-position:40px 0;} }
@keyframes llSpin { to { transform: rotate(360deg); } }
`;
