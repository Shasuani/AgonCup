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
  /** Display name as the LCU reports it (best-effort, EMPTY on modern clients). */
  readonly displayName: string;
  /** Riot ID game name (e.g. "Chama") — reliable; the canonical identity half. */
  readonly gameName: string;
  /** Riot ID tag line (e.g. "Shasu") — reliable; pairs with {@link gameName}. */
  readonly tagLine: string;
  /** `profileIconId` — the summoner icon (Data Dragon `img/profileicon/<id>.png`). */
  readonly profileIconId?: number;
  /** `summonerLevel` — account level, display only. */
  readonly summonerLevel?: number;
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
  /** `CLASSIC` = 5v5 Summoner's Rift; `ARAM` = Howling Abyss (used for 1v1 duels). */
  readonly gameMode: 'CLASSIC' | 'ARAM';
  /** `11` = Summoner's Rift, `12` = Howling Abyss (ARAM). */
  readonly mapId: 11 | 12;
  /** Per-team cap: `5` for 5v5, `1` for a 1v1 duel. */
  readonly teamSize: 1 | 5;
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

/**
 * The lobby the local client is sitting in right now, as a READ sees it.
 *
 * Read, not remembered: the lobby it describes is usually one the *player* made
 * by hand, which no write of ours knows about. That is the whole point — when
 * nobody's client is allowed to create a custom game, somebody creates it
 * themselves and this is how the automation notices it exists and can start
 * inviting (§3.6.1 manual fallback).
 */
export interface CurrentLobby {
  readonly lobbyId: LcuLobbyId;
  /**
   * Best-effort `gameConfig.isCustom`. ADVISORY: an `RE` field name that may
   * drift, so it must never be the sole test for "the lobby is there" — the
   * lobby's existence is carried by {@link lobbyId}.
   */
  readonly isCustom: boolean;
  /**
   * Best-effort `gameConfig.customLobbyName`. ADVISORY (`RE`, may drift), but
   * when readable it is the one field that identifies WHICH custom game this is
   * — the backend owns the name, so a match against the spec means "ours".
   */
  readonly customLobbyName?: string;
}

// ---------------------------------------------------------------------------
// Invite auto-accept filter — §3.6.3
// ---------------------------------------------------------------------------

/**
 * Filter for guest-side auto-accept. Accept ONLY when the inviter matches the
 * designated host for the current epoch; party/queue invites are hard-excluded.
 * Gated additionally on per-session opt-in + presence at call time.
 *
 * The inviter is matched on ANY of the identity fields below. The LCU's
 * received-invitation record identifies the inviter by the LCU-local 36-char
 * `fromPuuid` and the per-region numeric `fromSummonerId` — it never carries the
 * canonical 78-char PUUID, so a fence made only of {@link expectedInviterPuuid}
 * matches nothing and every invite sits un-accepted. The backend therefore also
 * sends the host's advisory LCU identifiers (captured at desktop verification).
 */
export interface AutoAcceptInviteFilter {
  /** Canonical designated-host PUUID (kept as a fence input; see above). */
  readonly expectedInviterPuuid: Puuid;
  /** Host's LCU-local 36-char PUUID — what `fromPuuid` actually carries. */
  readonly expectedInviterLcuPuuid?: Puuid;
  /** Host's per-region numeric summonerId — matches `fromSummonerId`. */
  readonly expectedInviterSummonerId?: SummonerId;
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
  /**
   * `Recipient` — the scorer named by a `FirstBlood` event (that event carries
   * no killer/victim). Load-bearing for first-blood-decided modes downstream
   * (`GameMode.decides_on_first_blood`): adjudication reads it as the duel's
   * winner vote.
   */
  readonly recipient?: string;
  /**
   * `Result` — carried by the `GameEnd` event only: `"Win"` / `"Lose"` from the
   * LOCAL player's perspective (Documented API, same origin). Display-only
   * downstream (the desktop's live winner banner); corroboration still derives
   * truth from cross-report agreement, never from one client's verdict.
   */
  readonly result?: string;
}

/** Captured live-client kill stream for one game (corroboration signal). */
export interface LiveClientEventCapture {
  readonly source: 'liveClientData';
  readonly events: readonly LiveClientKillEvent[];
  /** Stamp of the pinned contract this capture was parsed under. */
  readonly schemaVersion: LcuSchemaVersion;
}

// ---------------------------------------------------------------------------
// Champ-select live status (RE session read) — display-only spectator surface
// ---------------------------------------------------------------------------

