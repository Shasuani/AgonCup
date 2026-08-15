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
  CustomGameConfiguration,
  CustomLobbySpec,
  EogReport,
  GameflowPhase,
  HostEpoch,
  LcuLobbyId,
  SummonerId,
} from "@lol/lcu";

/**
 * Discriminator for a backend->client lobby command.
 *
 * These are the `type` literals of the `lol_realtime` Pydantic payloads, verbatim
 * — NOT a parallel vocabulary. This file previously discriminated on a `kind`
 * field carrying short names (`"create_lobby"`), which the backend never sent:
 * it emits `type: "create_lobby_command"`. Nothing matched, so every command fell
 * through the controller's switch in silence.
 */
export type CommandType =
  | "create_lobby_command"
  | "promote_host_command"
  | "invite_players_command"
  | "auto_accept_command"
  | "abort_command";

interface BaseCommand {
  readonly commandId: string;
  readonly matchId: string;
  /** Host lease the command is bound to; the client rejects a stale epoch. */
  readonly hostEpoch: number;
  readonly type: CommandType;
}

/**
 * The lobby spec exactly as it arrives on the wire (`lol_realtime.LobbySpec`).
 *
 * Deliberately NOT `@lol/lcu`'s {@link CustomLobbySpec}: that is the *adapter's*
 * input shape (nested `configuration`, a flat `invitees` array), while this is
 * the *backend's* output shape. Conflating them is what let the two drift apart
 * unnoticed. {@link toCustomLobbySpec} is the one place that converts.
 */
export interface WireLobbySpec {
  readonly gameMode: string;
  readonly mapId: number;
  readonly teamSize: number;
  readonly lobbyName: string;
  readonly lobbyPassword: string;
  /** summonerIds — what an LCU invite is addressed by. */
  readonly inviteeSummonerIds: readonly string[];
  /** PUUIDs for the same players (collision-immune; guards auto-accept). */
  readonly inviteePuuids: readonly string[];
}

/** Become host and create the lobby (initial designation). */
export interface CreateLobbyCommand extends BaseCommand {
  readonly type: "create_lobby_command";
  readonly spec: WireLobbySpec;
}

/**
 * Become host after a failover and RE-create the lobby at a new epoch.
 *
 * Carries no spec: the backend's `PromoteHostCommand` sends only the new epoch +
 * successor PUUID. A successor re-creates **verbatim** from the spec it already
 * holds (PLAN.md §3.6.2); a client that never saw one — because it started after
 * the original create — acks `ok:false` so the backend falls back to the manual
 * lobby rather than creating a lobby with the wrong invitees.
 */
export interface PromoteHostCommand extends BaseCommand {
  readonly type: "promote_host_command";
  readonly newHostEpoch: number;
  readonly newHostPuuid: string;
}

/** Host: invite (or re-invite) this exact roster. */
export interface InvitePlayersCommand extends BaseCommand {
  readonly type: "invite_players_command";
  readonly toSummonerIds: readonly string[];
  readonly toPuuids: readonly string[];
}

/** Guest: arm auto-accept for invites from the designated host. */
export interface AutoAcceptCommand extends BaseCommand {
  readonly type: "auto_accept_command";
  /** PUUID of the designated host — the only inviter we auto-accept. */
  readonly expectedInviterPuuid: string;
}

/** Tear down all lobby automation (manual fallback / match cancelled). */
export interface AbortCommand extends BaseCommand {
  readonly type: "abort_command";
  readonly reason?: string;
}

export type LobbyCommand =
  | CreateLobbyCommand
  | PromoteHostCommand
  | InvitePlayersCommand
  | AutoAcceptCommand
  | AbortCommand;

/**
 * Convert a backend {@link WireLobbySpec} into the adapter's {@link CustomLobbySpec}.
 *
 * The two shapes are genuinely different — the backend sends the custom-game
 * config flat, the LCU wants it nested under `configuration`, and the invite list
 * is `inviteeSummonerIds` on one side and `invitees` on the other. `mutators.id: 1`
 * and `spectatorPolicy` are LCU-side constants (PLAN.md §3.6.2) the backend does
 * not carry, so they are filled here.
 */
export function toCustomLobbySpec(
  wire: WireLobbySpec,
  { matchId, hostEpoch }: { matchId: string; hostEpoch: number },
): CustomLobbySpec {
  const configuration: CustomGameConfiguration = {
    gameMode: wire.gameMode === "ARAM" ? "ARAM" : "CLASSIC",
    mapId: wire.mapId === 12 ? 12 : 11,
    teamSize: wire.teamSize === 1 ? 1 : 5,
    mutators: { id: 1 },
    spectatorPolicy: "AllAllowed",
  };
  return {
    matchId,
    hostEpoch: fromWire<HostEpoch>(hostEpoch),
    configuration,
    lobbyName: wire.lobbyName,
    lobbyPassword: wire.lobbyPassword,
    invitees: wire.inviteeSummonerIds.map((id) => fromWire<SummonerId>(id)),
  };
}

/**
 * Brand a value that arrived over the wire.
 *
 * `@lol/lcu`'s ids are branded types, so an arbitrary string cannot be passed
 * where a `SummonerId` is meant. JSON carries plain strings and numbers, so a
 * brand has to be applied somewhere — this is that somewhere, and the only one.
 * Keeping it a single named function instead of scattered `as` casts makes the
 * trust boundary greppable, and the brands keep doing their job everywhere else.
 */
export function fromWire<T>(value: string | number): T {
  return value as unknown as T;
}

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
