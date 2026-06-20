/**
 * Backend <-> desktop orchestration wire contract (PLAN.md §3.6.1).
 *
 * The TypeScript mirror of the `lol-realtime` Pydantic lobby envelopes. These are
 * hand-maintained for now; they should eventually be GENERATED from the
 * `lol_realtime` exported JSON-schema (see `make gen-clients`). Every command and
 * client->server message carries `matchId` + `hostEpoch` so the client can fence
 * stale-epoch commands locally and the backend can reject stale acks/heartbeats.
 *
 * `@lol/lcu` symbols are imported as TYPES ONLY, so the compiled JS has no runtime
 * dependency on the adapter package (the controller is unit-testable with a fake
 * adapter under plain `node --test`).
 */

import type {
  AutoAcceptInviteFilter,
  CustomLobbySpec,
  EogReport,
  GameflowPhase,
  LcuLobbyId,
} from "@lol/lcu";

/** Discriminator for a backend->client lobby command. */
export type CommandKind = "create_lobby" | "promote_host" | "auto_accept" | "abort";

interface BaseCommand {
  readonly commandId: string;
  readonly matchId: string;
  /** Host lease the command is bound to; the client rejects a stale epoch. */
  readonly hostEpoch: number;
  readonly kind: CommandKind;
}

/** Become host and create the lobby (initial designation). */
export interface CreateLobbyCommand extends BaseCommand {
  readonly kind: "create_lobby";
  readonly spec: CustomLobbySpec;
}

/** Become host after a failover and RE-create the lobby at a new epoch. */
export interface PromoteHostCommand extends BaseCommand {
  readonly kind: "promote_host";
  readonly spec: CustomLobbySpec;
}

/** Guest: arm auto-accept for invites from the designated host. */
export interface AutoAcceptCommand extends BaseCommand {
  readonly kind: "auto_accept";
  readonly filter: AutoAcceptInviteFilter;
}

/** Tear down all lobby automation (manual fallback / match cancelled). */
export interface AbortCommand extends BaseCommand {
  readonly kind: "abort";
  readonly reason?: string;
}

export type LobbyCommand =
  | CreateLobbyCommand
  | PromoteHostCommand
  | AutoAcceptCommand
  | AbortCommand;

/** Client->server acknowledgement of a command (epoch-fenced server-side). */
export interface LobbyAck {
  readonly commandId: string;
  readonly matchId: string;
  readonly hostEpoch: number;
  readonly ok: boolean;
  readonly lcuLobbyId?: LcuLobbyId;
  /** Failure reason when `ok` is false (e.g. writes refused -> manual fallback). */
  readonly error?: string;
}

/** Periodic client->server liveness + phase (drives the failover watchdog). */
export interface HostHeartbeat {
  readonly matchId: string;
  readonly hostEpoch: number;
  readonly phase: GameflowPhase;
  readonly isHost: boolean;
  readonly sentAtMs: number;
}

/** Client->server end-of-game report (one of the 10 corroboration inputs). */
export interface ResultReportMessage {
  readonly matchId: string;
  readonly hostEpoch: number;
  readonly report: EogReport;
}

/**
 * Transport seam to the backend realtime plane. The controller depends only on
 * this interface, so the WebSocket implementation is swappable and the controller
 * is testable with an in-memory fake.
 */
export interface BackendTransport {
  /** Register a handler for inbound commands; returns an unsubscribe fn. */
  onCommand(handler: (command: LobbyCommand) => void): () => void;
  sendAck(ack: LobbyAck): Promise<void>;
  sendHeartbeat(heartbeat: HostHeartbeat): Promise<void>;
  sendReport(report: ResultReportMessage): Promise<void>;
}

/** Injectable periodic scheduler (real impl wraps setInterval; tests fake it). */
export interface Scheduler {
  /** Run `fn` every `ms`; returns a cancel fn. */
  every(ms: number, fn: () => void): () => void;
}

/** Injectable wall clock (tests pass a deterministic one). */
export interface Clock {
  nowMs(): number;
}