/** One player's seat on the champ-select board, as THIS client's LCU sees it. */
export interface ChampSelectMember {
  readonly cellId: number;
  /** Which side of the session the seat sits on, relative to this viewer. */
  readonly side: 'my' | 'their';
  /** Best-effort display name (`gameName` vs `summonerName` — RE drift). */
  readonly gameName?: string;
  /** 36-char LCU uuid — advisory only, never the canonical PUUID (§2). */
  readonly puuid?: string;
  readonly summonerId?: SummonerId;
  /** Current pick: the locked champion, else the hover intent (0 = none yet). */
  readonly championId: number;
  /** True once this cell's `pick` action is completed (champion locked). */
  readonly locked: boolean;
}

/**
 * `RE` — a read-only snapshot of `GET /lol-champ-select/v1/session`. In a
 * custom game every member's LCU exposes BOTH teams, so each client can render
 * the full board locally. Display-only: never an identity or result input.
 */
export interface ChampSelectSnapshot {
  readonly source: 'champSelectSession';
  /** RE timer phase (`PLANNING` / `BAN_PICK` / `FINALIZATION`), verbatim. */
  readonly timerPhase?: string;
  readonly members: readonly ChampSelectMember[];
  /** Stamp of the pinned contract this snapshot was parsed under. */
  readonly schemaVersion: LcuSchemaVersion;
}

/** `RE` — one row of the client's own champion-id -> name catalogue. */
export interface ChampionSummaryEntry {
  readonly id: number;
  readonly name: string;
}

// ---------------------------------------------------------------------------
// End-of-game report (RE eog-stats-block) — §3.6.5
// ---------------------------------------------------------------------------

/** `RE` — per-player line from the eog-stats-block scoreboard. */
export interface EogPlayerStats {
  readonly puuid?: Puuid;
  readonly summonerId?: SummonerId;
  /** `championId` — numeric champion id (0 if absent); icon resolved downstream. */
  readonly championId: number;
  /** `CHAMPIONS_KILLED`. */
  readonly championsKilled: number;
  /** `NUM_DEATHS`. */
  readonly numDeaths: number;
  /** `ASSISTS`. */
  readonly assists: number;
  /** `championName` — display name as the client had it (optional, RE). */
  readonly championName?: string;
  /** `level` — final champion level (optional, RE). */
  readonly level?: number;
  /** `items` — final build, item ids in slot order incl. trinket (0 = empty). */
  readonly items?: readonly number[];
  /** `spell1Id` / `spell2Id` — summoner spell ids (optional, RE). */
  readonly spell1Id?: number;
  readonly spell2Id?: number;
  /** `GOLD_EARNED` (optional, RE). */
  readonly goldEarned?: number;
  /** `MINIONS_KILLED` + `NEUTRAL_MINIONS_KILLED` (optional, RE). */
  readonly minionsKilled?: number;
  /** `TOTAL_DAMAGE_DEALT_TO_CHAMPIONS` (optional, RE). */
  readonly damageToChampions?: number;
  /** `VISION_SCORE` (optional, RE). */
  readonly visionScore?: number;
  /** `profileIconId` (optional, RE) — the player's summoner icon. */
  readonly profileIconId?: number;
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
 *
 * `source` names the capture surface: `'eogStatsBlock'` is the post-game
 * scoreboard read; `'liveClientData'` is the MID-GAME variant for a mode decided
 * before the nexus falls (the 1v1 first-blood duel, §3.6.4) — `teams` is empty
 * there and the verdict rides entirely on `liveClientEvents`.
 */
export interface EogReport {
  readonly source: 'eogStatsBlock' | 'liveClientData';
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
   * Grant or revoke this SESSION's write opt-in (PLAN.md §3.3).
   *
   * The opt-in is a runtime answer, not a build-time setting: the player turns
   * automation on when they are at the keyboard and about to play, and every
   * launch starts from off. Reads are unaffected, and this cannot override the
   * kill-switch or the presence requirement — it only ever supplies the consent
   * half of the gate.
   */
  setWriteAutomationOptIn(enabled: boolean): void;

  /** True when this session has granted the write opt-in (see above). */
  writeAutomationEnabled(): boolean;

