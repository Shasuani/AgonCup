/**
 * @lol/lcu — Single audited LCU / Live-Client-Data adapter.
 *
 * ============================================================================
 *  DANGER / READ THIS FIRST  (PLAN.md §3.5 "Single LCU adapter", §3.6)
 * ============================================================================
 *  ALL calls made through this adapter against the League Client Update (LCU)
 *  API and the in-game Live Client Data API (https://127.0.0.1:2999) are
 *  COMMUNITY-REVERSE-ENGINEERED and UNSUPPORTED by Riot unless explicitly
 *  marked `Documented`. These endpoints, paths, payload shapes and WebSocket
 *  event names DRIFT BETWEEN PATCHES (League ships on a ~2-week patch cadence)
 *  and can break silently with no Riot fallback to catch it.
 *
 *  Endpoint provenance markers used in the docstrings below:
 *    - `RE`         : reverse-engineered / unsupported, drift-prone.
 *    - `Documented` : Riot-official (e.g. the Live Client Data API at :2999).
 *
 *  THIS MODULE IS THE SINGLE AUDITED FIX-POINT. No other package in the repo
 *  is permitted to talk to the LCU or :2999 directly — every reverse-engineered
 *  call routes through here so drift has exactly one place to be fixed, pinned
 *  per patch, fixture-validated in CI, and stamped with a `schemaVersion` on
 *  every emitted report (a disagreement downstream = an anomaly).
 *
 *  SAFETY INVARIANTS baked into the contract (do not weaken):
 *    - OWN-CLIENT ONLY. Auth is the per-launch lockfile on THIS machine. Never
 *      proxy or drive another account.
 *    - WRITES ARE SCOPED TO LOBBY/SETUP ONLY (create lobby, invite, auto-accept,
 *      start). NO gameplay/value automation, NO matchmaking. Reads for live
 *      state and end-of-game are best-effort and read-only.
 *    - PER-SESSION DEFAULT-OFF OPT-IN + a global KILL-SWITCH gate every write.
 *      No action while the user is absent (presence gate). Writes are
 *      human-paced / rate-capped / event-driven (see {@link LcuPacingConfig}).
 *    - LCU-WRITE AUTOMATION CARRIES ToS / account-ban EXPOSURE (PLAN.md §11
 *      risk row "LCU write-automation ToS exposure"). The opt-in / kill-switch /
 *      pacing fields below are load-bearing mitigations, not decoration.
 *
 *  Phase-0 STATUS: this is a fully-typed SKELETON. Every method throws
 *  "not implemented". Wiring happens behind a known-patch pin in later phases.
 * ============================================================================
 */

// ---------------------------------------------------------------------------
// Provenance / drift bookkeeping
// ---------------------------------------------------------------------------

/** Where an endpoint's contract comes from — see header. */
export type EndpointProvenance = 'RE' | 'Documented';

/**
 * Opaque schema-version stamp for the pinned LCU/EOG contract this adapter was
 * built and fixture-validated against. Emitted on every report so downstream
 * corroboration can flag cross-client disagreement as an anomaly. (PLAN.md §3.6.5)
 */
export type LcuSchemaVersion = string & { readonly __brand: 'LcuSchemaVersion' };

// ---------------------------------------------------------------------------
// Identity primitives (advisory — never an authoritative identity anchor)
// ---------------------------------------------------------------------------

/** Riot PUUID. Advisory secondary identity signal only (PLAN.md §3 / §2). */
export type Puuid = string & { readonly __brand: 'Puuid' };

/**
 * Per-region numeric `summonerId`. Stored/used for invites because the LCU
 * invitation payload is keyed on it; NEVER matched on for identity. (PLAN.md §2)
 */
export type SummonerId = string & { readonly __brand: 'SummonerId' };

/** LCU-reported lobby handle (`lcu_lobby_id`). */
export type LcuLobbyId = string & { readonly __brand: 'LcuLobbyId' };

/** LCU-reported `gameId` — the corroboration join key (PLAN.md §3.6.5/§3.6.6). */
export type LcuGameId = string & { readonly __brand: 'LcuGameId' };

/** Backend-monotonic host lease, fences create against double-lobby. (§3.6.1) */
export type HostEpoch = number & { readonly __brand: 'HostEpoch' };

// ---------------------------------------------------------------------------
// Gameflow phase (RE: GET /lol-gameflow/v1/gameflow-phase) — §3.6.1
// ---------------------------------------------------------------------------

/**
 * `RE` — values returned by `GET /lol-gameflow/v1/gameflow-phase`. State
 * transitions are driven off THIS, not the ambiguous lobby DELETE event.
 */
