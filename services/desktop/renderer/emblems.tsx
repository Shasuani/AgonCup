// Rank emblems. Riot's own ranked-emblem art is not on Data Dragon, so it is
// loaded from CommunityDragon (a mirror of the client's static assets, which
// the Electron CSP allows). While it loads — or if it never does — a drawn
// hextech-style crest with the tier colour and division numeral stands in, so
// "Gold II" reads at a glance either way.
import React from 'react';
import { theme as t } from './theme';

export type Tier =
  | 'iron' | 'bronze' | 'silver' | 'gold' | 'platinum' | 'emerald' | 'diamond'
  | 'master' | 'grandmaster' | 'challenger' | 'unranked';

export interface RankParts {
  tier: Tier;
  /** "I" … "IV" for the divided tiers; null for apex tiers / unranked. */
  division: string | null;
  /** League points when the text carried them ("Gold II, 45 LP"). */
  lp: number | null;
  /** The human label to show ("Gold II", "Master", "Unranked"). */
  label: string;
}

const TIERS: Tier[] = ['iron', 'bronze', 'silver', 'gold', 'platinum', 'emerald', 'diamond', 'master', 'grandmaster', 'challenger'];

/** Parse the backend's best-effort rank snapshot ("Gold II, 45 LP", "Unranked"). */
export function rankParts(rankText: string | null | undefined): RankParts {
  const text = (rankText ?? '').trim();
  const lower = text.toLowerCase();
  const tier = TIERS.find((x) => lower.startsWith(x)) ?? 'unranked';
  const div = /\b(IV|III|II|I)\b/.exec(text)?.[1] ?? null;
  const lpMatch = /(\d+)\s*lp/i.exec(text);
  const lp = lpMatch ? Number(lpMatch[1]) : null;
  const cap = tier.charAt(0).toUpperCase() + tier.slice(1);
  const apex = tier === 'master' || tier === 'grandmaster' || tier === 'challenger';
  const label = tier === 'unranked' ? 'Unranked' : apex ? cap : `${cap}${div ? ` ${div}` : ''}`;
  return { tier, division: apex ? null : div, lp, label };
}

/** Two-stop gradient + glow per tier. */
export const TIER_COLORS: Record<Tier, { a: string; b: string; glow: string }> = {
  iron: { a: '#7c7c7c', b: '#3f3f3f', glow: 'rgba(140,140,140,.35)' },
  bronze: { a: '#d09a5e', b: '#6f3f1c', glow: 'rgba(200,140,80,.4)' },
  silver: { a: '#d6dee5', b: '#6e7f8d', glow: 'rgba(190,205,215,.4)' },
  gold: { a: '#f2d78e', b: '#9c7325', glow: 'rgba(240,200,110,.45)' },
  platinum: { a: '#9fe7dc', b: '#1f6f6a', glow: 'rgba(80,210,195,.45)' },
  emerald: { a: '#7ce8a8', b: '#176b3e', glow: 'rgba(80,220,140,.45)' },
  diamond: { a: '#b8dcff', b: '#2e64c0', glow: 'rgba(120,180,255,.5)' },
  master: { a: '#d6b3ff', b: '#5b2aa8', glow: 'rgba(170,120,255,.5)' },
  grandmaster: { a: '#ff9c8a', b: '#8c1f14', glow: 'rgba(255,120,100,.5)' },
  challenger: { a: '#bdf1ff', b: '#c99a3a', glow: 'rgba(200,230,255,.55)' },
  unranked: { a: '#4a5563', b: '#1f2731', glow: 'rgba(0,0,0,0)' },
};

let uid = 0;

/** Riot's own ranked emblem art for a tier (CommunityDragon mirrors the client's
 *  static assets). Unranked has no emblem — the crest below stands in. */
export function riotEmblemUrl(tier: Tier): string | null {
  if (tier === 'unranked') return null;
  // The 500×500 shared-components emblem (the 1280×720 `ranked-emblem/emblem-*`
  // canvas is mostly transparent padding and renders as a speck).
  return `https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-shared-components/global/default/${tier}.png`;
}

/** The rank emblem: Riot's art when it loads, the drawn crest otherwise. */
export function RankEmblem({ rankText, size = 64 }: { rankText: string | null | undefined; size?: number }) {
  const parts = rankParts(rankText);
  const url = riotEmblemUrl(parts.tier);
  const [broken, setBroken] = React.useState<string | null>(null);
  if (url && broken !== url) {
    return (
      <img
        src={url}
        alt={parts.label}
        title={parts.label}
        width={size}
        height={size}
        loading="lazy"
        style={{ width: size, height: size, objectFit: 'contain', display: 'block', flexShrink: 0, transform: 'scale(1.28)', transformOrigin: 'center', filter: `drop-shadow(0 0 ${Math.round(size / 9)}px ${TIER_COLORS[parts.tier].glow})` }}
        onError={() => setBroken(url)}
      />
    );
  }
  return <RankCrest rankText={rankText} size={size} />;
}

/** The drawn crest for the tier, with the division numeral (or a gem for apex tiers). */
export function RankCrest({ rankText, size = 64 }: { rankText: string | null | undefined; size?: number }) {
  const parts = rankParts(rankText);
  const c = TIER_COLORS[parts.tier];
  const id = React.useMemo(() => `emb${++uid}`, []);
  const apex = parts.division === null && parts.tier !== 'unranked';
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" role="img" aria-label={parts.label} style={{ filter: parts.tier === 'unranked' ? undefined : `drop-shadow(0 0 ${Math.round(size / 8)}px ${c.glow})` }}>
      <defs>
        <linearGradient id={`${id}a`} x1="8" y1="4" x2="56" y2="60" gradientUnits="userSpaceOnUse">
          <stop stopColor={c.a} />
          <stop offset="1" stopColor={c.b} />
        </linearGradient>
        <linearGradient id={`${id}b`} x1="32" y1="10" x2="32" y2="56" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0e1c2f" />
          <stop offset="1" stopColor="#050c16" />
        </linearGradient>
      </defs>
      {/* outer crest */}
      <path d="M32 3 55 12v18c0 13-9.5 24-23 31C18.5 54 9 43 9 30V12l23-9Z" fill={`url(#${id}b)`} stroke={`url(#${id}a)`} strokeWidth="2.2" strokeLinejoin="round" />
      {/* inner line */}
      <path d="M32 9.5 49.5 16.5V30c0 10-7 18.5-17.5 24.5C21.5 48.5 14.5 40 14.5 30V16.5L32 9.5Z" stroke={`url(#${id}a)`} strokeWidth="1" strokeOpacity="0.55" strokeLinejoin="round" />
      {/* wings */}
      <path d="M9 22 2 18l7 10M55 22l7-4-7 10" stroke={`url(#${id}a)`} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      {apex ? (
        <path d="M32 20 41 31 32 44 23 31 32 20Z" fill={`url(#${id}a)`} />
      ) : parts.tier === 'unranked' ? (
        <path d="M24 32h16" stroke={c.a} strokeWidth="2.4" strokeLinecap="round" />
      ) : (
        <text x="32" y="40" textAnchor="middle" fontFamily={t.display} fontWeight="700" fontSize="20" fill={`url(#${id}a)`} letterSpacing="1">
          {parts.division ?? ''}
        </text>
      )}
      {/* base gem */}
      <path d="M32 50.5 35 54l-3 4-3-4 3-3.5Z" fill={`url(#${id}a)`} opacity="0.9" />
    </svg>
  );
}
