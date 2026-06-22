// Brand logo rendered INLINE (not via <img src=...>) on purpose.
//
// The wordmark uses fill="currentColor" so it inherits the surrounding text color
// (var(--text)) and stays legible in BOTH the dark and light themes. An SVG loaded
// through <img> cannot inherit currentColor — it resolves to black — which made the
// wordmark invisible on the dark cobalt sidebar. Inlining fixes that.

import type { SVGProps } from "react";

const WORDMARK_FONT =
  "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

/** Full horizontal logo: hex sigil mark + "LeagueOfLeagues" wordmark. */
export function BrandLogo(props: SVGProps<SVGSVGElement>): JSX.Element {
  return (
    <svg
      width="206"
      height="32"
      viewBox="0 0 206 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="LeagueOfLeagues"
      {...props}
    >
      <defs>
        <linearGradient id="llLogoGrad" x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
          <stop stopColor="#6d5cf6" />
          <stop offset="1" stopColor="#8b7dff" />
        </linearGradient>
      </defs>
      <g>
        <path
          d="M16 2.5 27.7 9.25v13.5L16 29.5 4.3 22.75V9.25L16 2.5Z"
          fill="#191c24"
          stroke="url(#llLogoGrad)"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <path
          d="M11.5 10v12h4.5"
          fill="none"
          stroke="url(#llLogoGrad)"
          strokeWidth="2.1"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M20.5 10v12"
          fill="none"
          stroke="#8b7dff"
          strokeWidth="2.1"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity="0.55"
        />
        <path d="M16 16h4.5" fill="none" stroke="url(#llLogoGrad)" strokeWidth="2.1" strokeLinecap="round" />
      </g>
      <text
        x="40"
        y="22"
        fontFamily={WORDMARK_FONT}
        fontSize="17"
        fontWeight="700"
        letterSpacing="-0.3"
        fill="currentColor"
      >
        League
        <tspan fill="#8b7dff" fontWeight="600">
          Of
        </tspan>
        Leagues
      </text>
    </svg>
  );
}

/** Square mark only (used when the sidebar collapses to icons). */
export function BrandMark(props: SVGProps<SVGSVGElement>): JSX.Element {
  return (
    <svg
      width="32"
      height="32"
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="LeagueOfLeagues"
      {...props}
    >
      <defs>
        <linearGradient id="llMarkGrad" x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
          <stop stopColor="#6d5cf6" />
          <stop offset="1" stopColor="#8b7dff" />
        </linearGradient>
      </defs>
      <path
        d="M16 2.5 27.7 9.25v13.5L16 29.5 4.3 22.75V9.25L16 2.5Z"
        fill="#191c24"
        stroke="url(#llMarkGrad)"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M11.5 10v12h4.5"
        fill="none"
        stroke="url(#llMarkGrad)"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M20.5 10v12"
        fill="none"
        stroke="#8b7dff"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.55"
      />
      <path d="M16 16h4.5" fill="none" stroke="url(#llMarkGrad)" strokeWidth="2.1" strokeLinecap="round" />
    </svg>
  );
}