export type GameflowPhase =
  | 'None'
  | 'Lobby'
  | 'Matchmaking'
  | 'CheckedIntoTournament'
  | 'ReadyCheck'
  | 'ChampSelect'
  | 'GameStart'
  | 'InProgress'
  | 'Reconnect'
  | 'WaitingForStats'
  | 'PreEndOfGame'
  | 'EndOfGame'
  | 'TerminatedInError';

// ---------------------------------------------------------------------------
// Lockfile auth (own-client only) — §3.5
// ---------------------------------------------------------------------------

/**
 * Credentials parsed from the local League `lockfile`
 * (`name:pid:port:password:protocol`). Rotates on every client launch and on
 * patch; the adapter must detect rotation and re-read. OWN-CLIENT ONLY.
 */
export interface LockfileAuth {
  /** Loopback port the LCU is bound to this launch. */
  readonly port: number;
  /** Basic-auth password (`riot:<password>`), self-signed cert at 127.0.0.1. */
  readonly password: string;
  /** `https` per the lockfile's protocol field. */
  readonly protocol: 'https';
  /** Absolute path the credentials were read from (for rotation detection). */
  readonly lockfilePath: string;
}

// ---------------------------------------------------------------------------
// Config: opt-in / kill-switch / pacing (load-bearing ToS mitigations) — §3.6
// ---------------------------------------------------------------------------

/**
 * Human-pacing / rate-cap for every WRITE call. Writes must look event-driven
 * and human, never AFK-automated bursts. (PLAN.md §3.6.3 / §11 ToS row.)
 */
export interface LcuPacingConfig {
  /** Minimum delay applied before each write (ms). */
  readonly minWriteDelayMs: number;
  /** Random jitter added on top of `minWriteDelayMs` (ms) to avoid lockstep. */
  readonly writeJitterMs: number;
  /** Hard ceiling on writes per rolling minute. */
  readonly maxWritesPerMinute: number;
  /** Bounded backoff cap (ms) for re-invite retries (§3.6.3). */
  readonly maxBackoffMs: number;
  /** Max re-invite / re-create attempts before manual fallback (§3.6.1). */
  readonly maxRetryAttempts: number;
}

/**
 * Top-level adapter config. The opt-in + kill-switch fields gate EVERY write;
 * if `killSwitchEngaged` is true OR `writeAutomationOptIn` is false, the
 * adapter MUST refuse all writes and fall back to the manual lobby UI.
 */
export interface LcuAdapterConfig {
  /**
   * Per-session DEFAULT-OFF opt-in to LCU WRITE automation (create/invite/
   * auto-accept/start). Must be explicitly granted by the user each session.
   */
  readonly writeAutomationOptIn: boolean;

  /**
   * Global kill-switch. When engaged (fleet-wide or local), ALL LCU automation
   * stops and the client degrades to manual lobby name + password. (§3.6.1)
   */
  readonly killSwitchEngaged: boolean;

  /**
   * Presence gate: require the user to be at-keyboard / client-focused before
   * any write fires. No AFK action. (§3.6.3, liveness §2.)
   */
  readonly requirePresenceForWrites: boolean;

  /** Human-pacing / rate-cap for writes. */
  readonly pacing: LcuPacingConfig;

  /** Pinned, fixture-validated schema version stamped on emitted reports. */
  readonly schemaVersion: LcuSchemaVersion;

  /**
   * Optional override for the lockfile path (defaults to the platform's known
   * League install location when omitted).
   */
  readonly lockfilePathOverride?: string;
}

// ---------------------------------------------------------------------------
// Lobby create spec (immutable, backend-owned) — §3.6.2
// ---------------------------------------------------------------------------

/**
 * `RE` — `customGameLobby.configuration` for `POST /lol-lobby/v2/lobby`.
 * Re-created VERBATIM by any successor host (§3.6.1/§3.6.2).
 */
export interface CustomGameConfiguration {
  readonly gameMode: 'CLASSIC';
  readonly mapId: 11;
  readonly teamSize: 5;
  readonly mutators: { readonly id: number };
  readonly spectatorPolicy: 'AllAllowed' | 'NotAllowed';
}

/**
 * The IMMUTABLE lobby spec owned by the backend. A successor host re-creates
 * the lobby byte-for-byte from this, fenced by `(matchId, hostEpoch)`. The
 * 10-invitee list is separate from `teamSize:5` (which only caps the lobby).
 */
