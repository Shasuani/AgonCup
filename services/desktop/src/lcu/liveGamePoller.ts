/**
 * Live-game winner poller (Documented `:2999` surface, READ-ONLY).
 *
 * While a custom game is running, the local Live Client Data port already
 * narrates the two moments a tournament player is waiting on: **first blood**
 * (which *decides* a 1v1 ARAM duel — `GameMode.decides_on_first_blood`) and
 * **game end** (which decides everything else). The desktop was capturing that
 * stream for corroboration and telling the player nothing; this poller is the
 * display half — the renderer's winner banner reads from here.
 *
 * Display-only by design: the same events ride the (forgeable) corroboration
 * report, and the RECORDED result still comes from cross-client quorum + admin
 * override (PLAN.md §3.6.4–§3.6.6). This never feeds adjudication; it only
 * tells the human at this desk what their own client just saw.
 *
 * Scorer resolution mirrors `lol_core.adjudication.derive_first_blood_team`:
 * the `FirstBlood` event's `Recipient` when the capture kept it, else the
 * killer of the `ChampionKill` nearest in time (±2s — the FirstBlood rides on a
 * same-tick kill). "That's you" is a display-name comparison against the LCU's
 * own Riot ID — advisory, like every name in this stream.
 *
 * Same injectable shape as {@link ChampSelectPoller}: constructing polls
 * nothing, {@link start} kicks the timer, {@link tick} runs one probe.
 */

import type { GameflowPhase, LiveClientEventCapture, LiveClientKillEvent } from "@lol/lcu";
import type { PollerTimers } from "./statusPoller.js";

/** First blood, as the winner banner shows it. */
export interface LiveGameFirstBlood {
  /** Display name of the scorer, verbatim from the stream (`Recipient`/killer). */
  readonly scorer: string;
  /** Whether the scorer is THIS player; null when the local name is unknown. */
  readonly isYou: boolean | null;
  /** In-game seconds when it happened (for the "at 1:23" line). */
  readonly atSeconds: number;
}

/** What the renderer shows for the running (or just-finished) game. */
export interface LiveGameView {
  /** `in_game` while the game runs; `ended` once it is over. */
  readonly status: "in_game" | "ended";
  /** Set the moment first blood is scored (decides a 1v1 duel on the spot). */
  readonly firstBlood: LiveGameFirstBlood | null;
  /** The LOCAL player's outcome from the `GameEnd` event; null until known. */
  readonly result: "win" | "loss" | null;
}

const DEFAULT_TIMERS: PollerTimers = {
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (h) => clearInterval(h as ReturnType<typeof setInterval>),
};

export interface LiveGamePollerConfig {
  /** The adapter's gameflow-phase read (RE; already polled by every controller). */
  readonly readPhase: () => Promise<GameflowPhase>;
  /** The adapter's `:2999` eventdata capture (Documented, read-only). */
  readonly readEvents: () => Promise<LiveClientEventCapture>;
  /** The LCU's own Riot ID game name, for the "that's you" verdict (null = unknown). */
  readonly readLocalGameName: () => Promise<string | null>;
  /** Called whenever the rendered view CHANGES (including to/from `null`). */
  readonly onUpdate: (view: LiveGameView | null) => void;
  /** Poll cadence; clamped to the 2–5s band (default 3s). */
  readonly intervalMs?: number;
  readonly timers?: PollerTimers;
}

/** Phases during which the `:2999` port is (or is about to be) alive. */
const LIVE_PHASES: ReadonlySet<GameflowPhase> = new Set(["GameStart", "InProgress", "Reconnect"]);

/** The game is over but this client still knows WHICH game it was. */
const OVER_PHASES: ReadonlySet<GameflowPhase> = new Set([
  "WaitingForStats",
  "PreEndOfGame",
  "EndOfGame",
]);

/**
 * Phases that mean a NEW game is forming — the one moment the previous game's
 * banner must leave the screen. Deliberately NOT `Lobby`/`None`: players land
 * there seconds after a game ends, and clearing then would flash the winner for
 * one poll tick and wipe it before anyone reads it.
 */
const NEW_CYCLE_PHASES: ReadonlySet<GameflowPhase> = new Set([
  "Matchmaking",
  "ReadyCheck",
  "ChampSelect",
]);

/** FirstBlood rides on a ChampionKill at the same tick (mirror of adjudication). */
const FIRST_BLOOD_KILL_TOLERANCE_S = 2.0;

function clampInterval(ms: number | undefined): number {
  const v = ms ?? 3_000;
  return Math.min(5_000, Math.max(2_000, v));
}

/** Casefold + trim, with the `GameName#TAG` → bare-name fallback both sides. */
function sameDisplayName(a: string, b: string): boolean {
  const norm = (s: string): string => s.trim().toLowerCase();
  const bare = (s: string): string => norm(s.includes("#") ? (s.split("#", 1)[0] ?? s) : s);
  return norm(a) === norm(b) || bare(a) === bare(b);
}

