// LeagueOfLeagues desktop — the one global stylesheet (injected once by App).
//
// Everything visual used to live in per-element inline `style={{}}` objects,
// which cannot express hover / focus / active states, keyframes, scrollbars or
// selection — so buttons never reacted, inputs showed Chromium's blue focus
// ring, and every screen re-declared the same panel recipe by hand. This file
// owns the hextech look (deep slate, gold, teal) as reusable `.ll-*` classes;
// components keep inline styles only for per-instance layout.
//
// Fonts: the Electron CSP blocks every CDN, so Cinzel/Barlow are embedded as
// data: URIs (see fonts.ts) rather than fetched.

import { fontFaceCss } from './fonts';
import { theme as t } from './theme';

/** Faint honeycomb, gold at ~4% — the ambient texture behind the content column. */
const HEX_PATTERN = encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="56" height="96" viewBox="0 0 56 96" fill="none">` +
    `<defs><path id="h" d="M14 0 L28 8 L28 24 L14 32 L0 24 L0 8 Z"/></defs>` +
    `<g stroke="#c8aa6e" stroke-opacity="0.09" fill="none" stroke-width="1">` +
    `<use href="#h" x="14" y="16"/><use href="#h" x="14" y="-32"/><use href="#h" x="14" y="64"/>` +
    `<use href="#h" x="-14" y="-8"/><use href="#h" x="42" y="-8"/><use href="#h" x="-14" y="40"/>` +
    `<use href="#h" x="42" y="40"/><use href="#h" x="-14" y="88"/><use href="#h" x="42" y="88"/>` +
    `</g></svg>`,
);

export const globalCss = `
${fontFaceCss}

*, *::before, *::after { box-sizing: border-box; }
html, body, #root { height: 100%; margin: 0; }
body {
  background: ${t.bg};
  color: ${t.text};
  font-family: ${t.body};
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  overflow: hidden;
}
button, input, select, textarea { font: inherit; color: inherit; }
button { cursor: pointer; }
button:disabled { cursor: not-allowed; }
h1, h2, h3, p { margin: 0; }
::selection { background: rgba(200, 170, 110, 0.28); color: ${t.goldBright}; }
:focus { outline: none; }
:focus-visible { outline: 2px solid rgba(200, 170, 110, 0.75); outline-offset: 2px; border-radius: 3px; }

::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: rgba(200, 170, 110, 0.16);
  border: 3px solid transparent;
  background-clip: padding-box;
  border-radius: 8px;
}
::-webkit-scrollbar-thumb:hover { background: rgba(200, 170, 110, 0.34); background-clip: padding-box; }

@keyframes llBlink { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
@keyframes llSpin { to { transform: rotate(360deg); } }
@keyframes llRise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes llGlow { 0%, 100% { box-shadow: 0 0 0 0 rgba(10, 200, 185, 0.35); } 50% { box-shadow: 0 0 0 5px rgba(10, 200, 185, 0); } }
@keyframes llShimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }

/* ---------------------------------------------------------------- shell --- */
.ll-app {
  width: 100vw; height: 100vh; min-width: 1100px;
  display: flex; flex-direction: column; overflow: hidden; position: relative;
  background: ${t.bg};
}
.ll-titlebar {
  height: 44px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
  background: linear-gradient(180deg, #0b1727, #06101c);
  border-bottom: 1px solid ${t.line};
  box-shadow: inset 0 1px 0 rgba(240, 230, 210, 0.04);
}
.ll-wordmark {
  display: inline-flex; align-items: center; gap: 10px;
  font-family: ${t.display}; font-weight: 700; font-size: 14px; letter-spacing: 0.16em;
  color: ${t.goldBright};
}
.ll-wordmark em { font-style: normal; color: ${t.gold}; }
.ll-sidebar {
  width: 248px; flex-shrink: 0;
  display: flex; flex-direction: column;
  background: linear-gradient(180deg, #0a1626 0%, #070f1a 100%);
  border-right: 1px solid ${t.line};
  padding: 20px 0 18px;
}
.ll-content {
  flex: 1; min-width: 0; overflow-y: auto;
  background:
    radial-gradient(900px 480px at 60% -10%, rgba(13, 40, 70, 0.75), transparent 62%),
    url("data:image/svg+xml,${HEX_PATTERN}") 0 0 / 56px 96px repeat,
    ${t.bg};
}
.ll-content > .ll-page { padding: 34px 44px 56px; max-width: 1180px; animation: llRise 180ms ease-out; }

/* --------------------------------------------------------------- user card */
.ll-user { padding: 0 20px 18px; }
.ll-user__label { font-size: 11px; letter-spacing: 0.22em; text-transform: uppercase; color: ${t.textDim}; }
.ll-user__row { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
.ll-user__name { font-size: 15px; color: ${t.textStrong}; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ll-user__meta { font-size: 12px; color: ${t.teal}; margin-top: 1px; display: flex; gap: 6px; align-items: center; white-space: nowrap; }
.ll-avatar {
  width: 42px; height: 42px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-family: ${t.display}; font-weight: 700; font-size: 18px; color: #0a1626;
  background: linear-gradient(135deg, ${t.goldDim}, ${t.gold} 60%, #e2c98c);
  box-shadow: 0 0 0 2px #071120, 0 0 0 3px rgba(200, 170, 110, 0.45);
}
.ll-avatar--sm { width: 30px; height: 30px; font-size: 12px; font-family: ${t.body}; font-weight: 600; box-shadow: none; }
.ll-avatar--teal { background: linear-gradient(135deg, #0a5c5a, ${t.teal}); color: #03141a; }
.ll-avatar--dim { background: ${t.panel3}; color: ${t.gold}; }

/* ------------------------------------------------------------------- nav --- */
.ll-nav { display: flex; flex-direction: column; gap: 3px; padding: 0 12px; margin-top: 6px; }
.ll-nav__item {
  position: relative; display: flex; align-items: center; gap: 12px; width: 100%;
  padding: 10px 14px; border: 1px solid transparent; border-radius: 4px; text-align: left;
  background: transparent; color: ${t.text}; font-size: 14.5px; font-weight: 500; letter-spacing: 0.01em;
  transition: background-color 140ms ease, color 140ms ease, border-color 140ms ease;
}
.ll-nav__item svg { width: 18px; height: 18px; flex-shrink: 0; color: ${t.goldDim}; transition: color 140ms ease; }
.ll-nav__item:hover { background: rgba(200, 170, 110, 0.06); color: ${t.textStrong}; }
.ll-nav__item:hover svg { color: ${t.gold}; }
.ll-nav__item.is-active {
  background: linear-gradient(90deg, rgba(200, 170, 110, 0.18), rgba(200, 170, 110, 0.04));
  border-color: rgba(200, 170, 110, 0.28); color: ${t.goldBright}; font-weight: 600;
}
.ll-nav__item.is-active::before {
  content: ""; position: absolute; left: -1px; top: 8px; bottom: 8px; width: 3px; border-radius: 3px;
  background: linear-gradient(180deg, #e2c98c, ${t.gold});
}
.ll-nav__item.is-active svg { color: ${t.gold}; }
.ll-nav__badge {
  margin-left: auto; min-width: 20px; padding: 1px 7px; border-radius: 10px; text-align: center;
  font-size: 11px; font-weight: 700; color: #03141a; background: ${t.teal};
}
.ll-sidebar__foot { margin-top: auto; padding: 16px 20px 0; border-top: 1px solid ${t.lineSoft}; margin-left: 12px; margin-right: 12px; }

/* --------------------------------------------------------------- type ---- */
.ll-kicker { font-size: 11.5px; font-weight: 600; letter-spacing: 0.28em; text-transform: uppercase; color: ${t.gold}; }
.ll-h1 { font-family: ${t.display}; font-weight: 700; font-size: 32px; line-height: 1.15; color: ${t.goldBright}; margin-top: 8px; letter-spacing: 0.01em; }
.ll-h2 { font-family: ${t.display}; font-weight: 600; font-size: 20px; line-height: 1.25; color: ${t.goldBright}; }
.ll-h3 { font-family: ${t.display}; font-weight: 600; font-size: 16px; color: ${t.goldBright}; }
.ll-lead { font-size: 14px; color: ${t.text}; line-height: 1.55; }
.ll-dim { color: ${t.textDim}; }
.ll-strong { color: ${t.textStrong}; }
.ll-mono { font-family: ${t.mono}; }
.ll-cap { font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; color: ${t.textDim}; font-weight: 600; }
.ll-page__head { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 26px; }
.ll-page__intro { margin-top: 8px; max-width: 62ch; color: ${t.text}; }

/* -------------------------------------------------------------- panels ---- */
.ll-panel {
  position: relative;
  background: linear-gradient(180deg, #0c1a2d 0%, #09152a 100%);
  border: 1px solid ${t.line};
  border-radius: 4px;
  box-shadow: inset 0 1px 0 rgba(240, 230, 210, 0.045), 0 6px 20px rgba(0, 0, 0, 0.35);
  padding: 24px 28px;
}
.ll-panel--tight { padding: 18px 22px; }
.ll-panel--flush { padding: 0; overflow: hidden; }
.ll-panel--sunken { background: #050d18; box-shadow: none; }
.ll-panel--featured { border-color: rgba(200, 170, 110, 0.55); box-shadow: inset 0 1px 0 rgba(240, 230, 210, 0.06), 0 0 0 1px rgba(200, 170, 110, 0.08), 0 10px 32px rgba(0, 0, 0, 0.45); }
.ll-panel--featured::before, .ll-panel--featured::after {
  content: ""; position: absolute; width: 14px; height: 14px; pointer-events: none;
  border-color: ${t.gold}; border-style: solid;
}
.ll-panel--featured::before { top: -1px; left: -1px; border-width: 2px 0 0 2px; }
.ll-panel--featured::after { bottom: -1px; right: -1px; border-width: 0 2px 2px 0; }
.ll-panel--teal { border-color: rgba(10, 200, 185, 0.45); }
.ll-panel--dashed { border-style: dashed; background: rgba(10, 200, 185, 0.025); box-shadow: none; }
.ll-panel--danger { border-color: rgba(196, 74, 74, 0.4); background: linear-gradient(180deg, rgba(196, 74, 74, 0.06), rgba(196, 74, 74, 0.02)), #09152a; }
.ll-panel__head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.ll-panel__section { border-top: 1px solid ${t.lineSoft}; padding: 20px 28px; }
.ll-stack { display: flex; flex-direction: column; gap: 16px; }
.ll-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.ll-grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
.ll-split { display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; }

/* ------------------------------------------------------------- buttons ---- */
.ll-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  height: 40px; padding: 0 22px; border-radius: 3px; border: 1px solid transparent;
  font-family: ${t.body}; font-size: 13px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  white-space: nowrap; user-select: none;
  transition: background-color 140ms ease, color 140ms ease, border-color 140ms ease, box-shadow 140ms ease, transform 100ms ease;
}
.ll-btn svg { width: 16px; height: 16px; }
.ll-btn:active:not(:disabled) { transform: translateY(1px); }
.ll-btn:disabled { opacity: 0.45; }
.ll-btn--primary {
  background: linear-gradient(180deg, #dcc08a 0%, ${t.gold} 45%, #9c7d3e 100%);
  color: #0a1626;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 4px 14px rgba(200, 170, 110, 0.22);
}
.ll-btn--primary:hover:not(:disabled) { background: linear-gradient(180deg, #ead1a0 0%, #d4b67c 45%, #ab8a48 100%); box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4), 0 6px 20px rgba(200, 170, 110, 0.35); }
.ll-btn--primary:disabled { background: #2b3a4f; color: #6f7d90; box-shadow: none; }
.ll-btn--ghost { background: transparent; border-color: rgba(200, 170, 110, 0.4); color: ${t.gold}; }
.ll-btn--ghost:hover:not(:disabled) { background: rgba(200, 170, 110, 0.1); border-color: ${t.gold}; color: ${t.goldBright}; }
.ll-btn--muted { background: transparent; border-color: ${t.line}; color: ${t.text}; }
.ll-btn--muted:hover:not(:disabled) { border-color: rgba(200, 170, 110, 0.5); color: ${t.textStrong}; background: rgba(200, 170, 110, 0.05); }
.ll-btn--danger { background: transparent; border-color: rgba(196, 74, 74, 0.45); color: #e07b7b; }
.ll-btn--danger:hover:not(:disabled) { background: rgba(196, 74, 74, 0.12); border-color: ${t.red}; color: #ff9e9e; }
.ll-btn--teal { background: transparent; border-color: rgba(10, 200, 185, 0.45); color: ${t.teal}; }
.ll-btn--teal:hover:not(:disabled) { background: rgba(10, 200, 185, 0.1); border-color: ${t.teal}; }
.ll-btn--sm { height: 32px; padding: 0 14px; font-size: 11.5px; letter-spacing: 0.08em; }
.ll-btn--xs { height: 26px; padding: 0 10px; font-size: 11px; letter-spacing: 0.06em; text-transform: none; font-weight: 500; }
.ll-btn--lg { height: 48px; padding: 0 30px; font-size: 14px; }
.ll-btn--block { width: 100%; }
.ll-btn--icon { padding: 0; width: 32px; }
.ll-link {
  background: none; border: none; padding: 0; color: ${t.gold}; font-size: inherit; letter-spacing: 0.02em;
  text-decoration: none; cursor: pointer; transition: color 120ms ease;
}
.ll-link:hover { color: ${t.goldBright}; text-decoration: underline; text-underline-offset: 3px; }

/* -------------------------------------------------------------- inputs ---- */
.ll-input {
  width: 100%; height: 44px; padding: 0 16px;
  background: #050d18; border: 1px solid ${t.line}; border-radius: 3px;
  color: ${t.textStrong}; font-size: 14px;
  transition: border-color 140ms ease, box-shadow 140ms ease, background-color 140ms ease;
}
.ll-input::placeholder { color: ${t.textDim}; }
.ll-input:hover:not(:focus) { border-color: rgba(200, 170, 110, 0.4); }
.ll-input:focus { border-color: ${t.gold}; box-shadow: 0 0 0 3px rgba(200, 170, 110, 0.18); background: #06101d; outline: none; }
.ll-input--code { font-family: ${t.mono}; letter-spacing: 0.16em; text-transform: uppercase; }
.ll-input--hero { height: 56px; font-size: 20px; text-align: center; letter-spacing: 0.32em; }
.ll-field { display: flex; gap: 10px; }
.ll-field .ll-input { flex: 1; min-width: 0; }

/* ------------------------------------------------------------ badges ------ */
.ll-badge {
  display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px;
  border-radius: 3px; border: 1px solid ${t.line}; background: rgba(200, 170, 110, 0.05);
  font-size: 10.5px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; color: ${t.text}; white-space: nowrap;
}
.ll-badge--gold { color: ${t.gold}; border-color: rgba(200, 170, 110, 0.45); background: rgba(200, 170, 110, 0.1); }
.ll-badge--teal { color: ${t.teal}; border-color: rgba(10, 200, 185, 0.45); background: rgba(10, 200, 185, 0.08); }
.ll-badge--red { color: #ff9e9e; border-color: rgba(196, 74, 74, 0.5); background: rgba(196, 74, 74, 0.1); }
.ll-badge--dim { color: ${t.textDim}; border-color: ${t.lineSoft}; background: transparent; }
.ll-badge--solid { color: #0a1626; background: linear-gradient(180deg, #e2c98c, ${t.gold}); border-color: transparent; }
.ll-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; display: inline-block; }
.ll-dot--teal { background: ${t.teal}; box-shadow: 0 0 8px ${t.teal}; }
.ll-dot--gold { background: ${t.gold}; box-shadow: 0 0 8px ${t.gold}; }
.ll-dot--red { background: ${t.red}; box-shadow: 0 0 8px ${t.red}; }
.ll-dot--dim { background: ${t.textDim}; }
.ll-dot--pulse { animation: llBlink 2.2s ease-in-out infinite; }
.ll-pill {
  display: inline-flex; align-items: center; gap: 9px; height: 30px; padding: 0 12px; border-radius: 3px;
  border: 1px solid; font-size: 11.5px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase;
}
.ll-pill--teal { color: ${t.teal}; border-color: rgba(10, 200, 185, 0.35); background: rgba(10, 200, 185, 0.08); }
.ll-pill--gold { color: ${t.gold}; border-color: rgba(200, 170, 110, 0.35); background: rgba(200, 170, 110, 0.08); }
.ll-pill--red { color: #ff9e9e; border-color: rgba(196, 74, 74, 0.35); background: rgba(196, 74, 74, 0.08); }

/* -------------------------------------------------------------- chips ----- */
.ll-chip {
  display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 9px; border-radius: 3px;
  background: rgba(200, 170, 110, 0.06); border: 1px solid ${t.lineSoft}; color: ${t.text}; font-size: 12px; white-space: nowrap;
}
.ll-chip svg { width: 13px; height: 13px; color: ${t.goldDim}; }
.ll-chips { display: flex; flex-wrap: wrap; gap: 6px; }

/* Credentials (lobby name / password) as copyable code blocks. */
.ll-cred { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.ll-cred__label { font-size: 10.5px; letter-spacing: 0.14em; text-transform: uppercase; color: ${t.textDim}; font-weight: 600; }
.ll-cred__value {
  display: inline-flex; align-items: center; gap: 10px; height: 40px; padding: 0 6px 0 14px;
  background: #050d18; border: 1px solid ${t.line}; border-radius: 3px;
  font-family: ${t.mono}; font-size: 17px; color: ${t.goldBright}; letter-spacing: 0.04em; user-select: text;
}
.ll-cred__value--plain { padding-right: 14px; }
.ll-copy {
  display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 3px;
  border: 1px solid transparent; background: transparent; color: ${t.textDim}; transition: all 120ms ease;
}
.ll-copy svg { width: 14px; height: 14px; }
.ll-copy:hover { color: ${t.gold}; background: rgba(200, 170, 110, 0.1); border-color: rgba(200, 170, 110, 0.3); }
.ll-copy.is-done { color: ${t.teal}; }

/* ------------------------------------------------------------- notices ---- */
.ll-notice {
  display: flex; align-items: flex-start; gap: 10px; padding: 12px 16px; border-radius: 3px; font-size: 13px; line-height: 1.5;
  border: 1px solid ${t.line}; background: rgba(200, 170, 110, 0.05); color: ${t.text};
}
.ll-notice svg { width: 16px; height: 16px; flex-shrink: 0; margin-top: 1px; }
.ll-notice--ok { color: #7fe3d9; border-color: rgba(10, 200, 185, 0.4); background: rgba(10, 200, 185, 0.07); }
.ll-notice--err { color: #ffb3b3; border-color: rgba(196, 74, 74, 0.45); background: rgba(196, 74, 74, 0.08); }
.ll-notice--gold { color: ${t.goldBright}; border-color: rgba(200, 170, 110, 0.4); background: rgba(200, 170, 110, 0.08); }

/* ---------------------------------------------------------------- stats --- */
.ll-stat {
  position: relative; padding: 20px 22px 18px; border-radius: 4px;
  background: linear-gradient(180deg, #0c1a2d, #09152a); border: 1px solid ${t.line};
  box-shadow: inset 0 1px 0 rgba(240, 230, 210, 0.045);
  overflow: hidden;
}
.ll-stat::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 2px; background: linear-gradient(90deg, ${t.gold}, rgba(200, 170, 110, 0.15)); }
.ll-stat--teal::before { background: linear-gradient(90deg, ${t.teal}, rgba(10, 200, 185, 0.15)); }
.ll-stat__label { font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase; color: ${t.textDim}; font-weight: 600; }
.ll-stat__value { font-family: ${t.display}; font-weight: 700; font-size: 36px; line-height: 1; color: ${t.goldBright}; margin-top: 12px; font-variant-numeric: tabular-nums; }
.ll-stat--teal .ll-stat__value { color: ${t.teal}; }
.ll-stat__sub { font-size: 12px; color: ${t.textDim}; margin-top: 8px; }

/* ------------------------------------------------------------- rows ------- */
.ll-row { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid ${t.lineSoft}; min-width: 0; }
.ll-row:last-child { border-bottom: 0; }
.ll-row__name { flex: 1; min-width: 0; color: ${t.textStrong}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ll-row__meta { font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; color: ${t.textDim}; white-space: nowrap; }
.ll-you { color: ${t.textDim}; font-size: 12px; font-weight: 400; letter-spacing: 0; text-transform: none; }

/* ------------------------------------------------------------- stepper ---- */
.ll-stepper { display: flex; align-items: flex-start; gap: 0; margin-top: 18px; }
.ll-step { flex: 1; position: relative; display: flex; flex-direction: column; align-items: center; gap: 8px; min-width: 0; }
.ll-step::before {
  content: ""; position: absolute; top: 9px; left: 50%; width: 100%; height: 2px; background: ${t.lineSoft};
}
.ll-step:last-child::before { display: none; }
.ll-step.is-done::before { background: rgba(200, 170, 110, 0.55); }
.ll-step__dot {
  position: relative; z-index: 1; width: 20px; height: 20px; border-radius: 50%;
  border: 2px solid ${t.lineSoft}; background: #071120; display: flex; align-items: center; justify-content: center;
}
.ll-step__dot svg { width: 11px; height: 11px; color: #0a1626; }
.ll-step.is-done .ll-step__dot { background: ${t.gold}; border-color: ${t.gold}; }
.ll-step.is-current .ll-step__dot { border-color: ${t.teal}; background: #071120; box-shadow: 0 0 0 0 rgba(10, 200, 185, 0.4); animation: llGlow 1.8s ease-out infinite; }
.ll-step.is-current .ll-step__dot::after { content: ""; width: 8px; height: 8px; border-radius: 50%; background: ${t.teal}; }
.ll-step__label { font-size: 10.5px; letter-spacing: 0.1em; text-transform: uppercase; color: ${t.textDim}; text-align: center; white-space: nowrap; }
.ll-step.is-done .ll-step__label { color: ${t.gold}; }
.ll-step.is-current .ll-step__label { color: ${t.teal}; font-weight: 700; }

/* -------------------------------------------------------------- switch ---- */
.ll-switch {
  position: relative; width: 46px; height: 26px; border-radius: 13px; border: 1px solid ${t.line};
  background: #050d18; transition: background-color 160ms ease, border-color 160ms ease; flex-shrink: 0; padding: 0;
}
.ll-switch::after {
  content: ""; position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; border-radius: 50%;
  background: ${t.textDim}; transition: transform 160ms ease, background-color 160ms ease;
}
.ll-switch.is-on { background: rgba(200, 170, 110, 0.25); border-color: ${t.gold}; }
.ll-switch.is-on::after { transform: translateX(20px); background: ${t.gold}; box-shadow: 0 0 8px rgba(200, 170, 110, 0.6); }
.ll-switch:disabled { opacity: 0.5; }

/* ---------------------------------------------------------- tournaments --- */
.ll-card-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 22px 28px; cursor: pointer; transition: background-color 120ms ease; }
.ll-card-head:hover { background: rgba(200, 170, 110, 0.03); }
.ll-chevron { width: 16px; height: 16px; color: ${t.gold}; transition: transform 160ms ease; flex-shrink: 0; }
.ll-chevron.is-open { transform: rotate(90deg); }
.ll-entrants { display: grid; gap: 8px; align-items: start; }
.ll-entrant { border: 1px solid ${t.line}; border-radius: 3px; background: #050d18; transition: border-color 120ms ease; }
.ll-entrant:hover { border-color: rgba(200, 170, 110, 0.4); }
.ll-entrant--you { border-color: rgba(200, 170, 110, 0.6); box-shadow: inset 3px 0 0 ${t.gold}; }
.ll-entrant__head { display: flex; align-items: center; gap: 10px; padding: 10px 14px; }
.ll-entrant__seed { font-family: ${t.mono}; font-size: 11px; color: ${t.textDim}; min-width: 22px; }
.ll-entrant__body { border-top: 1px solid ${t.lineSoft}; padding: 8px 14px 10px 46px; display: flex; flex-direction: column; gap: 5px; }

.ll-bracket { display: flex; gap: 26px; overflow-x: auto; padding-bottom: 6px; }
.ll-round { min-width: 236px; display: flex; flex-direction: column; }
.ll-round__title { font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase; color: ${t.gold}; font-weight: 600; margin-bottom: 14px; }
.ll-match { border: 1px solid ${t.line}; border-radius: 3px; background: #050d18; overflow: hidden; transition: border-color 120ms ease; }
.ll-match--decided { border-color: rgba(200, 170, 110, 0.55); }
.ll-match--live { border-color: rgba(10, 200, 185, 0.6); box-shadow: 0 0 0 1px rgba(10, 200, 185, 0.12), 0 0 18px rgba(10, 200, 185, 0.12); }
.ll-side { display: flex; align-items: center; gap: 9px; padding: 10px 14px; }
.ll-side + .ll-side { border-top: 1px solid ${t.lineSoft}; }
.ll-side--won { background: linear-gradient(90deg, rgba(200, 170, 110, 0.18), rgba(200, 170, 110, 0.04)); }
.ll-side__name { flex: 1; font-size: 14px; color: ${t.text}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ll-side--won .ll-side__name { color: ${t.goldBright}; font-weight: 700; }
.ll-side--lost .ll-side__name { color: ${t.textDim}; text-decoration: line-through; opacity: 0.7; }
.ll-match__foot {
  display: flex; justify-content: space-between; align-items: center; padding: 7px 14px;
  border-top: 1px solid ${t.lineSoft}; font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; color: ${t.textDim};
}
.ll-match__foot.is-clickable { cursor: pointer; }
.ll-match__foot.is-clickable:hover { color: ${t.gold}; }

/* ------------------------------------------------------------- champ select */
.ll-seat { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 0; border-bottom: 1px solid ${t.lineSoft}; }
.ll-seat:last-child { border-bottom: 0; }
.ll-seat__name { font-size: 13px; color: ${t.text}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ll-seat__champ { font-size: 13px; color: ${t.goldBright}; font-weight: 600; }
.ll-seat--locked .ll-seat__name { color: ${t.textStrong}; }

/* ------------------------------------------------------------- misc ------- */
.ll-empty { text-align: center; padding: 52px 40px; }
.ll-empty__mark { width: 56px; height: 56px; margin: 0 auto 18px; color: ${t.goldDim}; }
.ll-empty__mark svg { width: 100%; height: 100%; }
.ll-empty__title { font-family: ${t.display}; font-size: 22px; color: ${t.goldBright}; }
.ll-empty__copy { font-size: 14px; color: ${t.text}; margin: 10px auto 0; max-width: 46ch; line-height: 1.55; }
.ll-spinner { display: inline-block; border: 2px solid ${t.lineSoft}; border-top-color: ${t.gold}; border-radius: 50%; animation: llSpin 0.8s linear infinite; }
.ll-loading { display: flex; align-items: center; gap: 12px; color: ${t.textDim}; padding: 18px 0; font-size: 13px; }
.ll-skeleton { height: 12px; border-radius: 3px; background: linear-gradient(90deg, rgba(200,170,110,.06) 25%, rgba(200,170,110,.14) 50%, rgba(200,170,110,.06) 75%); background-size: 200% 100%; animation: llShimmer 1.4s ease-in-out infinite; }
.ll-sep { height: 1px; background: ${t.lineSoft}; margin: 18px 0; }
.ll-hr-dashed { border-top: 1px dashed ${t.line}; margin: 18px 0 16px; }
.ll-center { display: flex; align-items: center; justify-content: center; }
.ll-stage {
  width: 100vw; height: 100vh; display: flex; align-items: center; justify-content: center; position: relative; overflow: hidden;
  background:
    radial-gradient(1100px 620px at 50% -12%, rgba(16, 48, 84, 0.85), transparent 62%),
    radial-gradient(700px 400px at 100% 100%, rgba(10, 200, 185, 0.06), transparent 60%),
    url("data:image/svg+xml,${HEX_PATTERN}") 0 0 / 56px 96px repeat,
    ${t.bg};
}
.ll-stage__card { width: 480px; animation: llRise 240ms ease-out; }
.ll-steps { display: flex; flex-direction: column; gap: 10px; }
.ll-steps__item { display: flex; align-items: flex-start; gap: 12px; font-size: 14px; color: ${t.text}; line-height: 1.5; }
.ll-steps__num {
  flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%; margin-top: 1px;
  border: 1px solid rgba(200, 170, 110, 0.5); color: ${t.gold}; font-size: 11px; font-weight: 700;
  display: flex; align-items: center; justify-content: center; font-family: ${t.display};
}
.ll-kbd { font-family: ${t.mono}; color: ${t.gold}; background: rgba(200, 170, 110, 0.1); border: 1px solid rgba(200, 170, 110, 0.25); border-radius: 3px; padding: 1px 6px; font-size: 12.5px; }

/* ---------------------------------------------------------------- hero ---- */
.ll-hero {
  position: relative; overflow: hidden;
  display: flex; align-items: center; gap: 22px; padding: 22px 26px;
  background: linear-gradient(120deg, #0d1d33 0%, #09152a 55%, #0a1a2e 100%);
  border: 1px solid ${t.line}; border-radius: 4px;
  box-shadow: inset 0 1px 0 rgba(240, 230, 210, 0.05), 0 8px 26px rgba(0, 0, 0, 0.4);
}
.ll-hero::after {
  content: ""; position: absolute; right: -60px; top: -80px; width: 260px; height: 260px; border-radius: 50%;
  background: radial-gradient(closest-side, rgba(200, 170, 110, 0.14), transparent 70%); pointer-events: none;
}
.ll-hero__name { font-family: ${t.display}; font-weight: 700; font-size: 24px; color: ${t.goldBright}; line-height: 1.15; }
.ll-hero__rank { font-size: 13px; color: ${t.gold}; margin-top: 4px; letter-spacing: 0.06em; text-transform: uppercase; font-weight: 600; }
.ll-hero__meta { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.ll-hero__stats { display: flex; gap: 0; margin-left: auto; border-left: 1px solid ${t.lineSoft}; padding-left: 22px; }
.ll-hero__stat { padding: 0 18px; text-align: center; min-width: 84px; }
.ll-hero__stat + .ll-hero__stat { border-left: 1px solid ${t.lineSoft}; }
.ll-hero__num { font-family: ${t.display}; font-weight: 700; font-size: 26px; color: ${t.goldBright}; line-height: 1; }
.ll-hero__num--teal { color: ${t.teal}; }
.ll-hero__num--red { color: #ff9e9e; }
.ll-hero__lbl { font-size: 10.5px; letter-spacing: 0.14em; text-transform: uppercase; color: ${t.textDim}; margin-top: 6px; font-weight: 600; }

/* ------------------------------------------------------------- next up ---- */
.ll-next { display: flex; flex-direction: column; justify-content: space-between; min-height: 100%; }
.ll-countdown { font-family: ${t.display}; font-weight: 700; font-size: 34px; color: ${t.goldBright}; line-height: 1; letter-spacing: 0.02em; font-variant-numeric: tabular-nums; }
.ll-countdown--live { color: ${t.teal}; }

/* ------------------------------------------------------------ progress ---- */
.ll-update { display: flex; align-items: center; gap: 16px; padding: 10px 18px 10px 20px; border-bottom: 1px solid rgba(200, 170, 110, 0.35); background: linear-gradient(90deg, rgba(200, 170, 110, 0.16), rgba(10, 200, 185, 0.06) 70%, transparent); flex-shrink: 0; animation: ll-fade-in 240ms ease; }
.ll-update--required { border-bottom-color: rgba(240, 200, 110, 0.6); background: linear-gradient(90deg, rgba(200, 170, 110, 0.26), rgba(196, 74, 74, 0.1)); }
.ll-update--failed { border-bottom-color: rgba(196, 74, 74, 0.45); background: linear-gradient(90deg, rgba(196, 74, 74, 0.14), transparent); }
.ll-update__icon { width: 34px; height: 34px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; color: ${t.goldBright}; background: rgba(200, 170, 110, 0.12); border: 1px solid rgba(200, 170, 110, 0.35); flex-shrink: 0; }
.ll-update--failed .ll-update__icon { color: #ffb3b3; border-color: rgba(196, 74, 74, 0.45); background: rgba(196, 74, 74, 0.1); }
.ll-update__body { flex: 1; min-width: 0; }
.ll-update__title { font-family: ${t.display}; font-weight: 700; font-size: 14px; letter-spacing: 0.04em; color: ${t.textStrong}; }
.ll-update__sub { font-size: 12.5px; color: ${t.textDim}; margin-top: 2px; }
.ll-update__bar { margin-top: 7px; max-width: 360px; }
.ll-update__actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.ll-version { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 12px; font-size: 11.5px; color: ${t.textDim}; }
.ll-version__tag { font-family: ${t.mono}; letter-spacing: 0.04em; }
.ll-version__note { color: ${t.teal}; }
.ll-version__note--err { color: #ffb3b3; }
@keyframes ll-fade-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
.ll-progress { height: 6px; border-radius: 3px; background: rgba(200, 170, 110, 0.1); overflow: hidden; }
.ll-progress > span { display: block; height: 100%; border-radius: 3px; background: linear-gradient(90deg, ${t.goldDim}, ${t.gold}); transition: width 300ms ease; }
.ll-progress--teal > span { background: linear-gradient(90deg, #0a7d76, ${t.teal}); }
.ll-trow { display: flex; align-items: center; gap: 14px; padding: 12px 0; border-bottom: 1px solid ${t.lineSoft}; }
.ll-trow:last-child { border-bottom: 0; }
.ll-trow__main { flex: 1; min-width: 0; }
.ll-trow__name { font-family: ${t.display}; font-size: 16px; color: ${t.goldBright}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ll-trow__sub { font-size: 12px; color: ${t.textDim}; margin-top: 3px; display: flex; gap: 8px; align-items: center; }

/* --------------------------------------------------------------- games ---- */
.ll-games { display: grid; grid-template-columns: repeat(auto-fill, minmax(196px, 1fr)); gap: 12px; }
.ll-game {
  position: relative; display: flex; align-items: center; gap: 12px; padding: 12px 14px;
  background: #071322; border: 1px solid ${t.line}; border-radius: 4px; overflow: hidden;
  transition: border-color 120ms ease, transform 120ms ease; cursor: default;
}
.ll-game:hover { border-color: rgba(200, 170, 110, 0.45); transform: translateY(-1px); }
.ll-game::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: ${t.textDim}; }
.ll-game--win::before { background: ${t.teal}; }
.ll-game--loss::before { background: ${t.red}; }
.ll-game--win { background: linear-gradient(90deg, rgba(10, 200, 185, 0.08), #071322 60%); }
.ll-game--loss { background: linear-gradient(90deg, rgba(196, 74, 74, 0.09), #071322 60%); }
.ll-game__body { min-width: 0; flex: 1; }
.ll-game__result { font-family: ${t.display}; font-weight: 700; font-size: 14px; letter-spacing: 0.04em; }
.ll-game--win .ll-game__result { color: ${t.teal}; }
.ll-game--loss .ll-game__result { color: #ff9e9e; }
.ll-game__kda { font-size: 13px; color: ${t.textStrong}; font-variant-numeric: tabular-nums; margin-top: 2px; }
.ll-game__sub { font-size: 11px; color: ${t.textDim}; margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* Full scoreboard card on the Match history screen. */
.ll-board { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: ${t.lineSoft}; }
.ll-board__team { background: #071322; padding: 12px 16px; }
.ll-board__team--win { background: linear-gradient(180deg, rgba(10, 200, 185, 0.07), rgba(10, 200, 185, 0.02)), #071322; }
.ll-board__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.ll-board__row { display: flex; align-items: center; gap: 10px; padding: 5px 0; }
.ll-board__row--me { background: rgba(200, 170, 110, 0.07); margin: 0 -8px; padding: 5px 8px; border-radius: 3px; }
.ll-board__name { flex: 1; min-width: 0; font-size: 13px; color: ${t.text}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ll-board__row--me .ll-board__name { color: ${t.goldBright}; font-weight: 600; }
.ll-board__kda { font-size: 12.5px; color: ${t.textStrong}; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ll-board__kda small { color: ${t.textDim}; font-size: 11px; margin-left: 6px; }
.ll-game-head { display: flex; align-items: center; gap: 16px; padding: 14px 18px; }
.ll-game-head__champ { position: relative; }
.ll-game-head__title { font-family: ${t.display}; font-size: 17px; color: ${t.goldBright}; }
.ll-game-head__sub { font-size: 12px; color: ${t.textDim}; margin-top: 3px; }

/* ------------------------------------------------------- build & spells ---- */
.ll-items { display: inline-flex; gap: 3px; align-items: center; }
.ll-item { display: inline-block; flex-shrink: 0; }
.ll-item--trinket { margin-left: 4px; }
.ll-item--trinket img, .ll-item--trinket > span { border-radius: 50% !important; }
.ll-spells { display: inline-flex; flex-direction: column; gap: 2px; flex-shrink: 0; }
.ll-lvl {
  position: absolute; right: -4px; bottom: -4px; min-width: 16px; height: 16px; padding: 0 4px;
  border-radius: 8px; background: #050d18; border: 1px solid rgba(200, 170, 110, 0.5);
  color: ${t.goldBright}; font-size: 9.5px; font-weight: 700; line-height: 14px; text-align: center;
}
.ll-board__row { gap: 8px; }
.ll-board__build { display: inline-flex; align-items: center; gap: 8px; flex-shrink: 0; }
.ll-board__econ { font-size: 11px; color: ${t.textDim}; white-space: nowrap; font-variant-numeric: tabular-nums; }
.ll-hero__art { position: relative; flex-shrink: 0; }
.ll-hero__emblem { position: absolute; right: -14px; bottom: -10px; }
.ll-user__lvl {
  display: inline-block; margin-left: 6px; padding: 0 5px; border-radius: 8px; font-size: 10px; font-weight: 700; line-height: 15px;
  background: rgba(200, 170, 110, 0.14); border: 1px solid rgba(200, 170, 110, 0.35); color: ${t.gold}; vertical-align: 1px;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.001ms !important; transition-duration: 0.001ms !important; }
}
`;
