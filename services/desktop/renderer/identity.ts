/**
 * "Which account am I right now?" — the one identity every screen shows.
 *
 * The player may have several linked Riot accounts. The name, rank, icon and
 * level on the sidebar and the Home hero must all describe the SAME account:
 *   1. the account signed in to the open League client (read live from the LCU),
 *   2. else the last account this device saw signed in (remembered by main),
 *   3. else the first linked account (a fresh install with League closed).
 * The rank always comes from the linked account that matches that Riot ID —
 * never from "the first account in the list", which is what used to mix one
 * account's name with the other's rank.
 */
import { useEffect, useMemo, useState } from 'react';
import { lol, useAsync, type BridgeUser, type LinkedAccount, type ReadSummoner, type RememberedSummoner } from './bridge';

export type IdentitySource = 'live' | 'remembered' | 'linked' | 'none';

export interface Identity {
  /** "Name#TAG" of the account to show, else the Discord display name. */
  riotId: string;
  gameName: string | null;
  region: string | null;
  /** The linked account behind that Riot ID (rank, verified flag); null when
   * the account signed in to League is not linked to this player (yet). */
  account: LinkedAccount | null;
  profileIconId: number | null;
  level: number | null;
  source: IdentitySource;
}

interface Seen { gameName: string; tagLine: string; region?: string; profileIconId?: number; summonerLevel?: number }

function seenOf(s: { gameName?: string; tagLine?: string; region?: string; profileIconId?: number; summonerLevel?: number } | null | undefined): Seen | null {
  if (!s || !s.gameName || !s.tagLine) return null;
  return {
    gameName: s.gameName,
    tagLine: s.tagLine,
    ...(s.region ? { region: s.region } : {}),
    ...(s.profileIconId !== undefined ? { profileIconId: s.profileIconId } : {}),
    ...(s.summonerLevel !== undefined ? { summonerLevel: s.summonerLevel } : {}),
  };
}

/** Case-insensitive Riot ID match ("Name#TAG"); Riot IDs are unique across regions. */
export function sameRiotId(a: { gameName: string | null; tagLine: string | null }, b: { gameName: string | null; tagLine: string | null }): boolean {
  return !!a.gameName && !!a.tagLine && !!b.gameName && !!b.tagLine
    && a.gameName.toLowerCase() === b.gameName.toLowerCase()
    && a.tagLine.toLowerCase() === b.tagLine.toLowerCase();
}

/** Pure: pick the identity to show from the live LCU read, the device memory
 *  and the linked accounts. Exported for tests; screens use {@link useIdentity}. */
export function pickIdentity(
  live: ReadSummoner | null,
  remembered: RememberedSummoner | null,
  accounts: LinkedAccount[],
  user: BridgeUser | null,
): Identity {
  const fromLive = live && live.ok ? seenOf(live) : null;
  const fromMemory = fromLive ? null : seenOf(remembered);
  const seen = fromLive ?? fromMemory;
  const source: IdentitySource = fromLive ? 'live' : fromMemory ? 'remembered' : accounts.length > 0 ? 'linked' : 'none';

  const account = seen
    ? (accounts.find((a) => sameRiotId(a, seen)) ?? null)
    : (accounts[0] ?? null);

  const gameName = seen?.gameName ?? account?.gameName ?? null;
  const tagLine = seen?.tagLine ?? account?.tagLine ?? null;
  const riotId = gameName && tagLine ? `${gameName}#${tagLine}` : (user?.displayName ?? 'Summoner');

  return {
    riotId,
    gameName,
    region: seen?.region ?? account?.region ?? null,
    account,
    // Icon and level follow the same source as the name, so they never show
    // one account's portrait next to another account's name.
    profileIconId: seen?.profileIconId ?? null,
    level: seen?.summonerLevel ?? null,
    source,
  };
}

/** The live identity, re-read from the League client every `pollMs` so
 *  switching accounts in Riot shows up without restarting this app. The rank of
 *  the account you are on is re-read from Riot (via the backend) as soon as that
 *  account is known and every `rankPollMs` after — live, not the verify-time
 *  snapshot. */
export function useIdentity(user: BridgeUser | null, pollMs = 15_000, rankPollMs = 5 * 60_000): Identity & { accounts: LinkedAccount[]; accountsLoading: boolean; reloadAccounts: () => void } {
  const b = lol();
  const summoner = useAsync(async () => { const s = b ? await b.readSummoner() : null; return s && s.ok ? s : null; }, [], pollMs);
  const remembered = useAsync(async () => (b && typeof b.getProfile === 'function' ? await b.getProfile() : null), [], pollMs);
  // Polled like the summoner read: a lock-in can link the account that is open
  // (see main.ts), and the card would otherwise say "Not linked" until a restart.
  const accounts = useAsync(async () => { const r = b ? await b.listAccounts() : null; return r && r.ok ? r.accounts : []; }, [], pollMs);

  // Freshly refreshed rows (live rank) layered over the list by account id.
  const [fresh, setFresh] = useState<Record<string, LinkedAccount>>({});
  const list = useMemo(() => (accounts.data ?? []).map((a) => fresh[a.accountId] ?? a), [accounts.data, fresh]);
  const identity = useMemo(
    () => pickIdentity(summoner.data, remembered.data, list, user),
    [summoner.data, remembered.data, list, user],
  );

  const currentId = identity.account?.accountId ?? null;
  const [rankTick, setRankTick] = useState(0);
  useEffect(() => {
    if (!rankPollMs) return;
    const id = setInterval(() => setRankTick((n) => n + 1), rankPollMs);
    return () => clearInterval(id);
  }, [rankPollMs]);
  useEffect(() => {
    if (!b || typeof b.refreshRank !== 'function' || !currentId) return;
    let alive = true;
    void b.refreshRank(currentId).then((r) => {
      if (alive && r.ok) setFresh((m) => ({ ...m, [r.account.accountId]: r.account }));
    }, () => { /* best-effort: the stored rank stays on screen */ });
    return () => { alive = false; };
  }, [b, currentId, rankTick]);

  return { ...identity, accounts: list, accountsLoading: accounts.loading, reloadAccounts: accounts.reload };
}
