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

import type {
  GameflowPhase,
  HostEpoch,
  LcuAdapter,
  LiveClientEventCapture,
  Puuid,
  SummonerId,
} from "@lol/lcu";
import type {
  AutoAcceptCommand,
  BackendTransport,
  Clock,
  CreateLobbyCommand,
  InvitePlayersCommand,
  LobbyCommand,
  PromoteHostCommand,
  Scheduler,
  WireLobbySpec,
} from "./protocol.js";
import { fromWire, toCustomLobbySpec } from "./protocol.js";

export interface ControllerConfig {
  /** The match this controller services; commands for other matches are ignored. */
  readonly matchId: string;
  /** Heartbeat cadence while pre-game/in-game (PLAN.md §3.6.1 ~2-3s). */
  readonly heartbeatMs: number;
}

const TERMINAL_PHASE: GameflowPhase = "EndOfGame";
/** Live-Client Data (`:2999`) is only up while the game is actually running. */
const IN_PROGRESS_PHASE: GameflowPhase = "InProgress";
/** The host sits in the custom lobby while it fills. */
const LOBBY_PHASE: GameflowPhase = "Lobby";

/**
 * How long this client keeps watching for a lobby a PLAYER is making by hand
 * before giving up on inviting the roster for them.
 *
 * Generous, because the clock starts when the bracket does and the human on the
 * other end has to read a DM, alt-tab, and click through the custom-game screen.
 * Bounded, because the alternative is a client that polls forever for a match
 * everybody walked away from.
 */
const MANUAL_LOBBY_WAIT_MS = 15 * 60_000;

/** How often an in-game client shares the live scoreboard (display-only). */
const LIVE_SNAPSHOT_EVERY_MS = 5_000;

/**
 * Commands only the designated host acts on.
 *
 * `promote_host_command` is deliberately NOT here: its entire job is to make a
 * client the host, so gating it on already being one would make failover
 * unreachable. It carries its own target and is fenced by the epoch instead.
 */
const HOST_ONLY_COMMANDS: ReadonlySet<string> = new Set([
  "create_lobby_command",
  "invite_players_command",
]);

export class OrchestrationController {
  readonly #adapter: LcuAdapter;
  readonly #transport: BackendTransport;
  readonly #scheduler: Scheduler;
  readonly #clock: Clock;
  readonly #matchId: string;
  readonly #heartbeatMs: number;

  #currentEpoch = -1;
  #isHost = false;
  /**
   * Whether the backend named THIS player as the match's host/opener.
   *
   * Commands are published to a per-match channel that every one of the ten
   * clients is subscribed to, so "create this lobby" and "invite these people"
   * arrive at all of them. Without this flag every client obeyed: ten custom
   * games for one match, each with one player in it, and ten sets of invitations
   * crossing in flight.
   *
   * It comes from the backend's own answer (`GET /orchestration/matches/mine`
   * → `isHost`), which is the same row that decides who is DMed the credentials,
   * so the client cannot disagree with it. Default false — a match with no lobby
   * falls back to credentials the players already hold, which is recoverable;
   * ten lobbies is not.
   */
  #assignedHost = false;
  #reported = false;
  /**
   * Whether this match's mode is decided at FIRST BLOOD, not at the nexus (the
   * 1v1 ARAM duel — `GameMode.decides_on_first_blood`, PLAN.md §3.6.4). When
   * true, the corroboration report is sent the moment the live kill stream
   * shows a `FirstBlood` event — duelists leave after the deciding kill, so an
   * eog block may never exist to report from. Comes from the backend's own
   * match answer (`GET /orchestration/matches/mine` → `gameMode`), re-applied
   * like `setAssignedHost`. Default false — a 5v5 keeps the eog-only path.
   */
  #firstBloodDecides = false;
  /**
   * The last lobby spec the backend sent. A promotion carries no spec, so this is
   * what "re-create verbatim" (PLAN.md §3.6.2) re-creates from.
   */
  #lastSpec: WireLobbySpec | null = null;
  /**
   * Latest best-effort Live-Client (`:2999`) kill-stream capture, refreshed on
   * each `InProgress` tick and bundled into the single eog report (§3.6.4). The
   * port dies on game exit, so we keep the freshest snapshot seen while in-game.
   */
  #liveClientEvents: LiveClientEventCapture | null = null;
  /** When the live scoreboard was last read (throttle for {@link #shareLiveSnapshot}). */
  #lastLiveSnapshotMs: number | null = null;
  /**
   * An invite we were told to send before any lobby existed to send it into —
   * the manual-fallback path, where a player creates the custom game by hand.
   * Retried from the heartbeat tick until the lobby shows up or the wait expires.
   */
  #pendingInvite: { readonly command: InvitePlayersCommand; readonly deadlineMs: number } | null =
    null;
  #unsubscribeCommands: (() => void) | null = null;
  #unsubscribeInvite: (() => void) | null = null;
  #cancelHeartbeat: (() => void) | null = null;
  /**
   * Monotonic auto-accept arm counter. Arms race (fire-and-forget dispatch), and
   * an arm that loses must not leave an orphaned accept loop behind — see
   * `#guestAutoAccept`. Bumped by abort/stop so an in-flight arm dies too.
   */
  #armSeq = 0;

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