export interface CustomLobbySpec {
  /** Backend match id; also embedded in `lobbyName` for audit correlation. */
  readonly matchId: string;
  /** Host lease that fences create against double-lobby across failover. */
  readonly hostEpoch: HostEpoch;
  readonly configuration: CustomGameConfiguration;
  /** Human-stable lobby name (audit clarity, NOT collision prevention). */
  readonly lobbyName: string;
  /** Lobby password surfaced for the manual-join fallback. */
  readonly lobbyPassword: string;
  /** Exact invitee list — summonerIds for the invite payload (§3.6.3). */
  readonly invitees: readonly SummonerId[];
}

/** Handle returned after a successful (re-)create; reported back to backend. */
export interface CustomLobbyHandle {
  readonly lobbyId: LcuLobbyId;
  readonly lobbyName: string;
  readonly hostEpoch: HostEpoch;
}

// ---------------------------------------------------------------------------
// Invite auto-accept filter — §3.6.3
// ---------------------------------------------------------------------------

/**
 * Filter for guest-side auto-accept. Accept ONLY when the inviter PUUID equals
 * the designated host for the current epoch; party/queue invites are hard-
 * excluded. Gated additionally on per-session opt-in + presence at call time.
 */
export interface AutoAcceptInviteFilter {
  /** Only auto-accept invites from this designated-host PUUID. */
  readonly expectedInviterPuuid: Puuid;
  /** Current host epoch; reject invites bound to a stale epoch. */
  readonly hostEpoch: HostEpoch;
  /**
   * Optional custom-lobby invite-type discriminator — an ADDITIONAL guard only.
   * Its live field name is an open research question (§3.6.3): never the sole
   * filter. Left optional/undefined until verified against a pinned patch.
   */
  readonly customInviteTypeDiscriminator?: string;
}

// ---------------------------------------------------------------------------
// Live-client kill stream (Documented API at :2999) — §3.6.4
// ---------------------------------------------------------------------------

/**
 * `Documented` — one entry from the Live Client Data `eventdata` kill stream
 * (`GET https://127.0.0.1:2999/liveclientdata/eventdata`). Read-only,
 * best-effort; the port exists only while `gameflow-phase = InProgress`.
 */
export interface LiveClientKillEvent {
  readonly eventId: number;
  readonly eventName: string;
  readonly eventTime: number;
  readonly killerName?: string;
  readonly victimName?: string;
  readonly assisters?: readonly string[];
}

/** Captured live-client kill stream for one game (corroboration signal). */
export interface LiveClientEventCapture {
  readonly source: 'liveClientData';
  readonly events: readonly LiveClientKillEvent[];
  /** Stamp of the pinned contract this capture was parsed under. */
  readonly schemaVersion: LcuSchemaVersion;
}

// ---------------------------------------------------------------------------
// End-of-game report (RE eog-stats-block) — §3.6.5
// ---------------------------------------------------------------------------

/** `RE` — per-player line from the eog-stats-block scoreboard. */
export interface EogPlayerStats {
  readonly puuid?: Puuid;
  readonly summonerId?: SummonerId;
  /** `CHAMPIONS_KILLED`. */
  readonly championsKilled: number;
  /** `NUM_DEATHS`. */
  readonly numDeaths: number;
  /** `ASSISTS`. */
  readonly assists: number;
}

/** `RE` — one team's block from the eog-stats-block. */
export interface EogTeamStats {
  readonly isWinningTeam: boolean;
  readonly players: readonly EogPlayerStats[];
}

/**
 * The end-of-game report this client captured and is about to POST upstream,
 * keyed by `(lcuGameId, reporterPuuid)`. Fully forgeable / user-attestable —
 * truth is established by cross-client corroboration, never by this alone.
 * (PLAN.md §3.6.5 / §3.6.6.)
 */
export interface EogReport {
  readonly source: 'eogStatsBlock';
  /** Root `gameId` — the corroboration join key. */
  readonly lcuGameId: LcuGameId;
  /** This client's reporter PUUID (advisory). */
  readonly reporterPuuid: Puuid;
  readonly teams: readonly EogTeamStats[];
  /** Best-effort kill stream captured live, bundled for corroboration. */
  readonly liveClientEvents?: LiveClientEventCapture;
  /** Pinned contract stamp; downstream disagreement = anomaly. */
  readonly schemaVersion: LcuSchemaVersion;
  /** Epoch-ms the block was captured (the block clears fast — §3.6.5). */
  readonly capturedAtMs: number;
}

// ---------------------------------------------------------------------------
// The adapter contract
// ---------------------------------------------------------------------------

/**
 * The single audited surface for all LCU / :2999 interaction. Implementations
 * MUST honour the safety invariants in this file's header. Writes
 * (createCustomLobby / invite / autoAcceptInvite) MUST short-circuit when the
 * kill-switch is engaged or the per-session opt-in / presence gate is not met.
 */
