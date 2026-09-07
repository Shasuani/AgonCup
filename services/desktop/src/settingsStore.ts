/**
 * Plain-JSON device settings store for the desktop client.
 *
 * NON-SECRETS ONLY. Secrets (the rotating refresh token) live in
 * {@link SecureStore} behind OS encryption; this file holds device preferences
 * that must survive a restart — today just the LCU write-automation consent.
 * It deliberately does NOT depend on `safeStorage`: on a machine without OS
 * keychain support the preference still persists, and clearing the session
 * (sign-out) does not erase it.
 *
 * Mirrors SecureStore's construction contract: the filesystem is injected so
 * tests run in memory, and CONSTRUCTING the store performs no IO — the file is
 * touched only by {@link load}/{@link save}. Both are failure-tolerant: a
 * missing/corrupt file loads as defaults, and a failed write only logs (a lost
 * preference must never take the app down).
 */

/** Injected filesystem seam (real impl wraps `node:fs/promises`). */
export interface SettingsStoreFs {
  readFile(path: string): Promise<Buffer>;
  writeFile(path: string, data: Buffer): Promise<void>;
  mkdir(path: string, options: { recursive: true }): Promise<unknown>;
}

/** The persisted device preferences. All fields optional — absent means "never
 * answered", which every consumer must treat as its safe default. */
export interface PersistedSettings {
  /** "May this client create/invite/accept lobbies?" — the automation toggle.
   * Absent/false ⇒ writes stay gated (PLAN.md §3.3 explicit opt-in). */
  readonly automationOptIn?: boolean;
  /** Last summoner icon / level read from the League client, so the profile
   * still renders while League is closed. Display only, never identity. */
  readonly profileIconId?: number;
  readonly summonerLevel?: number;
}

export interface SettingsStoreConfig {
  readonly fs: SettingsStoreFs;
  /** Absolute path of the JSON settings file (e.g. userData/settings.json). */
  readonly filePath: string;
  /** Directory to ensure exists before writing. */
  readonly dirPath: string;
}

/** Defensively shape untrusted JSON into {@link PersistedSettings}. */
function parseSettings(raw: unknown): PersistedSettings {
  if (raw === null || typeof raw !== "object") {
    return {};
  }
  const rec = raw as Record<string, unknown>;
  const out: { automationOptIn?: boolean; profileIconId?: number; summonerLevel?: number } = {};
  if (typeof rec["automationOptIn"] === "boolean") {
    out.automationOptIn = rec["automationOptIn"];
  }
  if (typeof rec["profileIconId"] === "number" && Number.isFinite(rec["profileIconId"])) {
    out.profileIconId = rec["profileIconId"];
  }
  if (typeof rec["summonerLevel"] === "number" && Number.isFinite(rec["summonerLevel"])) {
    out.summonerLevel = rec["summonerLevel"];
  }
  return out;
}

export class SettingsStore {
  readonly #fs: SettingsStoreFs;
  readonly #filePath: string;
  readonly #dirPath: string;

  constructor(config: SettingsStoreConfig) {
    this.#fs = config.fs;
    this.#filePath = config.filePath;
    this.#dirPath = config.dirPath;
  }

  /** Read the settings; a missing or malformed file is simply the defaults. */
  async load(): Promise<PersistedSettings> {
    try {
      const bytes = await this.#fs.readFile(this.#filePath);
      return parseSettings(JSON.parse(bytes.toString("utf8")));
    } catch {
      return {};
    }
  }

  /** Merge `patch` over the stored settings and write back. Best-effort: a
   * failed write resolves `false` rather than throwing. */
  async save(patch: PersistedSettings): Promise<boolean> {
    try {
      const merged = { ...(await this.load()), ...patch };
      await this.#fs.mkdir(this.#dirPath, { recursive: true });
      await this.#fs.writeFile(
        this.#filePath,
        Buffer.from(JSON.stringify(merged, null, 2), "utf8"),
      );
      return true;
    } catch {
      return false;
    }
  }
}
