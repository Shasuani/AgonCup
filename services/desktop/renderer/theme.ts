// LeagueOfLeagues — hextech theme tokens (desktop renderer).
// Fonts are loaded from bundled/system fallbacks only — the Electron CSP
// (default-src 'self') blocks CDN font fetches, so Cinzel/Barlow degrade to
// the serif/sans fallbacks below. Inline styles are allowed (style-src 'unsafe-inline').
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
  textDim: '#5b5a4d',
  line: 'rgba(200,170,110,.22)',
  lineSoft: 'rgba(200,170,110,.10)',
  display: "'Cinzel', Georgia, 'Times New Roman', serif",
  body: "'Barlow', 'Helvetica Neue', Helvetica, Arial, sans-serif",
  mono: "ui-monospace, 'SF Mono', Menlo, monospace",
} as const;

export const keyframesCss = `
@keyframes llBlink { 0%,100%{opacity:1;} 50%{opacity:.35;} }
@keyframes llBar { 0%{background-position:0 0;} 100%{background-position:40px 0;} }
@keyframes llSpin { to { transform: rotate(360deg); } }
`;