export interface LcuAdapter {
  /**
   * Read + parse the local lockfile (own-client only). Detects rotation on
   * relaunch/patch; callers re-invoke to refresh credentials. (§3.5)
   */
  readLockfileAuth(): Promise<LockfileAuth>;

  /**
   * `RE` — `POST /lol-lobby/v2/lobby` (isCustom). WRITE: opt-in + presence +
   * kill-switch + pacing gated. Idempotent per `(matchId, hostEpoch)`: if a
   * lobby for the current epoch already exists, returns that handle instead of
   * POSTing a second one. (§3.6.1 / §3.6.2)
   */
  createCustomLobby(spec: CustomLobbySpec): Promise<CustomLobbyHandle>;

  /**
   * `RE` — one batched `POST /lol-lobby/v2/lobby/invitations`. WRITE: gated +
   * paced; re-invites use bounded backoff+jitter, keyed by
   * `(matchId, toSummonerId, hostEpoch)`. (§3.6.3)
   */
  invite(summonerIds: readonly SummonerId[]): Promise<void>;

  /**
   * `RE` — subscribe to the invitation WS event and auto-accept ONLY invites
   * matching `filter` (inviter == designated host for the current epoch).
   * WRITE: gated on per-session opt-in + presence. Returns an unsubscribe fn.
   * (§3.6.3)
   */
  autoAcceptInvite(filter: AutoAcceptInviteFilter): Promise<() => void>;

  /**
   * `RE` — `GET /lol-gameflow/v1/gameflow-phase`. READ. Drives the host/failover
   * state machine; the authoritative phase signal (DELETE is advisory). (§3.6.1)
   */
  getGameflowPhase(): Promise<GameflowPhase>;

  /**
   * `RE` — on `EndOfGame`, read `GET /lol-end-of-game/v1/eog-stats-block` and
   * build the report. READ-ONLY, background, time-boxed (block clears fast).
   * (§3.6.5)
   */
  captureEndOfGame(): Promise<EogReport>;

  /**
   * `Documented` — read the Live Client Data `eventdata` kill stream from
   * `https://127.0.0.1:2999/liveclientdata/*`. READ-ONLY, best-effort; valid
   * only while `gameflow-phase = InProgress`. (§3.6.4)
   */
  captureLiveClientEvents(): Promise<LiveClientEventCapture>;
}

// ---------------------------------------------------------------------------
// Phase-0 skeleton implementation (every method throws "not implemented")
// ---------------------------------------------------------------------------

const NOT_IMPLEMENTED = 'not implemented' as const;

/**
 * Phase-0 skeleton. Fully typed against {@link LcuAdapter}; every method throws
 * "not implemented". Real wiring lands behind a pinned-patch contract + CI
 * fixture validation in a later phase. Construct with an {@link LcuAdapterConfig}
 * so the opt-in / kill-switch / pacing contract is present from day one.
 */
export class LcuAdapterSkeleton implements LcuAdapter {
  readonly #config: LcuAdapterConfig;

  constructor(config: LcuAdapterConfig) {
    this.#config = config;
  }

  /** The opt-in / kill-switch / pacing / schema config this adapter runs under. */
  get config(): LcuAdapterConfig {
    return this.#config;
  }

  /**
   * Guard every WRITE path: refuse when the kill-switch is engaged or the
   * per-session opt-in is not granted. Wired up by the real implementation;
   * present here so the contract is visible in the skeleton.
   */
  #assertWritesAllowed(): never {
    if (this.#config.killSwitchEngaged) {
      throw new Error('LCU writes refused: kill-switch engaged');
    }
    if (!this.#config.writeAutomationOptIn) {
      throw new Error('LCU writes refused: per-session opt-in not granted');
    }
    throw new Error(NOT_IMPLEMENTED);
  }

  readLockfileAuth(): Promise<LockfileAuth> {
    throw new Error(NOT_IMPLEMENTED);
  }

  createCustomLobby(_spec: CustomLobbySpec): Promise<CustomLobbyHandle> {
    return this.#assertWritesAllowed();
  }

  invite(_summonerIds: readonly SummonerId[]): Promise<void> {
    return this.#assertWritesAllowed();
  }

  autoAcceptInvite(_filter: AutoAcceptInviteFilter): Promise<() => void> {
    return this.#assertWritesAllowed();
  }

  getGameflowPhase(): Promise<GameflowPhase> {
    throw new Error(NOT_IMPLEMENTED);
  }

  captureEndOfGame(): Promise<EogReport> {
    throw new Error(NOT_IMPLEMENTED);
  }

  captureLiveClientEvents(): Promise<LiveClientEventCapture> {
    throw new Error(NOT_IMPLEMENTED);
  }
}
