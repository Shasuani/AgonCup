/**
 * Write-gating + human-pacing — the load-bearing ToS mitigations (PLAN.md §3.6,
 * §11 risk row "LCU write-automation ToS exposure").
 *
 * EVERY write (createCustomLobby / invite / autoAcceptInvite acceptance) routes
 * through {@link WriteGate.acquire}. It enforces, in order:
 *   1. kill-switch          — engaged ⇒ refuse (fleet-wide or local). (§3.6.1)
 *   2. per-session opt-in    — default-off; must be explicitly granted. (§3.6)
 *   3. presence gate         — no AFK action when required. (§3.6.3)
 *   4. rolling rate-cap       — `maxWritesPerMinute` over a 60s sliding window.
 *   5. human pacing delay     — `minWriteDelayMs` + uniform jitter, so writes
 *                              look event-driven, never lockstep bursts.
 *
 * Refusals are surfaced as {@link WriteRefusedError} so callers can degrade to
 * the manual lobby UI rather than crash.
 */

import type { Clock, RandomSource, Sleeper } from './transport.js';

/** Reasons a write can be refused (for the manual-fallback degrade path). */
export type WriteRefusalReason =
  | 'kill-switch-engaged'
  | 'opt-in-not-granted'
  | 'presence-required'
  | 'rate-cap-exceeded';

/** Raised when a write is gated off; carries a machine-readable reason. */
export class WriteRefusedError extends Error {
  override readonly name = 'WriteRefusedError';
  readonly reason: WriteRefusalReason;
  constructor(reason: WriteRefusalReason) {
    super(`LCU write refused: ${reason}`);
    this.reason = reason;
  }
}

/** Pacing/rate-cap parameters (mirror of `LcuPacingConfig` write-relevant bits). */
export interface WriteGatePacing {
  readonly minWriteDelayMs: number;
  readonly writeJitterMs: number;
  readonly maxWritesPerMinute: number;
}

/** Live gate state read on every acquire (config + dynamic presence). */
export interface WriteGateState {
  readonly killSwitchEngaged: boolean;
  readonly writeAutomationOptIn: boolean;
  readonly requirePresenceForWrites: boolean;
}

/**
 * Presence probe — returns whether the user is currently at-keyboard /
 * client-focused. Injected because the real signal (OS focus / input idle) is
 * platform-specific; tests supply a deterministic stub. Only consulted when
 * `requirePresenceForWrites` is true.
 */
export interface PresenceProbe {
  isPresent(): boolean;
}

const ROLLING_WINDOW_MS = 60_000;

/**
 * Stateful gate enforcing the §3.6 write invariants. One instance per adapter;
 * `acquire()` is awaited immediately before each write IO and either resolves
 * (after applying the pacing delay) or rejects with {@link WriteRefusedError}.
 */
export class WriteGate {
  readonly #clock: Clock;
  readonly #sleeper: Sleeper;
  readonly #random: RandomSource;
  readonly #pacing: WriteGatePacing;
  readonly #presence: PresenceProbe;
  /** Epoch-ms timestamps of writes admitted within the rolling window. */
  readonly #recentWrites: number[] = [];

  constructor(args: {
    clock: Clock;
    sleeper: Sleeper;
    random: RandomSource;
    pacing: WriteGatePacing;
    presence: PresenceProbe;
  }) {
    this.#clock = args.clock;
    this.#sleeper = args.sleeper;
    this.#random = args.random;
    this.#pacing = args.pacing;
    this.#presence = args.presence;
  }

  /** Drop window entries older than 60s so the rate-cap is a true sliding count. */
  #prune(nowMs: number): void {
    const cutoff = nowMs - ROLLING_WINDOW_MS;
    while (this.#recentWrites.length > 0 && this.#recentWrites[0]! <= cutoff) {
      this.#recentWrites.shift();
    }
  }

  /** Compute the human-pacing delay: floor + uniform jitter in `[0, jitter)`. */
  #pacingDelayMs(): number {
    const jitter = Math.floor(this.#random.next() * this.#pacing.writeJitterMs);
    return this.#pacing.minWriteDelayMs + jitter;
  }

  /**
   * Gate + pace a single write. Throws {@link WriteRefusedError} when blocked;
   * otherwise records the write against the rolling cap and sleeps the pacing
   * delay before resolving (so the actual IO is human-paced).
   */
  async acquire(state: WriteGateState): Promise<void> {
    if (state.killSwitchEngaged) {
      throw new WriteRefusedError('kill-switch-engaged');
    }
    if (!state.writeAutomationOptIn) {
      throw new WriteRefusedError('opt-in-not-granted');
    }
    if (state.requirePresenceForWrites && !this.#presence.isPresent()) {
      throw new WriteRefusedError('presence-required');
    }

    const nowMs = this.#clock.nowMs();
    this.#prune(nowMs);
    if (this.#recentWrites.length >= this.#pacing.maxWritesPerMinute) {
      throw new WriteRefusedError('rate-cap-exceeded');
    }

    // Admit and record BEFORE sleeping so concurrent acquires share the cap.
    this.#recentWrites.push(nowMs);
    await this.#sleeper.sleep(this.#pacingDelayMs());
  }
}

/**
 * Bounded exponential backoff with jitter for re-invite/re-create retries
 * (§3.6.1 / §3.6.3). Attempt is 0-based: delay = min(base * 2^attempt, cap)
 * then a uniform jitter in `[0, computed)` is applied so retries de-correlate.
 */
export function backoffDelayMs(
  attempt: number,
  baseMs: number,
  capMs: number,
  random: RandomSource,
): number {
  const exp = baseMs * 2 ** Math.max(0, attempt);
  const capped = Math.min(exp, capMs);
  return Math.floor(random.next() * capped);
}
