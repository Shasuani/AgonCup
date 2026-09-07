// Riot Data Dragon — the official static CDN for champion / item / spell art
// (https://developer.riotgames.com/docs/lol#data-dragon). Champions and spells
// are addressed by name key, so numeric ids from the reports go through the
// `champion.json` / `summoner.json` maps, fetched once per session for the
// latest version and cached in localStorage. Items and profile icons are by
// numeric id. Everything degrades: while the map loads (or if it never does)
// champion icons fall back to CommunityDragon, which serves them by id.

import { useEffect, useState } from "react";

const DDRAGON = "https://ddragon.leagueoflegends.com";
const CACHE_KEY = "lol-ddragon-registry-v1";

export interface DdragonRegistry {
  version: string;
  champions: Map<number, { key: string; name: string }>;
  spells: Map<number, { key: string; name: string }>;
}

interface Cached {
  version: string;
  champions: [number, { key: string; name: string }][];
  spells: [number, { key: string; name: string }][];
}

let current: DdragonRegistry | null = null;
let loading: Promise<DdragonRegistry | null> | null = null;
const listeners = new Set<(r: DdragonRegistry) => void>();

function readCache(version: string): DdragonRegistry | null {
  try {
    const raw = window.localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Cached;
    if (parsed.version !== version) return null;
    return { version, champions: new Map(parsed.champions), spells: new Map(parsed.spells) };
  } catch {
    return null;
  }
}

function writeCache(reg: DdragonRegistry): void {
  try {
    const payload: Cached = {
      version: reg.version,
      champions: [...reg.champions.entries()],
      spells: [...reg.spells.entries()],
    };
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(payload));
  } catch {
    // Storage unavailable — the in-memory copy still serves this session.
  }
}

type DataFile = { data: Record<string, { key: string; id: string; name: string }> };

async function fetchRegistry(): Promise<DdragonRegistry | null> {
  try {
    const versions = (await (await fetch(`${DDRAGON}/api/versions.json`)).json()) as string[];
    const version = versions[0];
    if (!version) return null;
    const cached = readCache(version);
    if (cached) return cached;
    const [champ, spell] = await Promise.all([
      fetch(`${DDRAGON}/cdn/${version}/data/en_US/champion.json`).then((r) => r.json() as Promise<DataFile>),
      fetch(`${DDRAGON}/cdn/${version}/data/en_US/summoner.json`).then((r) => r.json() as Promise<DataFile>),
    ]);
    const toMap = (file: DataFile): Map<number, { key: string; name: string }> => {
      const m = new Map<number, { key: string; name: string }>();
      for (const v of Object.values(file.data)) {
        const id = Number(v.key);
        if (Number.isFinite(id)) m.set(id, { key: v.id, name: v.name });
      }
      return m;
    };
    const reg = { version, champions: toMap(champ), spells: toMap(spell) };
    writeCache(reg);
    return reg;
  } catch {
    return null;
  }
}

/** The registry once loaded, else null. Never throws; never blocks a render. */
export function useDdragon(): DdragonRegistry | null {
  const [reg, setReg] = useState<DdragonRegistry | null>(current);
  useEffect(() => {
    if (current) {
      setReg(current);
      return;
    }
    listeners.add(setReg);
    if (!loading) {
      loading = fetchRegistry().then((r) => {
        if (r) {
          current = r;
          for (const cb of listeners) cb(r);
        }
        return r;
      });
    }
    return () => {
      listeners.delete(setReg);
    };
  }, []);
  return reg;
}

export function championName(reg: DdragonRegistry | null, id: number): string | null {
  return reg?.champions.get(id)?.name ?? null;
}

/** Square champion icon: Data Dragon when known, CommunityDragon (by id) otherwise. */
export function championIconUrl(reg: DdragonRegistry | null, id: number): string | null {
  if (id <= 0) return null;
  const info = reg?.champions.get(id);
  if (reg && info) return `${DDRAGON}/cdn/${reg.version}/img/champion/${info.key}.png`;
  return (
    "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/" +
    `default/v1/champion-icons/${id}.png`
  );
}

export function itemIconUrl(reg: DdragonRegistry | null, id: number): string | null {
  return reg && id > 0 ? `${DDRAGON}/cdn/${reg.version}/img/item/${id}.png` : null;
}

export function spellName(reg: DdragonRegistry | null, id: number): string | null {
  return reg?.spells.get(id)?.name ?? null;
}

export function spellIconUrl(reg: DdragonRegistry | null, id: number): string | null {
  const info = reg?.spells.get(id);
  return reg && info ? `${DDRAGON}/cdn/${reg.version}/img/spell/${info.key}.png` : null;
}