  /**
   * `RE` — `GET /lol-lobby/v2/lobby`. READ-ONLY, own-client only, ungated (a
   * read tells the client nothing it cannot already see on screen).
   *
   * Returns `null` when no lobby is visible — including the ordinary "not in a
   * lobby" 404, and any response we cannot read a `partyId` out of. Callers poll
   * this to notice a lobby a HUMAN created, which is the one lobby no write of
   * ours can account for (§3.6.1 manual fallback).
   */
  getCurrentLobby(): Promise<CurrentLobby | null>;

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
   * `RE` — `GET /lol-champ-select/v1/session`. READ-ONLY, ungated. The live
   * champ-select board as THIS client sees it (a custom exposes both teams to
   * every member). `null` when not in champ select — the normal answer most of
   * the time. Display-only status surface; never an identity or result input.
   */
  getChampSelectSession(): Promise<ChampSelectSnapshot | null>;

  /**
   * `RE` — `GET /lol-game-data/assets/v1/champion-summary.json`. READ-ONLY,
   * ungated: the client's own static champion-id -> name catalogue, used only
   * to label champ-select picks in the UI. Advisory display data.
   */
  getChampionSummary(): Promise<readonly ChampionSummaryEntry[]>;

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

  /**
   * `RE` + `Documented` — build a MID-GAME report for a mode decided before the
   * nexus falls (the 1v1 first-blood duel, §3.6.4). Keys the bundled kill stream
   * by the RUNNING game's platform id (`GET /lol-gameflow/v1/session` →
   * `gameData.gameId` — the same id the eog block reports later), so the two
   * duelists' captures corroborate without anyone waiting for an eog block that
   * may never exist (duelists leave after the deciding kill). READ-ONLY. (§3.6.5)
   */
  captureLiveClientReport(
    reporterPuuid?: Puuid,
    liveClientEvents?: LiveClientEventCapture,
  ): Promise<EogReport>;
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
import { WriteGate, WriteRefusedError, type PresenceProbe } from './writeGate.js';

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
  #writeOptIn: boolean;

  constructor(config: LcuAdapterConfig) {
    this.#config = config;
    this.#writeOptIn = config.writeAutomationOptIn;
  }

