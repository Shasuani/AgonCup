/**
 * Per-match desktop orchestration controller (PLAN.md §3.6.1).
 *
 * Drives the local League client (via the audited `@lol/lcu` adapter) from
 * backend lobby commands, and streams liveness + the end-of-game report back. It
 * is the client half of the autonomous match operation; the backend service +
 * worker watchdog own host designation and failover decisions. This controller:
 *
 *  - **fences stale epochs**: a command bound to an epoch older than the one it
 *    has already adopted is rejected (the deposed-host / split-brain guard mirror
 *    of the server-side fence);
 *  - **fails soft**: every LCU write goes through the adapter's opt-in / kill-
 *    switch / presence gate — when a write is refused or errors, the controller
 *    ACKs `ok:false` so the backend falls back to the manual lobby UI, and never
 *    throws into the event loop;
 *  - **reports once**: on `EndOfGame` it captures the (forgeable) eog block + live
 *    kill stream and sends a single corroboration report.
 *
 * `@lol/lcu` is imported as TYPES ONLY — no runtime dependency, so this is
 * unit-testable with a fake adapter under `node --test`.
 */

import type { GameflowPhase, LcuAdapter, LiveClientEventCapture } from "@lol/lcu";
import type {
  AutoAcceptCommand,
  BackendTransport,
  Clock,
  CreateLobbyCommand,
  LobbyCommand,
  PromoteHostCommand,
  Scheduler,
} from "./protocol.js";

export interface ControllerConfig {
  /** The match this controller services; commands for other matches are ignored. */
  readonly matchId: string;
  /** Heartbeat cadence while pre-game/in-game (PLAN.md §3.6.1 ~2-3s). */
  readonly heartbeatMs: number;
}

const TERMINAL_PHASE: GameflowPhase = "EndOfGame";
/** Live-Client Data (`:2999`) is only up while the game is actually running. */
const IN_PROGRESS_PHASE: GameflowPhase = "InProgress";

export class OrchestrationController {
  readonly #adapter: LcuAdapter;
  readonly #transport: BackendTransport;
  readonly #scheduler: Scheduler;
  readonly #clock: Clock;
  readonly #matchId: string;
  readonly #heartbeatMs: number;

  #currentEpoch = -1;
  #isHost = false;
  #reported = false;
  /**
   * Latest best-effort Live-Client (`:2999`) kill-stream capture, refreshed on
   * each `InProgress` tick and bundled into the single eog report (§3.6.4). The
   * port dies on game exit, so we keep the freshest snapshot seen while in-game.
   */
  #liveClientEvents: LiveClientEventCapture | null = null;
  #unsubscribeCommands: (() => void) | null = null;
  #unsubscribeInvite: (() => void) | null = null;
  #cancelHeartbeat: (() => void) | null = null;

  constructor(deps: {
    adapter: LcuAdapter;
    transport: BackendTransport;
    scheduler: Scheduler;
    clock: Clock;
    config: ControllerConfig;
  }) {
    this.#adapter = deps.adapter;
    this.#transport = deps.transport;
    this.#scheduler = deps.scheduler;
    this.#clock = deps.clock;
    this.#matchId = deps.config.matchId;
    this.#heartbeatMs = deps.config.heartbeatMs;
  }