  /**
   * Tell this controller whether the backend named its player as the host/opener
   * of this match. Re-applied on every match poll, so a failover that moves the
   * lease is picked up without rebuilding the controller.
   */
  setAssignedHost(isHost: boolean): void {
    this.#assignedHost = isHost;
  }

  /**
   * Tell this controller whether first blood decides this match (1v1 duel).
   * Re-applied on every match poll, same as {@link setAssignedHost}.
   */
  setFirstBloodDecides(decides: boolean): void {
    this.#firstBloodDecides = decides;
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
    this.#armSeq++; // kill any auto-accept arm still resolving
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

    // Host-only work, on a channel everybody hears (see `#assignedHost`). A
    // client that was not named simply has nothing to do with these — no ack,
    // for the same reason a command for another match gets none: it was never
    // addressed to us.
    if (!this.#assignedHost && HOST_ONLY_COMMANDS.has(command.type)) {
      return;
    }

    switch (command.type) {
      case "create_lobby_command":
        await this.#hostCreate(command, command.spec);
        break;
      case "promote_host_command":
        // Re-create VERBATIM (PLAN.md §3.6.2) from the spec this client already
        // holds. Without one there is nothing faithful to re-create, so refuse and
        // let the backend surface the manual lobby instead of guessing a roster.
        if (this.#lastSpec === null) {
          await this.#ack(command, false, "no lobby spec to re-create from");
          break;
        }
        // Adopt the freshly-leased epoch. The command's own `hostEpoch` is the
        // one being SUPERSEDED (that is what it fences on); acting — and acking —
        // at it would be rejected server-side as a deposed host's ack.
        this.#currentEpoch = command.newHostEpoch;
        await this.#hostCreate(command, this.#lastSpec, command.newHostEpoch);
        break;
      case "invite_players_command":
        await this.#hostInvite(command);
        break;
      case "auto_accept_command":
        await this.#guestAutoAccept(command);
        break;
      case "abort_command":
        this.#armSeq++; // kill any arm still resolving, not just the installed one
        this.#unsubscribeInvite?.();
        this.#unsubscribeInvite = null;
        this.#pendingInvite = null;
        this.#isHost = false;
        await this.#ack(command, true);
        break;
    }
  }

