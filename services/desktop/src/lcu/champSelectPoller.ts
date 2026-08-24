/**
 * Champ-select status poller (read-only spectator surface).
 *
 * While the local client sits in champion select, its OWN LCU exposes the full
 * board — in a custom game both teams are visible to every member — so each
 * desktop renders the live pick/lock state locally: no server round-trip and
 * nothing forgeable broadcast to anyone else. One cheap loopback GET per tick;
 * outside champ select the session read collapses to `null` (board hidden).
 *
 * Champion ids become names via the client's own locally-served catalogue
 * (`getChampionSummary`), loaded lazily on the first board and cached for the
 * process lifetime; until it loads (or if it never does) the view ships ids
 * only and the renderer falls back to `#<id>` labels.
 *
 * Same injectable shape as {@link LcuStatusPoller}: constructing polls nothing,
 * {@link start} kicks the timer, {@link tick} runs one probe (tests drive it).
 */

import type { ChampSelectSnapshot } from "@lol/lcu";
import type { PollerTimers } from "./statusPoller.js";

/** What the renderer shows for one seat — labels only, no identity fields. */
export interface ChampSelectViewMember {
  readonly cellId: number;
  /** 'my' | 'their', relative to THIS client's session. */
  readonly side: "my" | "their";
  readonly gameName?: string;
  readonly championId: number;
  readonly championName?: string;
  readonly locked: boolean;
}

/** The champ-select board as pushed to the renderer (`null` = not in select). */
export interface ChampSelectView {
  readonly timerPhase?: string;
  readonly members: readonly ChampSelectViewMember[];
}

const DEFAULT_TIMERS: PollerTimers = {
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (h) => clearInterval(h as ReturnType<typeof setInterval>),
};

export interface ChampSelectPollerConfig {
  /** The adapter's champ-select session read (`null` outside champ select). */
  readonly readSession: () => Promise<ChampSelectSnapshot | null>;
  /** The adapter's champion-id -> name catalogue read (loaded once, lazily). */
  readonly readChampionNames: () => Promise<ReadonlyMap<number, string>>;
  /** Called whenever the rendered view CHANGES (including to/from `null`). */
  readonly onUpdate: (view: ChampSelectView | null) => void;
  /** Poll cadence; clamped to the 2–5s band (default 3s). */
  readonly intervalMs?: number;
  readonly timers?: PollerTimers;
}

function clampInterval(ms: number | undefined): number {
  const v = ms ?? 3_000;
  return Math.min(5_000, Math.max(2_000, v));
}

/**
 * Periodic champ-select board poller. Emits {@link ChampSelectView} transitions
 * via `onUpdate`, deduped on content so an unchanged board stays silent.
 */
export class ChampSelectPoller {
  readonly #config: ChampSelectPollerConfig;
  readonly #timers: PollerTimers;
  readonly #intervalMs: number;
  #handle: unknown = null;
  /** JSON of the last emitted view (dedupe key); undefined before first emit. */
  #lastJson: string | undefined = undefined;
  #inFlight = false;
  /** Champion catalogue: null until loaded; load failures retry on demand. */
  #names: ReadonlyMap<number, string> | null = null;
  #namesLoading = false;

  constructor(config: ChampSelectPollerConfig) {
    this.#config = config;
    this.#timers = config.timers ?? DEFAULT_TIMERS;
    this.#intervalMs = clampInterval(config.intervalMs);
  }

  /** Begin polling: an immediate tick, then every interval. Idempotent. */
  start(): void {
    if (this.#handle !== null) {
      return;
    }
    void this.tick();
    this.#handle = this.#timers.setInterval(() => {
      void this.tick();
    }, this.#intervalMs);
  }

  /** Stop polling. The dedupe state is retained (a restart re-dedupes). */
  stop(): void {
    if (this.#handle !== null) {
      this.#timers.clearInterval(this.#handle);
      this.#handle = null;
    }
  }

  /**
   * Run a single probe, emit on change, and return the computed view. A session
   * read failure is treated as "no board" (fail-safe — never throws).
   */
  async tick(): Promise<ChampSelectView | null> {
    if (this.#inFlight) {
      return null;
    }
    this.#inFlight = true;
    let view: ChampSelectView | null;
    try {
      view = await this.#compute();
    } catch {
      view = null;
    } finally {
      this.#inFlight = false;
    }
    const json = JSON.stringify(view);
    if (json !== this.#lastJson) {
      this.#lastJson = json;
      this.#config.onUpdate(view);
    }
    return view;
  }

  async #compute(): Promise<ChampSelectView | null> {
    const snapshot = await this.#config.readSession();
    if (snapshot === null) {
      return null;
    }
    await this.#ensureNames();
    const members = snapshot.members.map((m): ChampSelectViewMember => {
      const championName = m.championId > 0 ? this.#names?.get(m.championId) : undefined;
      return {
        cellId: m.cellId,
        side: m.side,
        ...(m.gameName === undefined ? {} : { gameName: m.gameName }),
        championId: m.championId,
        ...(championName === undefined ? {} : { championName }),
        locked: m.locked,
      };
    });
    return {
      ...(snapshot.timerPhase === undefined ? {} : { timerPhase: snapshot.timerPhase }),
      members,
    };
  }

  /** Lazy one-shot catalogue load; a failure leaves it null to retry later. */
  async #ensureNames(): Promise<void> {
    if (this.#names !== null || this.#namesLoading) {
      return;
    }
    this.#namesLoading = true;
    try {
      this.#names = await this.#config.readChampionNames();
    } catch {
      this.#names = null; // ids-only view this tick; retried on the next board.
    } finally {
      this.#namesLoading = false;
    }
  }
}