  /** Begin consuming commands and emitting heartbeats. Idempotent. */
  start(): void {
    if (this.#unsubscribeCommands === null) {
      this.#unsubscribeCommands = this.#transport.onCommand((c) => {
        // Fire-and-forget: handler errors are contained inside #handle.
        void this.#handle(c);
      });
    }
    if (this.#cancelHeartbeat === null) {
      this.#cancelHeartbeat = this.#scheduler.every(this.#heartbeatMs, () => {
        void this.#tick();
      });
    }
  }

  /** Tear everything down (match ended, aborted, or app shutdown). */
  stop(): void {
    this.#unsubscribeCommands?.();
    this.#unsubscribeInvite?.();
    this.#cancelHeartbeat?.();
    this.#unsubscribeCommands = null;
    this.#unsubscribeInvite = null;
    this.#cancelHeartbeat = null;
  }

  // --- command handling --------------------------------------------------- //
  async #handle(command: LobbyCommand): Promise<void> {
    if (command.matchId !== this.#matchId) {
      return; // not ours — another controller (or stale) owns it.
    }
    if (command.hostEpoch < this.#currentEpoch) {
      await this.#ack(command, false, "stale host epoch");
      return;
    }
    this.#currentEpoch = command.hostEpoch;

    switch (command.kind) {
      case "create_lobby":
      case "promote_host":
        await this.#hostCreate(command);
        break;
      case "auto_accept":
        await this.#guestAutoAccept(command);
        break;
      case "abort":
        this.#unsubscribeInvite?.();
        this.#unsubscribeInvite = null;
        this.#isHost = false;
        await this.#ack(command, true);
        break;
    }
  }

  async #hostCreate(command: CreateLobbyCommand | PromoteHostCommand): Promise<void> {
    try {
      const handle = await this.#adapter.createCustomLobby(command.spec);
      await this.#adapter.invite(command.spec.invitees);
      this.#isHost = true;
      await this.#transport.sendAck({
        commandId: command.commandId,
        matchId: command.matchId,
        hostEpoch: command.hostEpoch,
        ok: true,
        lcuLobbyId: handle.lobbyId,
      });
    } catch (err) {
      // Writes refused (gate/kill-switch) or an LCU error -> backend falls back
      // to the manual lobby name+password flow. Never throw into the loop.
      await this.#ack(command, false, errorMessage(err));
    }
  }

  async #guestAutoAccept(command: AutoAcceptCommand): Promise<void> {
    try {
      this.#unsubscribeInvite?.();
      this.#unsubscribeInvite = await this.#adapter.autoAcceptInvite(command.filter);
      await this.#ack(command, true);
    } catch (err) {
      await this.#ack(command, false, errorMessage(err));
    }
  }

  // --- heartbeat + reporting --------------------------------------------- //
  async #tick(): Promise<void> {
    let phase: GameflowPhase;
    try {
      phase = await this.#adapter.getGameflowPhase();
    } catch {
      return; // transient LCU read failure — skip this tick, stay alive.
    }
    await this.#safe(() =>
      this.#transport.sendHeartbeat({
        matchId: this.#matchId,
        hostEpoch: this.#currentEpoch,
        phase,
        isHost: this.#isHost,
        sentAtMs: this.#clock.nowMs(),
      }),
    );
    if (phase === IN_PROGRESS_PHASE) {
      await this.#captureLiveEvents();
    }
    if (phase === TERMINAL_PHASE && !this.#reported) {
      await this.#captureAndReport();
    }
  }

  /**
   * Best-effort `:2999` kill-stream snapshot while in-game (§3.6.4). READ-ONLY,
   * soft-fail: a connection/parse failure (e.g. the port already died) keeps the
   * previous snapshot and never throws into the tick loop.
   */
  async #captureLiveEvents(): Promise<void> {
    try {
      this.#liveClientEvents = await this.#adapter.captureLiveClientEvents();
    } catch {
      // Port not up yet / already gone — keep the last good capture, stay alive.
    }
  }

  async #captureAndReport(): Promise<void> {
    this.#reported = true; // single-shot guard against double-report.
    try {
      // Bundle the latest live kill-stream into the eog report (§3.6.4/§3.6.5).
      const report = await this.#adapter.captureEndOfGame(
        undefined,
        this.#liveClientEvents ?? undefined,
      );
      await this.#transport.sendReport({
        matchId: this.#matchId,
        hostEpoch: this.#currentEpoch,
        report,
      });
      // Reported successfully: stop the heartbeat loop (orchestration is done).
      // We cancel ONLY here so that a capture/send FAILURE leaves the heartbeat
      // running and the retry below can actually fire on the next tick within the
      // brief eog window; the runtime tears the controller down at match end, so
      // the loop is always bounded.
      this.#cancelHeartbeat?.();
      this.#cancelHeartbeat = null;
    } catch (err) {
      // Best-effort: a failed capture/send degrades to "honest missing" in the
      // corroboration quorum, never a crash. Clear the guard so the next tick
      // retries while the eog block is still readable.
      this.#reported = false;
      void errorMessage(err);
    }
  }

  // --- helpers ------------------------------------------------------------ //
  async #ack(command: LobbyCommand, ok: boolean, error?: string): Promise<void> {
    await this.#safe(() =>
      this.#transport.sendAck({
        commandId: command.commandId,
        matchId: command.matchId,
        hostEpoch: command.hostEpoch,
        ok,
        ...(error === undefined ? {} : { error }),
      }),
    );
  }

  async #safe(fn: () => Promise<void>): Promise<void> {
    try {
      await fn();
    } catch {
      // Transport hiccup — swallow so a send failure never breaks orchestration.
    }
  }
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
