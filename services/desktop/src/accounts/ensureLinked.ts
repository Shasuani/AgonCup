/**
 * Link the account open in League BEFORE lock-in, when it is not linked yet.
 *
 * Lock-in tells the backend which account is open (`active_account`), but the
 * backend only ever picks among the player's LINKED accounts — an invite is
 * addressed through the `RiotAccount` row. A player who linked acc1 and then
 * signed into acc2 was therefore still invited on acc1, the offline one
 * (ToDoList #8, second round). The desktop already owns a no-code way to link
 * what is open (`verify-lcu`, see lcu/verify.ts), so lock-in runs it first.
 *
 * Only an account that is NOT already linked+verified is sent: `verify-lcu`
 * DMs the player on every call, and a lock-in must not spam them. A renamed
 * account reads as "not linked" here and is simply refreshed in place (the
 * backend dedupes on the canonical PUUID).
 *
 * Never throws. A `401` is surfaced so the caller can refresh + retry.
 */

import { listMyAccounts, type ListAccountsResult } from "./listAccounts.js";
import { verifyDesktop, type VerifyResult } from "../lcu/verify.js";
import type { CurrentSummonerOk } from "../lcu/readSummoner.js";

export type EnsureLinkedResult =
  | {
      readonly ok: true;
      /** `linked` = this call linked it; `already-linked` = nothing was sent. */
      readonly outcome: "already-linked" | "linked";
      readonly riotId: string;
    }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export interface EnsureLinkedArgs {
  readonly accessToken: string;
  /** The live LCU read lock-in already made — reused, so ONE account is judged. */
  readonly summoner: CurrentSummonerOk;
}

export interface EnsureLinkedDeps {
  readonly fetch?: typeof fetch;
  readonly backendUrl?: string;
  readonly listAccounts?: (accessToken: string) => Promise<ListAccountsResult>;
  readonly verify?: (accessToken: string) => Promise<VerifyResult>;
}

/** Case/whitespace-insensitive Riot ID key (mirrors the backend's `_fold`). */
function fold(value: string): string {
  return value.trim().toLowerCase();
}

export async function ensureActiveAccountLinked(
  args: EnsureLinkedArgs,
  deps: EnsureLinkedDeps = {},
): Promise<EnsureLinkedResult> {
  const http = {
    ...(deps.fetch !== undefined ? { fetch: deps.fetch } : {}),
    ...(deps.backendUrl !== undefined ? { backendUrl: deps.backendUrl } : {}),
  };
  const list = deps.listAccounts ?? ((t: string) => listMyAccounts({ accessToken: t }, http));
  const verify =
    deps.verify ??
    ((t: string) =>
      verifyDesktop({ accessToken: t }, { ...http, readSummoner: async () => args.summoner }));

  const { gameName, tagLine } = args.summoner;
  const riotId = `${gameName}#${tagLine}`;

  const mine = await list(args.accessToken);
  if (!mine.ok) {
    return { ok: false, status: mine.status, detail: mine.detail };
  }
  const known = mine.accounts.some(
    (a) =>
      a.verified &&
      a.gameName !== null &&
      a.tagLine !== null &&
      fold(a.gameName) === fold(gameName) &&
      fold(a.tagLine) === fold(tagLine),
  );
  if (known) {
    return { ok: true, outcome: "already-linked", riotId };
  }

  const linked = await verify(args.accessToken);
  if (!linked.ok) {
    return { ok: false, status: linked.status, detail: linked.detail };
  }
  return { ok: true, outcome: "linked", riotId };
}