  /** The opt-in / kill-switch / pacing / schema config this adapter runs under. */
  get config(): LcuAdapterConfig {
    return { ...this.#config, writeAutomationOptIn: this.#writeOptIn };
  }

  setWriteAutomationOptIn(enabled: boolean): void {
    this.#writeOptIn = enabled;
  }

  writeAutomationEnabled(): boolean {
    return this.#writeOptIn;
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
    if (!this.#writeOptIn) {
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

  getCurrentLobby(): Promise<CurrentLobby | null> {
    throw new Error(NOT_IMPLEMENTED);
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

  getChampSelectSession(): Promise<ChampSelectSnapshot | null> {
    throw new Error(NOT_IMPLEMENTED);
  }

  getChampionSummary(): Promise<readonly ChampionSummaryEntry[]> {
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

  captureLiveClientReport(
    _reporterPuuid?: Puuid,
    _liveClientEvents?: LiveClientEventCapture,
  ): Promise<EogReport> {
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
  /** `RE` — queue catalogue; resolves the custom queue id a create must name. */
  queues: '/lol-game-queues/v1/queues',
  /** `RE` — batched invitations endpoint (§3.6.3). */
  invitations: '/lol-lobby/v2/lobby/invitations',
  /** `RE` — invites addressed to THIS client, polled for auto-accept (§3.6.3). */
  receivedInvitations: '/lol-lobby/v2/received-invitations',
  /** `RE` — end-of-game scoreboard block (§3.6.5). */
  eogStatsBlock: '/lol-end-of-game/v1/eog-stats-block',
  /** `RE` — gameflow session; `gameData.gameId` keys the MID-GAME duel report. */
  gameflowSession: '/lol-gameflow/v1/session',
  /** `RE` — live champ-select session (read-only status board). */
  champSelectSession: '/lol-champ-select/v1/session',
  /** `RE` — the client's own champion-id -> name catalogue (display labels). */
  championSummary: '/lol-game-data/assets/v1/champion-summary.json',
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

/**
 * `RE` — fallback custom queue ids per game mode (League client 16.16, EUN1).
 *
 * Patch ~16.x made `POST /lol-lobby/v2/lobby` REQUIRE a top-level `queueId`
 * naming one of the `/lol-game-queues/v1/queues` "Custom" entries; the old
 * `isCustom` + `customGameLobby`-only payload now dies with `INVALID_LOBBY`
 * ("couldn't find controller for queue type: -1" in the client log). Blind-pick
 * customs sort lowest in each family: 3100 = SR Blind Pick Custom, 3200 =
 * Howling Abyss Blind Custom. Used only when the live catalogue read fails.
 */
const CUSTOM_QUEUE_FALLBACK: Record<'CLASSIC' | 'ARAM', number> = {
  CLASSIC: 3100,
  ARAM: 3200,
};

/**
 * Pick the custom queue id for a lobby configuration from the LIVE queue
 * catalogue (`GET /lol-game-queues/v1/queues`).
 *
 * Matches on the structural fields only — `category === 'Custom'`, availability,
 * `gameMode`, `mapId` — never on `description`, which is localized per client
 * locale and therefore useless as an anchor. Of the matching family (blind /
 * draft / all-random / tournament-draft) the LOWEST id is the blind-pick queue
 * on every observed platform, which matches the spec's `mutators.id: 1` (blind)
 * — the only mutator the backend ever sends today. Returns `undefined` when the
 * catalogue holds no match (caller falls back to {@link CUSTOM_QUEUE_FALLBACK}).
 */
export function resolveCustomQueueId(
  queues: unknown,
  config: CustomGameConfiguration,
): number | undefined {
  if (!Array.isArray(queues)) {
    return undefined;
  }
  const candidates: number[] = [];
  for (const raw of queues) {
    const q = asRecord(raw);
    if (
      q['category'] === 'Custom' &&
      q['queueAvailability'] === 'Available' &&
      q['gameMode'] === config.gameMode &&
      q['mapId'] === config.mapId &&
      typeof q['id'] === 'number'
    ) {
      candidates.push(q['id']);
    }
  }
  if (candidates.length === 0) {
    return undefined;
  }
  return Math.min(...candidates);
}

/**
 * How often an armed guest re-reads its own received invitations.
 *
 * Fast enough that accepting looks automatic to the player, slow enough that a
 * client waiting through a long lobby is doing one cheap loopback GET at a
 * human cadence — this is a poll standing in for an event socket, not a busy
 * wait.
 */
const INVITE_POLL_INTERVAL_MS = 2_000;

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
 * Is the client's current lobby THIS match's lobby (vs. some unrelated party)?
 *
 * The backend-owned name is authoritative when readable; otherwise `isCustom`
 * is the best remaining signal. Both are `RE` fields, so the unreadable-name
 * case deliberately errs toward "yes" for a custom lobby: wrongly re-creating
 * kicks every joined player, wrongly reusing is repaired by the invite fence.
 */
function isMatchLobby(lobby: CurrentLobby, expectedName: string): boolean {
  if (lobby.customLobbyName !== undefined) {
    return lobby.customLobbyName === expectedName;
  }
  return lobby.isCustom;
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
  /**
   * The live consent half of the write gate. Seeded from the config and then
   * owned here, because the player answers "may this client act for me?" while
   * the app is running — an adapter rebuilt to carry a new answer would drop
   * every armed match with it.
   */
  #writeOptIn: boolean;
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
    this.#writeOptIn = args.config.writeAutomationOptIn;
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
    return { ...this.#config, writeAutomationOptIn: this.#writeOptIn };
  }

  setWriteAutomationOptIn(enabled: boolean): void {
    this.#writeOptIn = enabled;
  }

  writeAutomationEnabled(): boolean {
    return this.#writeOptIn;
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
      writeAutomationOptIn: this.#writeOptIn,
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
      // Idempotency probe (§3.6.1): if we already own THIS MATCH's lobby, reuse
      // it. "A lobby exists" is not enough — the client can be sitting in a
      // normal-queue party or a friend's custom game, and adopting that would
      // invite nine tournament players into it. The backend owns the lobby name,
      // so a readable name decides; when the name is unreadable (RE drift) we
      // fall back to `isCustom`, biased toward REUSE because the wrong "create"
      // here replaces the player's current lobby and kicks everyone in it.
      const existing = await this.getCurrentLobby();
      if (existing !== null && isMatchLobby(existing, spec.lobbyName)) {
        return {
          lobbyId: existing.lobbyId,
          lobbyName: spec.lobbyName,
          hostEpoch: spec.hostEpoch,
        };
      }

      // `RE` — the current client (16.x) refuses a create without a top-level
      // `queueId` naming a Custom catalogue queue ("couldn't find controller
      // for queue type: -1"). Resolve it from the live catalogue — a READ, so
      // ungated — and fall back to the pinned ids if the read/shape drifts.
      const queueId = await this.#resolveCustomQueueId(spec.configuration);

      // `RE` — create payload per §3.6.2 (verbatim, backend-owned spec).
      const payload = {
        queueId,
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

  /**
   * Resolve the custom queue id for this configuration from the live catalogue,
   * falling back to {@link CUSTOM_QUEUE_FALLBACK} when the read fails or the
   * catalogue holds no structural match (see {@link resolveCustomQueueId}).
   * READ-ONLY — never gated, never throws.
   */
  async #resolveCustomQueueId(config: CustomGameConfiguration): Promise<number> {
    try {
      const res = await this.#lcuRequest('GET', LCU_PATHS.queues);
      if (res.status >= 200 && res.status < 300) {
        const resolved = resolveCustomQueueId(res.json, config);
        if (resolved !== undefined) {
          return resolved;
        }
      }
    } catch {
      // League unreadable mid-create: the POST below will surface the real
      // failure; the pinned id at least gives it a valid payload to fail with.
    }
    return CUSTOM_QUEUE_FALLBACK[config.gameMode];
  }

  // -- getCurrentLobby ------------------------------------------------------

  /**
   * `RE` — `GET /lol-lobby/v2/lobby`. READ-ONLY and ungated.
   *
   * "No lobby" is the normal answer while waiting for a player to make one, so
   * every unreadable outcome collapses to `null` rather than an error: the 404
   * the LCU returns when you are in no lobby, a body with no `partyId`, and any
   * other non-2xx (an LCU hiccup means "we cannot see one", which is the same
   * decision for every caller). Transport failures — League not running,
   * lockfile gone — still throw, because those are not about the lobby.
   */
  async getCurrentLobby(): Promise<CurrentLobby | null> {
    const res = await this.#lcuRequest('GET', LCU_PATHS.lobby);
    if (res.status < 200 || res.status >= 300) {
      return null;
    }
    const rec = asRecord(res.json);
    const lobbyId = asOptString<LcuLobbyId>(rec['partyId']);
    if (lobbyId === undefined) {
      return null;
    }
    const gameConfig = asRecord(rec['gameConfig']);
    const customLobbyName = asOptString(gameConfig['customLobbyName']);
    return {
      lobbyId,
      isCustom: gameConfig['isCustom'] === true,
      ...(customLobbyName === undefined ? {} : { customLobbyName }),
    };
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
   * `RE` — guest-side auto-accept: watch this client's own received invitations
   * and accept the one from the designated inviter.
   *
   * It **polls** rather than subscribing to the LCU event socket. The socket is
   * not part of the injected transport seam, and until this loop existed
   * "arming" a guest did precisely nothing — it built a controller that nothing
   * ever fed, so every invite sat unanswered while the client reported itself
   * armed. A read every couple of seconds is well inside human reaction time for
   * a lobby invite, costs one loopback GET, and reuses the audited request path.
   *
   * Acceptance still passes the whole filter: `invitationType === 'lobby'`, the
   * inviter matching the designated host for the current epoch (see
   * {@link inviterMatchesFilter} for the identifier spaces involved), and the
   * write-gate (opt-in + kill-switch + presence + pacing). The loop stops
   * on the first acceptance — there is nothing left to wait for — or when the
   * returned unsubscribe is called.
   */
  async autoAcceptInvite(filter: AutoAcceptInviteFilter): Promise<() => void> {
    const controller = this.makeInviteAcceptController(filter);
    let stopped = false;
    const poll = async (): Promise<void> => {
      while (!stopped) {
        try {
          for (const invitation of await this.getReceivedInvitations()) {
            const decision = await controller.handle(invitation);
            if (decision.accepted) {
              stopped = true;
              break;
            }
          }
        } catch {
          // League closed, creds rotated, endpoint drifted: keep waiting rather
          // than tearing down the arm — the next tick may well succeed.
        }
        if (stopped) {
          break;
        }
        await this.#rt.sleeper.sleep(INVITE_POLL_INTERVAL_MS);
      }
    };
    void poll();
    return () => {
      stopped = true;
      controller.unsubscribe();
    };
  }

  /**
   * `RE` — `GET /lol-lobby/v2/received-invitations`. READ-ONLY, ungated: it
   * reports invites the player can already see in their own client.
   *
   * Records missing the fields the filter is made of are dropped rather than
   * guessed at — an invitation with no readable inviter identity (neither
   * `fromPuuid` nor `fromSummonerId`) can never satisfy "accept only from the
   * designated host", so passing it on could only ever loosen the fence.
   */
  async getReceivedInvitations(): Promise<readonly ReceivedInvitation[]> {
    const res = await this.#lcuRequest('GET', LCU_PATHS.receivedInvitations);
    if (res.status < 200 || res.status >= 300) {
      return [];
    }
    const out: ReceivedInvitation[] = [];
    for (const raw of asArray(res.json)) {
      const rec = asRecord(raw);
      const invitationId = asOptString(rec['invitationId']);
      const fromPuuid = asOptString<Puuid>(rec['fromPuuid']);
      // `fromSummonerId` arrives as a JSON number; normalize to the string brand.
      const rawSummonerId = rec['fromSummonerId'];
      const fromSummonerId =
        typeof rawSummonerId === 'number' || typeof rawSummonerId === 'string'
          ? (String(rawSummonerId) as SummonerId)
          : undefined;
      const invitationType = asOptString(rec['invitationType']);
      const hasInviterIdentity = fromPuuid !== undefined || fromSummonerId !== undefined;
      if (invitationId === undefined || invitationType === undefined || !hasInviterIdentity) {
        continue;
      }
      const inviteType = asOptString(rec['gameConfigInviteType']);
      out.push({
        invitationId,
        ...(fromPuuid === undefined ? {} : { fromPuuid }),
        ...(fromSummonerId === undefined ? {} : { fromSummonerId }),
        invitationType,
        ...(inviteType === undefined ? {} : { gameConfigInviteType: inviteType }),
      });
    }
    return out;
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
      const res = await this.#lcuRequest(
        'POST',
        `/lol-lobby/v2/received-invitations/${invitationId}/accept`,
      );
      // A failed accept (invite expired, lobby full, LCU drift) must NOT be
      // reported as accepted — that would stop the poll loop and silently
      // disarm the guest for the rest of the match.
      if (res.status < 200 || res.status >= 300) {
        throw new LcuResponseError(`accept failed: HTTP ${res.status}`, res.status);
      }
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
        if (!inviterMatchesFilter(invitation, filter)) {
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
          if (err instanceof WriteRefusedError) {
            return { accepted: false, reason: 'write-gated', error: err };
          }
          // The POST itself failed — keep the arm alive so the next invite (or
          // a retried one) can still be taken.
          return { accepted: false, reason: 'accept-failed', error: err };
        }
        return { accepted: true };
      },
    };
  }

  // -- getCurrentSummoner ---------------------------------------------------

  /**
   * `RE` — `GET /lol-summoner/v1/current-summoner`. READ-ONLY, own-client only.
   * Parses `{ puuid, summonerId, displayName, gameName, tagLine }` into a
   * {@link CurrentSummoner}. `gameName`/`tagLine` are the reliable Riot ID halves
   * (the canonical identity the backend resolves); `displayName` is EMPTY on
   * modern clients (kept for back-compat, never used for identity).
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
    const gameName =
      typeof rec['gameName'] === 'string' ? rec['gameName'] : '';
    const tagLine =
      typeof rec['tagLine'] === 'string' ? rec['tagLine'] : '';
    const profileIconId =
      typeof rec['profileIconId'] === 'number' ? rec['profileIconId'] : undefined;
    const summonerLevel =
      typeof rec['summonerLevel'] === 'number' ? rec['summonerLevel'] : undefined;
    return {
      puuid,
      summonerId,
      displayName,
      gameName,
      tagLine,
      ...(profileIconId !== undefined ? { profileIconId } : {}),
      ...(summonerLevel !== undefined ? { summonerLevel } : {}),
    };
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

  // -- getChampSelectSession -------------------------------------------------

  /**
   * `RE` — `GET /lol-champ-select/v1/session`. READ-ONLY and ungated, like
   * {@link getCurrentLobby}: the 404 outside champ select is the normal answer,
   * so every non-2xx collapses to `null` rather than an error.
   *
   * Lock detection: a seat is `locked` when a completed `pick` action exists
   * for its cell in the RE `actions` grid. `championId` prefers the member
   * row's own `championId` (the current/locked selection) and falls back to
   * `championPickIntent` (the hover). All field names are RE and drift-prone;
   * an unknown shape degrades to fewer/empty seats, never a throw.
   */
  async getChampSelectSession(): Promise<ChampSelectSnapshot | null> {
    const res = await this.#lcuRequest('GET', LCU_PATHS.champSelectSession);
    if (res.status < 200 || res.status >= 300) {
      return null;
    }
    const root = asRecord(res.json);
    const lockedCells = new Set<number>();
    for (const round of asArray(root['actions'])) {
      for (const action of asArray(round)) {
        const act = asRecord(action);
        if (act['type'] === 'pick' && act['completed'] === true) {
          lockedCells.add(asNumber(act['actorCellId'], -1));
        }
      }
    }
    const toMember =
      (side: 'my' | 'their') =>
      (raw: unknown): ChampSelectMember => {
        const rec = asRecord(raw);
        const cellId = asNumber(rec['cellId'], -1);
        // RE name-field drift: `gameName` (riot-id era) vs `summonerName` (older).
        const gameName = asOptString(rec['gameName']) ?? asOptString(rec['summonerName']);
        const puuid = asOptString(rec['puuid']);
        const rawSummonerId = rec['summonerId'];
        const summonerId =
          typeof rawSummonerId === 'number' || typeof rawSummonerId === 'string'
            ? (String(rawSummonerId) as SummonerId)
            : undefined;
        const picked = asNumber(rec['championId']);
        const championId = picked > 0 ? picked : asNumber(rec['championPickIntent']);
        return {
          cellId,
          side,
          ...(gameName === undefined ? {} : { gameName }),
          ...(puuid === undefined ? {} : { puuid }),
          ...(summonerId === undefined ? {} : { summonerId }),
          championId,
          locked: lockedCells.has(cellId),
        };
      };
    const members = [
      ...asArray(root['myTeam']).map(toMember('my')),
      ...asArray(root['theirTeam']).map(toMember('their')),
    ].filter((m) => m.cellId >= 0);
    const timerPhase = asOptString(asRecord(root['timer'])['phase']);
    return {
      source: 'champSelectSession',
      ...(timerPhase === undefined ? {} : { timerPhase }),
      members,
      schemaVersion: this.#config.schemaVersion,
    };
  }

  // -- getChampionSummary ----------------------------------------------------

  /**
   * `RE` — `GET /lol-game-data/assets/v1/champion-summary.json`, the client's
   * own locally-served champion catalogue. READ-ONLY, ungated; display labels
   * only. The catalogue's `id: -1` "None" row is filtered out.
   */
  async getChampionSummary(): Promise<readonly ChampionSummaryEntry[]> {
    const res = await this.#lcuRequest('GET', LCU_PATHS.championSummary);
    if (res.status < 200 || res.status >= 300) {
      throw new LcuResponseError(`getChampionSummary failed: HTTP ${res.status}`, res.status);
    }
    return asArray(res.json)
      .map((raw): ChampionSummaryEntry => {
        const rec = asRecord(raw);
        return {
          id: asNumber(rec['id'], -1),
          name: typeof rec['name'] === 'string' ? rec['name'] : '',
        };
      })
      .filter((c) => c.id > 0 && c.name.length > 0);
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
        // The live client nests the counters under `stats`; older captures (and
        // the fake harness) carry them flat on the player. Read whichever exists.
        const stats = asRecord(pr['stats']);
        const stat = (key: string): number =>
          pr[key] !== undefined ? asNumber(pr[key]) : asNumber(stats[key]);
        const optNum = (v: unknown): number | undefined =>
          typeof v === 'number' && Number.isFinite(v) ? v : undefined;
        const items = Array.isArray(pr['items'])
          ? (pr['items'] as unknown[]).map((i) => asNumber(i))
          : undefined;
        const championName = asOptString<string>(pr['championName']);
        const level = optNum(pr['level']);
        const spell1Id = optNum(pr['spell1Id']);
        const spell2Id = optNum(pr['spell2Id']);
        const profileIconId = optNum(pr['profileIconId']);
        const gold = optNum(pr['GOLD_EARNED'] ?? stats['GOLD_EARNED']);
        const cs =
          optNum(pr['MINIONS_KILLED'] ?? stats['MINIONS_KILLED']) !== undefined
            ? stat('MINIONS_KILLED') + stat('NEUTRAL_MINIONS_KILLED')
            : undefined;
        const dmg = optNum(
          pr['TOTAL_DAMAGE_DEALT_TO_CHAMPIONS'] ?? stats['TOTAL_DAMAGE_DEALT_TO_CHAMPIONS'],
        );
        const vision = optNum(pr['VISION_SCORE'] ?? stats['VISION_SCORE']);
        return {
          ...(puuid !== undefined ? { puuid } : {}),
          ...(summonerId !== undefined ? { summonerId } : {}),
          championId: asNumber(pr['championId']),
          championsKilled: stat('CHAMPIONS_KILLED'),
          numDeaths: stat('NUM_DEATHS'),
          assists: stat('ASSISTS'),
          ...(championName !== undefined ? { championName } : {}),
          ...(level !== undefined ? { level } : {}),
          ...(items !== undefined ? { items } : {}),
          ...(spell1Id !== undefined ? { spell1Id } : {}),
          ...(spell2Id !== undefined ? { spell2Id } : {}),
          ...(gold !== undefined ? { goldEarned: gold } : {}),
          ...(cs !== undefined ? { minionsKilled: cs } : {}),
          ...(dmg !== undefined ? { damageToChampions: dmg } : {}),
          ...(vision !== undefined ? { visionScore: vision } : {}),
          ...(profileIconId !== undefined ? { profileIconId } : {}),
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
        const recipient = asOptString<string>(ev['Recipient']);
        const result = asOptString<string>(ev['Result']);
        return {
          eventId: asNumber(ev['EventID']),
          eventName: typeof ev['EventName'] === 'string' ? ev['EventName'] : '',
          eventTime: asNumber(ev['EventTime']),
          ...(killerName !== undefined ? { killerName } : {}),
          ...(victimName !== undefined ? { victimName } : {}),
          ...(assisters.length > 0 ? { assisters } : {}),
          ...(recipient !== undefined ? { recipient } : {}),
          ...(result !== undefined ? { result } : {}),
        };
      },
    );
    return {
      source: 'liveClientData',
      events,
      schemaVersion: this.#config.schemaVersion,
    };
  }

  // -- captureLiveClientReport ----------------------------------------------

  /**
   * `RE` + `Documented` — the MID-GAME report (see {@link LcuAdapter}). Reads
   * `GET /lol-gameflow/v1/session` for the running game's `gameData.gameId`
   * (READ-ONLY; the id is platform-established at game start, so every
   * participant keys the same value), takes the bundled kill stream — or
   * captures a fresh one when the caller has none — and emits an
   * {@link EogReport} with `source: 'liveClientData'` and an empty `teams`
   * (no scoreboard exists yet; adjudication derives the winner from the kill
   * stream — §3.6.4/§3.6.6).
   */
  async captureLiveClientReport(
    reporterPuuid?: Puuid,
    liveClientEvents?: LiveClientEventCapture,
  ): Promise<EogReport> {
    const res = await this.#lcuRequest('GET', LCU_PATHS.gameflowSession);
    if (res.status < 200 || res.status >= 300) {
      throw new LcuResponseError(
        `captureLiveClientReport failed: HTTP ${res.status}`,
        res.status,
      );
    }
    const gameId = asNumber(asRecord(asRecord(res.json)['gameData'])['gameId']);
    if (gameId <= 0) {
      throw new LcuResponseError(
        'captureLiveClientReport: no gameId in gameflow session',
        res.status,
      );
    }
    const events = liveClientEvents ?? (await this.captureLiveClientEvents());
    return {
      source: 'liveClientData',
      lcuGameId: String(gameId) as LcuGameId,
      reporterPuuid: reporterPuuid ?? ('' as Puuid),
      teams: [],
      liveClientEvents: events,
      schemaVersion: this.#config.schemaVersion,
      capturedAtMs: this.#rt.clock.nowMs(),
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
  /**
   * Inviter PUUID as the LCU reports it — the LCU-local 36-char UUID, NOT the
   * canonical 78-char PUUID. Optional: older schemas expose only
   * `fromSummonerId`; at least one of the two identity fields is present.
   */
  readonly fromPuuid?: Puuid;
  /** Inviter's per-region numeric summonerId (long-standing LCU field). */
  readonly fromSummonerId?: SummonerId;
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
    | 'write-gated'
    | 'accept-failed';
  /** The underlying error when `reason` is `'write-gated'` / `'accept-failed'`. */
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

/**
 * Does this invitation come from the designated host? A match on ANY of the
 * filter's identity fields accepts, because the record and the backend describe
 * the same player in different identifier spaces: `fromPuuid` is the LCU-local
 * 36-char UUID (compared against both the canonical fence for forward-compat and
 * the advisory `expectedInviterLcuPuuid` that actually lives in that space), and
 * `fromSummonerId` is the per-region numeric id. An absent field never matches —
 * the fence can only be satisfied, never defaulted through.
 */
export function inviterMatchesFilter(
  invitation: ReceivedInvitation,
  filter: AutoAcceptInviteFilter,
): boolean {
  if (invitation.fromPuuid !== undefined) {
    if (invitation.fromPuuid === filter.expectedInviterPuuid) {
      return true;
    }
    if (
      filter.expectedInviterLcuPuuid !== undefined &&
      invitation.fromPuuid === filter.expectedInviterLcuPuuid
    ) {
      return true;
    }
  }
  return (
    invitation.fromSummonerId !== undefined &&
    filter.expectedInviterSummonerId !== undefined &&
    invitation.fromSummonerId === filter.expectedInviterSummonerId
  );
}
