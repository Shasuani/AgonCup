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

import type { GameflowPhase, LcuAdapter } from "@lol/lcu";
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
    if (phase === TERMINAL_PHASE && !this.#reported) {
      await this.#captureAndReport();
    }
  }

  async #captureAndReport(): Promise<void> {
    this.#reported = true; // single-shot: the eog block clears fast (§3.6.5).
    try {
      const report = await this.#adapter.captureEndOfGame();
      await this.#transport.sendReport({
        matchId: this.#matchId,
        hostEpoch: this.#currentEpoch,
        report,
      });
    } catch (err) {
      // Capture/report is best-effort; a missing report degrades to "honest
      // missing" in the corroboration quorum, never a crash.
      this.#reported = false; // allow a retry on the next tick within the window.
      void errorMessage(err);
    } finally {
      this.#cancelHeartbeat?.();
      this.#cancelHeartbeat = null;
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
