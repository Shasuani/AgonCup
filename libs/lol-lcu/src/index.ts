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

/**
 * `RE` — the local logged-in account as reported by
 * `GET /lol-summoner/v1/current-summoner`. READ-ONLY, own-client only.
 *
 * Used ONLY as an ADVISORY identity signal: the desktop client compares this
 * `puuid` against the backend's verified-account PUUID for the same user to
 * surface a soft "you appear to be signed into a different Riot account"
 * warning. It is NEVER the authoritative identity anchor and never gates
 * auth / blocks orchestration (PLAN.md §3.6 / §2 — "LCU `current-summoner`
 * PUUID is an *advisory* secondary signal only").
 */
export interface CurrentSummoner {
  /** Local account PUUID — advisory only (the canonical key is backend-side). */
  readonly puuid: Puuid;
  /** Per-region numeric summoner id (used for invites, never for identity). */
  readonly summonerId: SummonerId;
  /** Display name as the LCU reports it (best-effort, may be empty on drift). */
  readonly displayName: string;
}

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
   * `RE` — `GET /lol-summoner/v1/current-summoner`. READ-ONLY, own-client only.
   * Returns the locally signed-in account. ADVISORY identity signal only — the
   * desktop client compares this PUUID to the backend's verified PUUID to warn
   * on a likely wrong-account sign-in; it NEVER gates auth or anti-smurf
   * (PLAN.md §3.6 / §2). Documented-vs-RE: this LCU route is community
   * reverse-engineered (`RE`), not a Riot-published contract.
   */
  getCurrentSummoner(): Promise<CurrentSummoner>;

  /**
   * `RE` — `GET /lol-gameflow/v1/gameflow-phase`. READ. Drives the host/failover
   * state machine; the authoritative phase signal (DELETE is advisory). (§3.6.1)
   */
  getGameflowPhase(): Promise<GameflowPhase>;

  /**
   * `RE` — on `EndOfGame`, read `GET /lol-end-of-game/v1/eog-stats-block` and
   * build the report. READ-ONLY, background, time-boxed (block clears fast).
   * Optionally bundles a pre-captured Live-Client kill stream (§3.6.4) and the
   * advisory reporter PUUID into the emitted {@link EogReport}. (§3.6.5)
   */
  captureEndOfGame(
    reporterPuuid?: Puuid,
    liveClientEvents?: LiveClientEventCapture,
  ): Promise<EogReport>;

  /**
   * `Documented` — read the Live Client Data `eventdata` kill stream from
   * `https://127.0.0.1:2999/liveclientdata/*`. READ-ONLY, best-effort; valid
   * only while `gameflow-phase = InProgress`. (§3.6.4)
   */
  captureLiveClientEvents(): Promise<LiveClientEventCapture>;
}

// ---------------------------------------------------------------------------
// Injected runtime + helper re-exports
// ---------------------------------------------------------------------------

export type {
  HttpMethod,
  HttpRequest,
  HttpResponse,
  HttpTransport,
  FileSystemReader,
  LockfileSnapshot,
  Clock,
  Sleeper,
  RandomSource,
  LcuRuntime,
} from './transport.js';
export type { ParsedLockfile, LcuAuth } from './lockfile.js';
export { parseLockfile, deriveAuth, LockfileParseError } from './lockfile.js';
export type {
  WriteRefusalReason,
  WriteGatePacing,
  WriteGateState,
  PresenceProbe,
} from './writeGate.js';
export { WriteGate, WriteRefusedError, backoffDelayMs } from './writeGate.js';

import type { LcuRuntime } from './transport.js';
import { deriveAuth, parseLockfile } from './lockfile.js';
import { WriteGate, type PresenceProbe } from './writeGate.js';

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

  getCurrentSummoner(): Promise<CurrentSummoner> {
    throw new Error(NOT_IMPLEMENTED);
  }

  getGameflowPhase(): Promise<GameflowPhase> {
    throw new Error(NOT_IMPLEMENTED);
  }

  captureEndOfGame(
    _reporterPuuid?: Puuid,
    _liveClientEvents?: LiveClientEventCapture,
  ): Promise<EogReport> {
    throw new Error(NOT_IMPLEMENTED);
  }

  captureLiveClientEvents(): Promise<LiveClientEventCapture> {
    throw new Error(NOT_IMPLEMENTED);
  }
}

// ---------------------------------------------------------------------------
// Runnable implementation (injected transport + write-gate)
// ---------------------------------------------------------------------------