  async #hostCreate(
    command: CreateLobbyCommand | PromoteHostCommand,
    wire: WireLobbySpec,
    epoch: number = command.hostEpoch,
  ): Promise<void> {
    try {
      const spec = toCustomLobbySpec(wire, {
        matchId: command.matchId,
        hostEpoch: epoch,
      });
      const handle = await this.#adapter.createCustomLobby(spec);
      await this.#adapter.invite(spec.invitees);
      this.#lastSpec = wire;
      this.#isHost = true;
      await this.#transport.sendAck({
        commandId: command.commandId,
        matchId: command.matchId,
        hostEpoch: epoch,
        ok: true,
        lcuLobbyId: handle.lobbyId,
      });
    } catch (err) {
      // Writes refused (gate/kill-switch) or an LCU error -> backend falls back
      // to the manual lobby name+password flow. Never throw into the loop.
      await this.#ack(command, false, errorMessage(err));
    }
  }

  /**
   * Invite the roster the backend named — waiting for a lobby if there is none yet.
   *
   * Two callers, one behaviour. After our own create, the lobby is already there
   * and this is the retry/repair lane. But when NOBODY's client is allowed to
   * host, the backend names one player and DMs them the name and password — and
   * if that named player is us, the lobby does not exist yet and will not until
   * that human clicks *Create custom game*.
   *
   * Inviting immediately in that case fails against an empty client, and failing
   * is the end of it — which left the manual path as nine people hunting for a
   * custom game by name. So an invite with no lobby is not an error, it is a
   * wait: parked and retried from the heartbeat tick (§3.6.1 manual fallback).
   *
   * The ack is deferred until the invites actually go out, because that is the
   * moment the statement "the roster was invited" becomes true.
   */
  async #hostInvite(command: InvitePlayersCommand): Promise<void> {
    let lobby: unknown = null;
    try {
      lobby = await this.#adapter.getCurrentLobby();
    } catch {
      // An unreadable client is not a "no": fall through and let the send decide.
      lobby = null;
    }
    if (lobby === null) {
      // Keep the FIRST deadline: replays re-issue this command on every
      // reconnect, and resetting the clock each time would turn a bounded wait
      // into an indefinite one.
      this.#pendingInvite = {
        command,
        deadlineMs: this.#pendingInvite?.deadlineMs ?? this.#clock.nowMs() + MANUAL_LOBBY_WAIT_MS,
      };
      return;
    }
    await this.#sendInvites(command);
  }

  /** Send one invite batch and ack its outcome. */
  async #sendInvites(command: InvitePlayersCommand): Promise<void> {
    try {
      await this.#adapter.invite(command.toSummonerIds.map((id) => fromWire<SummonerId>(id)));
      await this.#ack(command, true);
    } catch (err) {
      await this.#ack(command, false, errorMessage(err));
    }
  }

  /**
   * Poll for the hand-made lobby and invite the roster the moment it appears.
   *
   * Runs on the heartbeat rather than a timer of its own: the tick is already the
   * loop that asks the local client what it is doing, and a second timer would be
   * a second thing to cancel on every teardown path.
   */
  async #resumePendingInvite(): Promise<void> {
    const pending = this.#pendingInvite;
    if (pending === null) {
      return;
    }
    if (this.#clock.nowMs() >= pending.deadlineMs) {
      this.#pendingInvite = null;
      await this.#ack(pending.command, false, "no lobby was created to invite into");
      return;
    }
    let lobby: unknown;
    try {
      lobby = await this.#adapter.getCurrentLobby();
    } catch {
      return; // transient LCU read failure — try again next tick.
    }
    if (lobby === null) {
      return;
    }
    // Claim it before sending: a slow invite must not be started twice by the
    // next tick, which would double every invitation in the lobby.
    this.#pendingInvite = null;
    this.#isHost = true;
    await this.#sendInvites(pending.command);
  }

  async #guestAutoAccept(command: AutoAcceptCommand): Promise<void> {
    // Commands are dispatched fire-and-forget, so two arms (a live publish and
    // a subscribe replay, say) can interleave across the await below. The
    // sequence number makes the LAST arm the only survivor: a loop that comes
    // back to find itself superseded tears itself down instead of polling —
    // and accepting — forever as an orphan.
    const arm = ++this.#armSeq;
    try {
      this.#unsubscribeInvite?.();
      this.#unsubscribeInvite = null;
      // The LCU-side identifiers are nullable on the wire (hosts verified before
      // they were captured); pass only what is actually known so an absent field
      // can never satisfy the adapter's inviter fence.
      const lcuPuuid = command.expectedInviterLcuPuuid;
      const summonerId = command.expectedInviterSummonerId;
      const unsubscribe = await this.#adapter.autoAcceptInvite({
        expectedInviterPuuid: fromWire<Puuid>(command.expectedInviterPuuid),
        ...(lcuPuuid ? { expectedInviterLcuPuuid: fromWire<Puuid>(lcuPuuid) } : {}),
        ...(summonerId
          ? { expectedInviterSummonerId: fromWire<SummonerId>(summonerId) }
          : {}),
        hostEpoch: fromWire<HostEpoch>(command.hostEpoch),
      });
      if (arm !== this.#armSeq) {
        unsubscribe();
      } else {
        this.#unsubscribeInvite = unsubscribe;
      }
      await this.#ack(command, true);
    } catch (err) {
      await this.#ack(command, false, errorMessage(err));
    }
  }

  // --- heartbeat + reporting --------------------------------------------- //
  /**
   * Who the host's client sees in the lobby — the only evidence of the lobby
   * filling up, so the backend can move on from "invites are going out".
   * Best-effort: an unreadable lobby just leaves the beat without it.
   */
  async #lobbyMembers(): Promise<string[]> {
    try {
      const lobby = await this.#adapter.getCurrentLobby();
      return [...(lobby?.memberSummonerIds ?? [])];
    } catch {
      return [];
    }
  }

  async #tick(): Promise<void> {
    let phase: GameflowPhase;
    try {
      phase = await this.#adapter.getGameflowPhase();
    } catch {
      // Transient LCU read failure — no heartbeat this tick, but the parked
      // manual-path invite must not stall behind a flaky phase read.
      await this.#resumePendingInvite();
      return;
    }
    const members = this.#isHost && phase === LOBBY_PHASE ? await this.#lobbyMembers() : [];
    await this.#safe(() =>
      this.#transport.sendHeartbeat({
        matchId: this.#matchId,
        hostEpoch: this.#currentEpoch,
        phase,
        isHost: this.#isHost,
        sentAtMs: this.#clock.nowMs(),
        ...(members.length > 0 ? { lobbyMemberSummonerIds: members } : {}),
      }),
    );
    await this.#resumePendingInvite();
    if (phase === IN_PROGRESS_PHASE) {
      await this.#captureLiveEvents();
      await this.#shareLiveSnapshot();
      // A first-blood mode is DECIDED mid-game (§3.6.4): report the moment the
      // deciding event shows up instead of waiting for an eog block that may
      // never exist. #reported is the same single-shot guard the eog path uses,
      // so whichever fires first is the one report this client sends.
      if (this.#firstBloodDecides && !this.#reported && this.#firstBloodSeen()) {
        await this.#reportFirstBlood();
      }
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

  /**
   * Share the live scoreboard so other players can watch the game in their
   * client. Throttled to one read + send per {@link LIVE_SNAPSHOT_EVERY_MS};
   * display-only and soft-fail — it never touches reporting or the tick loop.
   */
  async #shareLiveSnapshot(): Promise<void> {
    const now = this.#clock.nowMs();
    if (this.#lastLiveSnapshotMs !== null && now - this.#lastLiveSnapshotMs < LIVE_SNAPSHOT_EVERY_MS) {
      return;
    }
    this.#lastLiveSnapshotMs = now;
    try {
      const snapshot = await this.#adapter.captureLiveGameSnapshot();
      if (snapshot.players.length > 0) {
        await this.#transport.sendLiveSnapshot({ matchId: this.#matchId, snapshot });
      }
    } catch {
      // :2999 not up yet / already gone — the next window tries again.
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

  /** Whether the freshest live capture already shows the deciding first blood. */
  #firstBloodSeen(): boolean {
    return (
      this.#liveClientEvents?.events.some((e) => e.eventName === "FirstBlood") === true
    );
  }

  /**
   * Send the mid-game first-blood report for a duel (§3.6.4/§3.6.5): the kill
   * stream just captured, keyed by the running game's own id (read by the
   * adapter from the gameflow session). Mirrors `#captureAndReport`: single-shot
   * on success (heartbeat stops — the match is decided), guard cleared on
   * failure so the next tick retries while the game is still running.
   */
  async #reportFirstBlood(): Promise<void> {
    this.#reported = true; // single-shot guard, shared with the eog path.
    try {
      const report = await this.#adapter.captureLiveClientReport(
        undefined,
        this.#liveClientEvents ?? undefined,
      );
      await this.#transport.sendReport({
        matchId: this.#matchId,
        hostEpoch: this.#currentEpoch,
        report,
      });
      this.#cancelHeartbeat?.();
      this.#cancelHeartbeat = null;
    } catch (err) {
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