/** Resolve who scored first blood from one capture (Recipient, else paired kill). */
function firstBloodScorer(
  events: readonly LiveClientKillEvent[],
): { scorer: string; atSeconds: number } | null {
  const ordered = [...events].sort((a, b) => a.eventTime - b.eventTime || a.eventId - b.eventId);
  const fb = ordered.find((e) => e.eventName === "FirstBlood");
  if (fb === undefined) {
    return null;
  }
  if (fb.recipient !== undefined && fb.recipient.trim() !== "") {
    return { scorer: fb.recipient, atSeconds: fb.eventTime };
  }
  const kills = ordered
    .filter((e) => e.eventName === "ChampionKill" && (e.killerName ?? "").trim() !== "")
    .sort(
      (a, b) =>
        Math.abs(a.eventTime - fb.eventTime) - Math.abs(b.eventTime - fb.eventTime) ||
        a.eventTime - b.eventTime ||
        a.eventId - b.eventId,
    );
  const paired = kills.find((k) => Math.abs(k.eventTime - fb.eventTime) <= FIRST_BLOOD_KILL_TOLERANCE_S);
  if (paired?.killerName === undefined) {
    return null;
  }
  return { scorer: paired.killerName, atSeconds: fb.eventTime };
}

/** The local player's outcome, from the `GameEnd` event when it was seen. */
function gameEndResult(events: readonly LiveClientKillEvent[]): "win" | "loss" | null {
  const end = events.find((e) => e.eventName === "GameEnd");
  if (end === undefined || end.result === undefined) {
    return null;
  }
  return end.result === "Win" ? "win" : "loss";
}

/**
 * Periodic live-game watcher. Emits {@link LiveGameView} transitions via
 * `onUpdate`, deduped on content; {@link lastView} answers a renderer that
 * mounts after the last change (pull complements push).
 */
export class LiveGamePoller {
  readonly #config: LiveGamePollerConfig;
  readonly #timers: PollerTimers;
  readonly #intervalMs: number;
  #handle: unknown = null;
  #inFlight = false;
  /** JSON of the last emitted view (dedupe key); undefined before first emit. */
  #lastJson: string | undefined = undefined;
  #view: LiveGameView | null = null;
  /** Freshest capture seen this game — kept because the port dies on game exit. */
  #events: readonly LiveClientKillEvent[] = [];
  /** Local Riot ID game name: null until read; load failures retry per tick. */
  #localName: string | null = null;

  constructor(config: LiveGamePollerConfig) {
    this.#config = config;
    this.#timers = config.timers ?? DEFAULT_TIMERS;
    this.#intervalMs = clampInterval(config.intervalMs);
  }

  /** The last computed view (`null` = nothing to show). For late subscribers. */
  get lastView(): LiveGameView | null {
    return this.#view;
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

  /** Stop polling. The view/dedupe state is retained (a restart re-dedupes). */
  stop(): void {
    if (this.#handle !== null) {
      this.#timers.clearInterval(this.#handle);
      this.#handle = null;
    }
  }

  /**
   * Run a single probe and emit on change. A phase-read failure keeps the
   * current view — a flapping LCU mid-game must not wipe a banner the player
   * is reading, and the final banner staying up after the client closes is the
   * point of having captured it.
   */
  async tick(): Promise<LiveGameView | null> {
    if (this.#inFlight) {
      return this.#view;
    }
    this.#inFlight = true;
    try {
      let phase: GameflowPhase;
      try {
        phase = await this.#config.readPhase();
      } catch {
        return this.#view; // unreadable client: hold what we have.
      }

      if (NEW_CYCLE_PHASES.has(phase)) {
        // A new game is forming — the previous game's story is over.
        this.#events = [];
        this.#view = null;
      } else if (LIVE_PHASES.has(phase)) {
        await this.#captureEvents();
        await this.#ensureLocalName();
        this.#view = this.#compute(gameEndResult(this.#events) !== null ? "ended" : "in_game");
      } else if (OVER_PHASES.has(phase) && this.#view !== null) {
        // Game over; the port may already be gone. Freeze what we saw as final.
        this.#view = this.#compute("ended");
      } else if (this.#view?.status === "in_game") {
        // InProgress -> Lobby/None without an end phase (dodge, remake, crash):
        // the game is gone; freeze rather than pretend it is still running.
        this.#view = this.#compute("ended");
      }
      // Lobby/None with nothing live: hold the (possibly final) view as-is.

      return this.#emitIfChanged();
    } finally {
      this.#inFlight = false;
    }
  }

  async #captureEvents(): Promise<void> {
    try {
      const capture = await this.#config.readEvents();
      this.#events = capture.events;
    } catch {
      // Port not up yet / mid-game blip — keep the freshest capture we have.
    }
  }

  /** Lazy local-name read; a failure leaves null and retries on a later tick. */
  async #ensureLocalName(): Promise<void> {
    if (this.#localName !== null) {
      return;
    }
    try {
      this.#localName = await this.#config.readLocalGameName();
    } catch {
      this.#localName = null;
    }
  }

  #compute(status: "in_game" | "ended"): LiveGameView {
    const fb = firstBloodScorer(this.#events);
    return {
      status,
      firstBlood:
        fb === null
          ? null
          : {
              scorer: fb.scorer,
              isYou: this.#localName === null ? null : sameDisplayName(fb.scorer, this.#localName),
              atSeconds: fb.atSeconds,
            },
      result: gameEndResult(this.#events),
    };
  }

  #emitIfChanged(): LiveGameView | null {
    const json = JSON.stringify(this.#view);
    if (json !== this.#lastJson) {
      this.#lastJson = json;
      this.#config.onUpdate(this.#view);
    }
    return this.#view;
  }
}