/** Default Live Client Data origin — the documented in-game port (§3.6.4). */
const LIVE_CLIENT_ORIGIN = 'https://127.0.0.1:2999' as const;

/** `Documented` Live Client Data path for the kill stream (§3.6.4). */
const LIVE_CLIENT_EVENTDATA_PATH = '/liveclientdata/eventdata' as const;

/** `RE` LCU paths, pinned here as the single fix-point (§3.6). */
const LCU_PATHS = {
  /** `RE` — gameflow phase, the authoritative state signal (§3.6.1). */
  gameflowPhase: '/lol-gameflow/v1/gameflow-phase',
  /** `RE` — locally signed-in account (advisory identity probe, §3.6/§2). */
  currentSummoner: '/lol-summoner/v1/current-summoner',
  /** `RE` — current custom-lobby handle (idempotency probe before create). */
  lobby: '/lol-lobby/v2/lobby',
  /** `RE` — batched invitations endpoint (§3.6.3). */
  invitations: '/lol-lobby/v2/lobby/invitations',
  /** `RE` — end-of-game scoreboard block (§3.6.5). */
  eogStatsBlock: '/lol-end-of-game/v1/eog-stats-block',
} as const;

/** Thrown when an LCU/EOG/live-client response is shaped unexpectedly (drift). */
export class LcuResponseError extends Error {
  override readonly name = 'LcuResponseError';
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** A presence probe that always reports present (real wiring injects a real one). */
const ALWAYS_PRESENT: PresenceProbe = { isPresent: () => true };

/** Narrowing helper for `unknown` JSON objects under `noUncheckedIndexedAccess`. */
function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null) {
    return {};
  }
  return value as Record<string, unknown>;
}

