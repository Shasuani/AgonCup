// Game art from Riot's Data Dragon — the official static CDN
// (https://developer.riotgames.com/docs/lol#data-dragon). It serves champion
// icons / splash / loading art, item icons, summoner-spell icons and profile
// icons. Champions and spells are addressed by NAME KEY ("Ahri", "SummonerFlash"),
// so numeric ids from the LCU / eog reports go through the id → key maps in
// `champion.json` and `summoner.json`; items and profile icons are by numeric
// id directly. The maps are fetched once per app start (latest version from
// `versions.json`) and cached in localStorage keyed by version.
//
// The Electron CSP opens img-src + connect-src to ddragon.leagueoflegends.com
// for this. CommunityDragon stays as the champion-icon fallback while the map
// is still loading or for an id the map does not know (it is served by id).
import React, { useEffect, useState } from 'react';

const DDRAGON = 'https://ddragon.leagueoflegends.com';
const CACHE_KEY = 'll-ddragon-registry-v2';

export interface ChampionInfo { key: string; name: string }
export interface SpellInfo { key: string; name: string }
export interface Registry {
  version: string;
  champions: Map<number, ChampionInfo>;
  spells: Map<number, SpellInfo>;
}

interface Cached { version: string; champions: [number, ChampionInfo][]; spells: [number, SpellInfo][] }

let loading: Promise<Registry | null> | null = null;
let current: Registry | null = null;
const listeners = new Set<(r: Registry) => void>();

function readCache(version: string): Registry | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Cached;
    if (parsed.version !== version || !Array.isArray(parsed.champions) || !Array.isArray(parsed.spells)) return null;
    return { version, champions: new Map(parsed.champions), spells: new Map(parsed.spells) };
  } catch {
    return null;
  }
}

function writeCache(reg: Registry): void {
  try {
    const payload: Cached = { version: reg.version, champions: [...reg.champions.entries()], spells: [...reg.spells.entries()] };
    localStorage.setItem(CACHE_KEY, JSON.stringify(payload));
  } catch {
    // Storage may be unavailable; the in-memory copy still serves this run.
  }
}

async function fetchRegistry(): Promise<Registry | null> {
  try {
    const versions = (await (await fetch(`${DDRAGON}/api/versions.json`)).json()) as string[];
    const version = versions[0];
    if (!version) return null;
    const cached = readCache(version);
    if (cached) return cached;
    const [champJson, spellJson] = await Promise.all([
      fetch(`${DDRAGON}/cdn/${version}/data/en_US/champion.json`).then((r) => r.json()) as Promise<{ data: Record<string, { key: string; id: string; name: string }> }>,
      fetch(`${DDRAGON}/cdn/${version}/data/en_US/summoner.json`).then((r) => r.json()) as Promise<{ data: Record<string, { key: string; id: string; name: string }> }>,
    ]);
    const champions = new Map<number, ChampionInfo>();
    for (const c of Object.values(champJson.data)) {
      const id = Number(c.key);
      if (Number.isFinite(id)) champions.set(id, { key: c.id, name: c.name });
    }
    const spells = new Map<number, SpellInfo>();
    for (const sp of Object.values(spellJson.data)) {
      const id = Number(sp.key);
      if (Number.isFinite(id)) spells.set(id, { key: sp.id, name: sp.name });
    }
    const reg = { version, champions, spells };
    writeCache(reg);
    return reg;
  } catch {
    return null;
  }
}

/** Kick off (once) and resolve the registry; null when Data Dragon is unreachable. */
export function loadRegistry(): Promise<Registry | null> {
  if (current) return Promise.resolve(current);
  if (!loading) {
    loading = fetchRegistry().then((reg) => {
      if (reg) {
        current = reg;
        for (const cb of listeners) cb(reg);
      }
      return reg;
    });
  }
  return loading;
}

/** The Data Dragon registry, or null until it has loaded (or if it never does). */
export function useChampions(): Registry | null {
  const [reg, setReg] = useState<Registry | null>(current);
  useEffect(() => {
    if (current) { setReg(current); return; }
    listeners.add(setReg);
    void loadRegistry();
    return () => { listeners.delete(setReg); };
  }, []);
  return reg;
}

export function championName(reg: Registry | null, id: number): string | null {
  return reg?.champions.get(id)?.name ?? null;
}

/** Square icon (Data Dragon when the key is known, else CommunityDragon by id). */
export function championIconUrl(reg: Registry | null, id: number): string | null {
  if (id <= 0) return null;
  const info = reg?.champions.get(id);
  if (reg && info) return `${DDRAGON}/cdn/${reg.version}/img/champion/${info.key}.png`;
  return `https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/champion-icons/${id}.png`;
}

/** Wide splash art (1215×717) — ambient backgrounds. Null until the key is known. */
export function championSplashUrl(reg: Registry | null, id: number): string | null {
  const info = reg?.champions.get(id);
  return info ? `${DDRAGON}/cdn/img/champion/splash/${info.key}_0.jpg` : null;
}

/** Tall loading-screen art (308×560). Null until the key is known. */
export function championLoadingUrl(reg: Registry | null, id: number): string | null {
  const info = reg?.champions.get(id);
  return info ? `${DDRAGON}/cdn/img/champion/loading/${info.key}_0.jpg` : null;
}

/** Item icon by numeric id (0 = empty slot → null). Needs only the version. */
export function itemIconUrl(reg: Registry | null, id: number): string | null {
  return reg && id > 0 ? `${DDRAGON}/cdn/${reg.version}/img/item/${id}.png` : null;
}

