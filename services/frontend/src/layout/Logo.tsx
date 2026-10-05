// Brand logo rendered INLINE (not via <img src=...>) on purpose.
//
// The wordmark uses fill="currentColor" so it inherits the surrounding text color
// (var(--text)) and stays legible in BOTH the dark and light themes. An SVG loaded
// through <img> cannot inherit currentColor — it resolves to black — which made the
// wordmark invisible on the dark cobalt sidebar. Inlining fixes that.
//
// The mark is the AgonCup "A": a solid A whose counter is a Panathenaic amphora
// (the prize cup of the ancient agon). Same geometry as the desktop app icon
// (services/desktop/build/icon.svg — the source of truth), in the same hextech
// gold, with the client's Cinzel "AGONCUP" wordmark. The amphora is a real hole
// cut by a mask (its handles are strokes), so the mark sits on either theme.
// Ids come from useId: the sidebar keeps the full logo AND the (display:none) square mark in the DOM together, and
// a url(#id) that resolves into a hidden SVG paints nothing.

import { useId, type SVGProps } from "react";

const WORDMARK_FONT = "Cinzel, Georgia, 'Times New Roman', serif";

/** Gradient + mask for one mark instance, in the mark's 100-grid. */
function MarkDefs({ grad, mask }: { grad: string; mask: string }): JSX.Element {
  return (
    <defs>
      <linearGradient id={grad} x1="25" y1="8" x2="80" y2="92" gradientUnits="userSpaceOnUse">
        <stop stopColor="#e2c98c" />
        <stop offset="0.5" stopColor="#c8aa6e" />
        <stop offset="1" stopColor="#785a28" />
      </linearGradient>
      <mask id={mask} maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="100">
        <path d="M50 12 87 85H13Z" fill="#fff" stroke="#fff" strokeWidth="6" strokeLinejoin="round" />
        <g transform="translate(19 35.18) scale(0.62)">
          <path
            d="M38 12H62V17H56V27C56 33 74 35 74 48C74 62 58 70 55 80L61 86H39L45 80C42 70 26 62 26 48C26 35 44 33 44 27V17H38Z"
            fill="#000"
          />
          <path
            d="M56 19.5C72 13 79 30 68 37.5M44 19.5C28 13 21 30 32 37.5"
            fill="none"
            stroke="#000"
            strokeWidth="5.5"
            strokeLinecap="round"
          />
        </g>
      </mask>
    </defs>
  );
}

/** Full horizontal logo: the A-amphora mark + "AgonCup" wordmark. */
export function BrandLogo(props: SVGProps<SVGSVGElement>): JSX.Element {
  const id = useId();
  const grad = `${id}-grad`;
  const mask = `${id}-mark`;
  return (
    <svg
      width="140"
      height="32"
      viewBox="0 0 140 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="AgonCup"
      {...props}
    >
      <MarkDefs grad={grad} mask={mask} />
      {/* the 100-grid mark in the 32 px row */}
      <g transform="scale(0.32)">
        <rect width="100" height="100" fill={`url(#${grad})`} mask={`url(#${mask})`} />
      </g>
      <text
        x="40"
        y="22"
        fontFamily={WORDMARK_FONT}
        fontSize="15"
        fontWeight="700"
        letterSpacing="2.2"
        fill="currentColor"
      >
        AGON
        <tspan fill="#c8aa6e">CUP</tspan>
      </text>
    </svg>
  );
}

/** Square mark only (used when the sidebar collapses to icons). */
export function BrandMark(props: SVGProps<SVGSVGElement>): JSX.Element {
  const id = useId();
  const grad = `${id}-grad`;
  const mask = `${id}-mark`;
  return (
    <svg
      width="32"
      height="32"
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="AgonCup"
      {...props}
    >
      <MarkDefs grad={grad} mask={mask} />
      <rect width="100" height="100" fill={`url(#${grad})`} mask={`url(#${mask})`} />
    </svg>
  );
}