function asArray(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function asOptString<T extends string>(value: unknown): T | undefined {
  return typeof value === 'string' ? (value as T) : undefined;
}

/**
 * The single audited, RUNNABLE LCU/Live-Client adapter (PLAN.md §3.5 / §3.6).
 *
 * All IO is performed through the injected {@link LcuRuntime} so this class is
 * transport-agnostic and unit-testable with fixtures; the real wiring supplies
 * a Node HTTPS transport that accepts the LCU's self-signed loopback cert (see
 * `transport.ts` header). Every WRITE (`createCustomLobby`/`invite`/auto-accept
 * acceptance) is gated through {@link WriteGate} — kill-switch + per-session
 * opt-in + presence + rolling rate-cap + human pacing — before any byte leaves.
 *
 * Provenance: `getGameflowPhase`/`createCustomLobby`/`invite`/`autoAcceptInvite`/
 * `captureEndOfGame` hit `RE` LCU endpoints; `captureLiveClientEvents` hits the
 * `Documented` Live Client Data API. RE paths are pinned in {@link LCU_PATHS}
 * and re-validated against fixtures in CI; drift surfaces as
 * {@link LcuResponseError}.
 */
export class LcuAdapterImpl implements LcuAdapter {
  readonly #config: LcuAdapterConfig;
  readonly #rt: LcuRuntime;
  readonly #gate: WriteGate;
  readonly #presence: PresenceProbe;
  /** Cached auth from the most recent lockfile read; refreshed on rotation. */
  #auth: import('./lockfile.js').LcuAuth | undefined;
  /** mtime of the lockfile backing {@link #auth}; a change ⇒ creds rotated. */
  #lockfileMtimeMs = -1;

  constructor(args: {
    config: LcuAdapterConfig;
    runtime: LcuRuntime;
    /** Optional real presence probe; defaults to always-present. */
    presence?: PresenceProbe;
  }) {
    this.#config = args.config;
    this.#rt = args.runtime;
    this.#presence = args.presence ?? ALWAYS_PRESENT;
    this.#gate = new WriteGate({
      clock: args.runtime.clock,
      sleeper: args.runtime.sleeper,
      random: args.runtime.random,
      pacing: {
        minWriteDelayMs: args.config.pacing.minWriteDelayMs,
        writeJitterMs: args.config.pacing.writeJitterMs,
        maxWritesPerMinute: args.config.pacing.maxWritesPerMinute,
      },
      presence: this.#presence,
    });
  }

  /** The opt-in / kill-switch / pacing / schema config this adapter runs under. */
  get config(): LcuAdapterConfig {
    return this.#config;
  }

  /** Resolve the lockfile path: explicit override or platform default. */
  #lockfilePath(): string {
    if (this.#config.lockfilePathOverride !== undefined) {
      return this.#config.lockfilePathOverride;
    }
    // Platform defaults (the real wiring resolves the live install dir). These
    // are the well-known install locations; override when League is elsewhere.
    const platform = (globalThis as { process?: { platform?: string } }).process?.platform;
    if (platform === 'win32') {
      return 'C:/Riot Games/League of Legends/lockfile';
    }
    if (platform === 'darwin') {
      return '/Applications/League of Legends.app/Contents/LoL/lockfile';
    }
    return '/lockfile';
  }

  /** Gate + perform a write IO; rejects with `WriteRefusedError` when blocked. */
  async #gatedWrite<T>(fn: () => Promise<T>): Promise<T> {
    await this.#gate.acquire({
      killSwitchEngaged: this.#config.killSwitchEngaged,
      writeAutomationOptIn: this.#config.writeAutomationOptIn,
      requirePresenceForWrites: this.#config.requirePresenceForWrites,
    });
    return fn();
  }

  /** Ensure fresh LCU auth, re-reading the lockfile when it has rotated. */
  async #ensureAuth(): Promise<import('./lockfile.js').LcuAuth> {
    // readLockfileAuth refreshes #auth (and detects rotation) as a side-effect.
    await this.readLockfileAuth();
    return this.#auth!;
  }

  /** Issue an authenticated LCU request against the loopback base URL. */
  async #lcuRequest(
    method: 'GET' | 'POST' | 'DELETE',
    path: string,
    jsonBody?: unknown,
  ): Promise<{ status: number; json: unknown }> {
    const auth = await this.#ensureAuth();
    const headers: Record<string, string> = {
      Authorization: auth.authorizationHeader,
      Accept: 'application/json',
    };
    const body = jsonBody === undefined ? undefined : JSON.stringify(jsonBody);
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    const res = await this.#rt.http.request({
      method,
      url: `${auth.baseUrl}${path}`,
      headers,
      ...(body === undefined ? {} : { body }),
    });
    let json: unknown = undefined;
    if (res.body.length > 0) {
      try {
        json = JSON.parse(res.body);
      } catch {
        json = undefined;
      }
    }
    return { status: res.status, json };
  }

  // -- readLockfileAuth -----------------------------------------------------

  /**
   * `RE` (lockfile is undocumented) — read + parse the local lockfile and
   * derive `Basic` auth + base URL. Detects rotation via mtime and re-parses;
   * returns a {@link LockfileAuth} view. OWN-CLIENT ONLY (§3.5).
   */
  async readLockfileAuth(): Promise<LockfileAuth> {
    const path = this.#lockfilePath();
    const snapshot = await this.#rt.fs.readLockfile(path);
    if (this.#auth === undefined || snapshot.mtimeMs !== this.#lockfileMtimeMs) {
      const parsed = parseLockfile(snapshot.contents);
      this.#auth = deriveAuth(parsed);
      this.#lockfileMtimeMs = snapshot.mtimeMs;
    }
    const auth = this.#auth;
    return {
      port: auth.port,
      password: auth.password,
      protocol: 'https',
      lockfilePath: path,
    };
  }

  // -- createCustomLobby ----------------------------------------------------

  /**
   * `RE` — `POST /lol-lobby/v2/lobby` (isCustom). WRITE: gated + paced.
   * Idempotent per `(matchId, hostEpoch)`: first `GET`s the current lobby and,
   * if one already exists for this epoch, returns that handle instead of
   * POSTing a second one (§3.6.1 fencing / §3.6.2).
   */
  async createCustomLobby(spec: CustomLobbySpec): Promise<CustomLobbyHandle> {
    return this.#gatedWrite(async () => {
      // Idempotency probe (§3.6.1): if we already own a lobby, reuse it.
      const existing = await this.#lcuRequest('GET', LCU_PATHS.lobby);
      if (existing.status >= 200 && existing.status < 300) {
        const rec = asRecord(existing.json);
        const existingId = asOptString<LcuLobbyId>(rec['partyId']);
        if (existingId !== undefined) {
          return {
            lobbyId: existingId,
            lobbyName: spec.lobbyName,
            hostEpoch: spec.hostEpoch,
          };
        }
      }

      // `RE` — create payload per §3.6.2 (verbatim, backend-owned spec).
      const payload = {
        isCustom: true,
        customGameLobby: {
          configuration: {
            gameMode: spec.configuration.gameMode,
            mapId: spec.configuration.mapId,
            mutators: { id: spec.configuration.mutators.id },
            spectatorPolicy: spec.configuration.spectatorPolicy,
            teamSize: spec.configuration.teamSize,
          },
          lobbyName: spec.lobbyName,
          lobbyPassword: spec.lobbyPassword,
        },
      };
      const res = await this.#lcuRequest('POST', LCU_PATHS.lobby, payload);
      if (res.status < 200 || res.status >= 300) {
        throw new LcuResponseError(
          `createCustomLobby failed: HTTP ${res.status}`,
          res.status,
        );
      }
      const rec = asRecord(res.json);
      const lobbyId = asOptString<LcuLobbyId>(rec['partyId']);
      if (lobbyId === undefined) {
        throw new LcuResponseError(
          'createCustomLobby: response missing partyId (RE drift?)',
          res.status,
        );
      }
      return { lobbyId, lobbyName: spec.lobbyName, hostEpoch: spec.hostEpoch };
    });
  }

  // -- invite ---------------------------------------------------------------

  /**
   * `RE` — one batched `POST /lol-lobby/v2/lobby/invitations` with the full
   * `toSummonerId` array. WRITE: gated + paced. Bounded-backoff re-invites of
   * non-joined members are driven by the caller (host state machine, §3.6.3)
   * using {@link backoffDelayMs}; this method performs the single batched send.
   */
  async invite(summonerIds: readonly SummonerId[]): Promise<void> {
    if (summonerIds.length === 0) {
      return;
    }
    await this.#gatedWrite(async () => {
      // `RE` — payload shape: array of `{ toSummonerId }` invitation records.
      const payload = summonerIds.map((id) => ({ toSummonerId: Number(id) }));
      const res = await this.#lcuRequest('POST', LCU_PATHS.invitations, payload);
      if (res.status < 200 || res.status >= 300) {
        throw new LcuResponseError(`invite failed: HTTP ${res.status}`, res.status);
      }
    });
  }

  // -- autoAcceptInvite -----------------------------------------------------

  /**
   * `RE` — guest-side auto-accept. In production this subscribes to the LCU
   * invitation WebSocket event; that socket is not part of the injected
   * transport seam yet, so this returns a closure-based controller you drive
   * from the WS layer: call {@link InviteAcceptController.handle} with each
   * observed invitation. Acceptance ONLY fires when the inviter PUUID equals
   * `filter.expectedInviterPuuid` for the current epoch, party/queue invites
   * are excluded, AND the write-gate (opt-in + presence) passes (§3.6.3).
   *
   * Returns an unsubscribe function per the {@link LcuAdapter} contract; the
   * controller it tears down is exposed via {@link makeInviteAcceptController}
   * for tests / the WS wiring.
   */
  async autoAcceptInvite(filter: AutoAcceptInviteFilter): Promise<() => void> {
    const controller = this.makeInviteAcceptController(filter);
    return () => controller.unsubscribe();
  }

  /**
   * Build the pure auto-accept controller used by {@link autoAcceptInvite}.
   * Exposed so the (future) WS layer and unit tests can feed it raw invitation
   * records without a live socket. RE: invitation record field names are
   * pinned here and re-validated against fixtures.
   */
  makeInviteAcceptController(filter: AutoAcceptInviteFilter): InviteAcceptController {
    let active = true;
    const accept = async (invitationId: string): Promise<void> => {
      // `RE` — `POST /lol-lobby/v2/received-invitations/{id}/accept`.
      await this.#lcuRequest(
        'POST',
        `/lol-lobby/v2/received-invitations/${invitationId}/accept`,
      );
    };
    return {
      unsubscribe: () => {
        active = false;
      },
      handle: async (invitation: ReceivedInvitation): Promise<InviteDecision> => {
        if (!active) {
          return { accepted: false, reason: 'unsubscribed' };
        }
        if (invitation.invitationType !== 'lobby') {
          return { accepted: false, reason: 'not-a-lobby-invite' };
        }
        if (invitation.fromPuuid !== filter.expectedInviterPuuid) {
          return { accepted: false, reason: 'inviter-mismatch' };
        }
        if (
          filter.customInviteTypeDiscriminator !== undefined &&
          invitation.gameConfigInviteType !== filter.customInviteTypeDiscriminator
        ) {
          return { accepted: false, reason: 'invite-type-mismatch' };
        }
        // Acceptance is a WRITE — gate it (opt-in + presence + pacing).
        try {
          await this.#gatedWrite(() => accept(invitation.invitationId));
        } catch (err) {
          return { accepted: false, reason: 'write-gated', error: err };
        }
        return { accepted: true };
      },
    };
  }

  // -- getCurrentSummoner ---------------------------------------------------

  /**
   * `RE` — `GET /lol-summoner/v1/current-summoner`. READ-ONLY, own-client only.
   * Parses `{ puuid, summonerId, displayName }` into a {@link CurrentSummoner}.
   * ADVISORY identity signal only (PLAN.md §3.6 / §2): the caller compares the
   * PUUID to the backend's verified PUUID and warns on mismatch — it NEVER
   * gates auth, anti-smurf, or orchestration. Drift surfaces as
   * {@link LcuResponseError}.
   */
  async getCurrentSummoner(): Promise<CurrentSummoner> {
    const res = await this.#lcuRequest('GET', LCU_PATHS.currentSummoner);
    if (res.status < 200 || res.status >= 300) {
      throw new LcuResponseError(
        `getCurrentSummoner failed: HTTP ${res.status}`,
        res.status,
      );
    }
    const rec = asRecord(res.json);
    const puuid = asOptString<Puuid>(rec['puuid']);
    if (puuid === undefined) {
      throw new LcuResponseError(
        'getCurrentSummoner: response missing puuid (RE drift?)',
        res.status,
      );
    }
    const summonerId =
      rec['summonerId'] !== undefined
        ? (String(asNumber(rec['summonerId'])) as SummonerId)
        : ('' as SummonerId);
    const displayName =
      typeof rec['displayName'] === 'string' ? rec['displayName'] : '';
    return { puuid, summonerId, displayName };
  }

  // -- getGameflowPhase -----------------------------------------------------

  /**
   * `RE` — `GET /lol-gameflow/v1/gameflow-phase`. READ. The authoritative
   * phase signal that drives the host/failover state machine (§3.6.1). The
   * endpoint returns a bare JSON string, e.g. `"InProgress"`.
   */
  async getGameflowPhase(): Promise<GameflowPhase> {
    const res = await this.#lcuRequest('GET', LCU_PATHS.gameflowPhase);
    if (res.status < 200 || res.status >= 300) {
      throw new LcuResponseError(
        `getGameflowPhase failed: HTTP ${res.status}`,
        res.status,
      );
    }
    if (typeof res.json !== 'string') {
      throw new LcuResponseError(
        'getGameflowPhase: expected a bare string phase (RE drift?)',
        res.status,
      );
    }
    return res.json as GameflowPhase;
  }

  // -- captureEndOfGame -----------------------------------------------------

  /**
   * `RE` — `GET /lol-end-of-game/v1/eog-stats-block`. READ-ONLY, time-boxed
   * (the block clears on the next champ-select/game — §3.6.5). Parses the
   * scoreboard into an {@link EogReport} stamped with the pinned schema version
   * and optionally bundles a best-effort live-client kill stream.
   *
   * @param reporterPuuid this client's advisory reporter PUUID.
   * @param liveClientEvents optional pre-captured kill stream to bundle.
   */
  async captureEndOfGame(
    reporterPuuid?: Puuid,
    liveClientEvents?: LiveClientEventCapture,
  ): Promise<EogReport> {
    const res = await this.#lcuRequest('GET', LCU_PATHS.eogStatsBlock);
    if (res.status < 200 || res.status >= 300) {
      throw new LcuResponseError(
        `captureEndOfGame failed: HTTP ${res.status}`,
        res.status,
      );
    }
    const root = asRecord(res.json);
    const lcuGameId = String(asNumber(root['gameId'])) as LcuGameId;

    const teams: EogTeamStats[] = asArray(root['teams']).map((t) => {
      const team = asRecord(t);
      const players: EogPlayerStats[] = asArray(team['players']).map((p) => {
        const pr = asRecord(p);
        const puuid = asOptString<Puuid>(pr['puuid']);
        const summonerId =
          pr['summonerId'] !== undefined
            ? (String(asNumber(pr['summonerId'])) as SummonerId)
            : undefined;
        return {
          ...(puuid !== undefined ? { puuid } : {}),
          ...(summonerId !== undefined ? { summonerId } : {}),
          championsKilled: asNumber(pr['CHAMPIONS_KILLED']),
          numDeaths: asNumber(pr['NUM_DEATHS']),
          assists: asNumber(pr['ASSISTS']),
        };
      });
      return {
        isWinningTeam: team['isWinningTeam'] === true,
        players,
      };
    });

    return {
      source: 'eogStatsBlock',
      lcuGameId,
      reporterPuuid: reporterPuuid ?? ('' as Puuid),
      teams,
      ...(liveClientEvents !== undefined ? { liveClientEvents } : {}),
      schemaVersion: this.#config.schemaVersion,
      capturedAtMs: this.#rt.clock.nowMs(),
    };
  }

  // -- captureLiveClientEvents ----------------------------------------------

  /**
   * `Documented` — `GET https://127.0.0.1:2999/liveclientdata/eventdata`.
   * READ-ONLY, best-effort; the port exists only while
   * `gameflow-phase = InProgress` and dies on game exit (§3.6.4). Parses the
   * `Events` array into {@link LiveClientKillEvent}s. A connection failure / 404
   * (game already exited) surfaces as {@link LcuResponseError} for the caller to
   * treat as "no live capture this game".
   */
  async captureLiveClientEvents(): Promise<LiveClientEventCapture> {
    // Live Client Data needs no auth, but is also self-signed at 127.0.0.1 —
    // the injected transport handles that cert (see transport.ts header).
    const res = await this.#rt.http.request({
      method: 'GET',
      url: `${LIVE_CLIENT_ORIGIN}${LIVE_CLIENT_EVENTDATA_PATH}`,
      headers: { Accept: 'application/json' },
    });
    if (res.status < 200 || res.status >= 300) {
      throw new LcuResponseError(
        `captureLiveClientEvents failed: HTTP ${res.status}`,
        res.status,
      );
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(res.body);
    } catch {
      throw new LcuResponseError(
        'captureLiveClientEvents: non-JSON response',
        res.status,
      );
    }
    // The documented shape is `{ "Events": [ ... ] }`.
    const events: LiveClientKillEvent[] = asArray(asRecord(parsed)['Events']).map(
      (e) => {
        const ev = asRecord(e);
        const assisters = asArray(ev['Assisters'])
          .filter((a): a is string => typeof a === 'string');
        const killerName = asOptString<string>(ev['KillerName']);
        const victimName = asOptString<string>(ev['VictimName']);
        return {
          eventId: asNumber(ev['EventID']),
          eventName: typeof ev['EventName'] === 'string' ? ev['EventName'] : '',
          eventTime: asNumber(ev['EventTime']),
          ...(killerName !== undefined ? { killerName } : {}),
          ...(victimName !== undefined ? { victimName } : {}),
          ...(assisters.length > 0 ? { assisters } : {}),
        };
      },
    );
    return {
      source: 'liveClientData',
      events,
      schemaVersion: this.#config.schemaVersion,
    };
  }
}