export function spellName(reg: Registry | null, id: number): string | null {
  return reg?.spells.get(id)?.name ?? null;
}

/** Summoner-spell icon by numeric id (Flash = 4, Ignite = 14, …). */
export function spellIconUrl(reg: Registry | null, id: number): string | null {
  const info = reg?.spells.get(id);
  return reg && info ? `${DDRAGON}/cdn/${reg.version}/img/spell/${info.key}.png` : null;
}

/** Summoner (profile) icon by numeric id. Needs only the version. */
export function profileIconUrl(reg: Registry | null, id: number): string | null {
  return reg && id > 0 ? `${DDRAGON}/cdn/${reg.version}/img/profileicon/${id}.png` : null;
}

/** An <img> that collapses to `fallback` when the URL is missing or fails. */
function Art({ url, alt, size, style, fallback, title }: { url: string | null; alt: string; size: number; style?: React.CSSProperties; fallback: React.ReactNode; title?: string }) {
  const [broken, setBroken] = useState<string | null>(null);
  if (!url || broken === url) return <>{fallback}</>;
  return <img src={url} alt={alt} title={title} width={size} height={size} loading="lazy" style={style} onError={() => setBroken(url)} />;
}

export function ChampIcon({ id, name, size = 32, ring, level }: { id: number; name?: string; size?: number; ring?: 'gold' | 'teal' | 'red' | 'none'; level?: number | null }) {
  const reg = useChampions();
  const url = championIconUrl(reg, id);
  const label = name ?? championName(reg, id) ?? undefined;
  const border = ring === 'gold' ? '#c8aa6e' : ring === 'teal' ? '#0ac8b9' : ring === 'red' ? '#c44a4a' : 'rgba(200,170,110,.22)';
  const style: React.CSSProperties = {
    width: size, height: size, borderRadius: Math.max(4, size / 8), flexShrink: 0,
    border: `1px solid ${border}`, background: '#050d18', objectFit: 'cover', display: 'block',
    boxShadow: ring && ring !== 'none' ? `0 0 0 1px ${border}33` : undefined,
  };
  const fallback = (
    <span style={{ ...style, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: '#6b6a5c', fontSize: Math.max(9, size / 3), fontWeight: 600 }} title={label}>
      {label ? label.slice(0, 2) : '?'}
    </span>
  );
  const img = <Art url={url} alt={label ?? ''} title={label} size={size} style={style} fallback={fallback} />;
  if (level == null) return img;
  return (
    <span style={{ position: 'relative', display: 'inline-block', flexShrink: 0, width: size, height: size }}>
      {img}
      <span className="ll-lvl">{level}</span>
    </span>
  );
}

/** The player's summoner icon; initials while unknown. */
export function ProfileIcon({ id, name, size = 42, ring = true }: { id: number | null | undefined; name: string; size?: number; ring?: boolean }) {
  const reg = useChampions();
  const url = id != null ? profileIconUrl(reg, id) : null;
  const style: React.CSSProperties = {
    width: size, height: size, borderRadius: '50%', objectFit: 'cover', display: 'block', flexShrink: 0, background: '#050d18',
    boxShadow: ring ? '0 0 0 2px #071120, 0 0 0 3px rgba(200,170,110,.45)' : undefined,
  };
  const initials = (name || '?').split('#')[0]!.trim().slice(0, 2) || '?';
  const fallback = (
    <span className={`ll-avatar${size <= 32 ? ' ll-avatar--sm' : ''}`} style={{ width: size, height: size, fontSize: Math.max(11, size * 0.42), boxShadow: ring ? undefined : 'none' }}>{initials}</span>
  );
  return <Art url={url} alt="" size={size} style={style} fallback={fallback} />;
}

/** Two summoner spells, stacked. */
export function SpellPair({ a, b, size = 18 }: { a: number | null; b: number | null; size?: number }) {
  const reg = useChampions();
  const one = (id: number | null, i: number) => {
    const url = id != null ? spellIconUrl(reg, id) : null;
    const label = id != null ? spellName(reg, id) ?? `Spell ${id}` : '';
    const style: React.CSSProperties = { width: size, height: size, borderRadius: 3, display: 'block', border: '1px solid rgba(200,170,110,.18)', background: '#050d18' };
    return <Art key={i} url={url} alt={label} title={label} size={size} style={style} fallback={<span style={{ ...style, opacity: 0.4 }} />} />;
  };
  return <span className="ll-spells">{one(a, 0)}{one(b, 1)}</span>;
}

/** The final build: six item slots + trinket, in slot order; empty slots stay empty. */
export function ItemRow({ items, size = 22 }: { items: number[]; size?: number }) {
  const reg = useChampions();
  const slots = [...items];
  while (slots.length < 7) slots.push(0);
  return (
    <span className="ll-items">
      {slots.slice(0, 7).map((id, i) => {
        const url = itemIconUrl(reg, id);
        const style: React.CSSProperties = { width: size, height: size, borderRadius: 3, display: 'block', border: '1px solid rgba(200,170,110,.18)', background: '#050d18' };
        return (
          <span key={i} className={`ll-item${i === 6 ? ' ll-item--trinket' : ''}`} style={{ width: size, height: size }}>
            <Art url={url} alt={id > 0 ? `Item ${id}` : ''} title={id > 0 ? `Item ${id}` : undefined} size={size} style={style} fallback={<span style={{ ...style, opacity: 0.35 }} />} />
          </span>
        );
      })}
    </span>
  );
}