// ---------------------------------------------------------------------------
// Auto-accept controller types (§3.6.3)
// ---------------------------------------------------------------------------

/**
 * `RE` — a single received-invitation record as observed on the LCU invitation
 * WebSocket. Field names are pinned here (the single fix-point) and re-validated
 * against fixtures; `gameConfigInviteType` is the OPTIONAL custom-lobby
 * discriminator whose live name is an open research question (§3.6.3) — never
 * the sole filter.
 */
export interface ReceivedInvitation {
  readonly invitationId: string;
  /** Inviter PUUID — the load-bearing auto-accept filter key (§3.6.3). */
  readonly fromPuuid: Puuid;
  /** `'lobby'` for custom-lobby invites; party/queue types are excluded. */
  readonly invitationType: string;
  /** Optional custom-lobby invite-type discriminator (additional guard only). */
  readonly gameConfigInviteType?: string;
}

/** Outcome of evaluating one invitation against the auto-accept filter. */
export interface InviteDecision {
  readonly accepted: boolean;
  /** Why it was not accepted (present iff `accepted` is false). */
  readonly reason?:
    | 'unsubscribed'
    | 'not-a-lobby-invite'
    | 'inviter-mismatch'
    | 'invite-type-mismatch'
    | 'write-gated';
  /** The underlying error when `reason === 'write-gated'`. */
  readonly error?: unknown;
}

/**
 * Pure controller driving guest-side auto-accept. The WS layer feeds each
 * observed invitation to {@link handle}; {@link unsubscribe} stops accepting.
 */
export interface InviteAcceptController {
  handle(invitation: ReceivedInvitation): Promise<InviteDecision>;
  unsubscribe(): void;
}
