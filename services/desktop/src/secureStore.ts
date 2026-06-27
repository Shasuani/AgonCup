/**
 * Encrypted-at-rest session store for the desktop client (PLAN.md §3.3 desktop
 * auth; docs/desktop-auth-plan.md Phase B "Secure storage").
 *
 * The desktop's durable credential is the rotating REFRESH TOKEN. It is persisted
 * to a file under Electron's `app.getPath('userData')`, encrypted with the OS
 * keychain via Electron's `safeStorage` (Keychain on macOS, libsecret/DPAPI
 * elsewhere). It is NEVER written in plaintext: if OS encryption is unavailable
 * we refuse to persist rather than fall back to clear text or `localStorage`.
 *
 * Both the `safeStorage` and filesystem dependencies are injected so the store is
 * unit-testable with an in-memory fake (no Electron, no disk). CONSTRUCTING the
 * store performs NO IO — the file is touched only by {@link load}/{@link save}/
 * {@link clear}.
 */

/** The narrow slice of Electron's `safeStorage` this store depends on. */
export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  encryptString(plainText: string): Buffer;
  decryptString(encrypted: Buffer): string;
}

/** Injected filesystem seam (real impl wraps `node:fs/promises`). */
export interface SecureStoreFs {
  readFile(path: string): Promise<Buffer>;
  writeFile(path: string, data: Buffer): Promise<void>;
  mkdir(path: string, options: { recursive: true }): Promise<unknown>;
  rm(path: string, options: { force: true }): Promise<void>;
}

/**
 * The paired-user summary cached alongside the refresh token. `/auth/refresh`
 * carries NO user payload, so without this a stored-token (silent-refresh) boot
 * would yield a session with `user: null` until the next re-pair — any UI showing
 * the paired user's name would be blank. Caching it here closes that gap. It is
 * advisory display data only (the access JWT remains the authority for identity).
 */
export interface PersistedUser {
  readonly userId: string;
  readonly discordUserId: string;
  readonly displayName: string;
}

/** The persisted session payload — the rotating refresh-token secret plus the
 * advisory paired-user summary (so a silent-refresh boot can restore the name). */
export interface PersistedSession {
  readonly refreshToken: string;
  readonly user?: PersistedUser | null;
}

export interface SecureStoreConfig {
  readonly safeStorage: SafeStorageLike;
  readonly fs: SecureStoreFs;
  /** Absolute path of the encrypted session file (e.g. userData/session.bin). */
  readonly filePath: string;
  /** Directory to ensure exists before writing (defaults to filePath's dir). */
  readonly dirPath: string;
}

/** Defensively shape a parsed `user` blob into a {@link PersistedUser} (or null
 * when absent / malformed) — the file is OS-encrypted but still untrusted JSON. */
function parseUser(raw: unknown): PersistedUser | null {
  if (raw === null || raw === undefined || typeof raw !== "object") {
    return null;
  }
  const rec = raw as Record<string, unknown>;
  if (typeof rec["userId"] !== "string") {
    return null;
  }
  return {
    userId: rec["userId"],
    discordUserId: typeof rec["discordUserId"] === "string" ? rec["discordUserId"] : "",
    displayName: typeof rec["displayName"] === "string" ? rec["displayName"] : "",
  };
}

/**
 * safeStorage-backed encrypted session store. Reads/writes a single small
 * encrypted blob holding the refresh token. Every method is failure-tolerant:
 * {@link load} returns `null` on any error, {@link clear} swallows errors, and
 * {@link save} throws ONLY when OS encryption is unavailable (never silently
 * persisting plaintext).
 */
export class SecureStore {
  readonly #safeStorage: SafeStorageLike;
  readonly #fs: SecureStoreFs;
  readonly #filePath: string;
  readonly #dirPath: string;

  constructor(config: SecureStoreConfig) {
    this.#safeStorage = config.safeStorage;
    this.#fs = config.fs;
    this.#filePath = config.filePath;
    this.#dirPath = config.dirPath;
  }

  /** True when the OS keychain can encrypt — a precondition for {@link save}. */
  isEncryptionAvailable(): boolean {
    try {
      return this.#safeStorage.isEncryptionAvailable();
    } catch {
      return false;
    }
  }

  /**
   * Decrypt + parse the persisted session, or `null` if there is none / it is
   * unreadable (missing file, decrypt failure, malformed JSON). Never throws.
   */
  async load(): Promise<PersistedSession | null> {
    if (!this.isEncryptionAvailable()) {
      return null;
    }
    let encrypted: Buffer;
    try {
      encrypted = await this.#fs.readFile(this.#filePath);
    } catch {
      // Missing file / unreadable => no stored session.
      return null;
    }
    let plain: string;
    try {
      plain = this.#safeStorage.decryptString(encrypted);
    } catch {
      // Tampered / key-rotated / wrong-machine blob => treat as no session.
      return null;
    }
    try {
      const parsed = JSON.parse(plain) as { refreshToken?: unknown; user?: unknown };
      if (typeof parsed.refreshToken === "string" && parsed.refreshToken !== "") {
        return { refreshToken: parsed.refreshToken, user: parseUser(parsed.user) };
      }
      return null;
    } catch {
      return null;
    }
  }

  /**
   * Encrypt + persist the session. Throws if OS encryption is unavailable — we
   * never fall back to plaintext. Creates the parent directory if needed.
   */
  async save(session: PersistedSession): Promise<void> {
    if (!this.isEncryptionAvailable()) {
      throw new Error("OS encryption unavailable; refusing to persist the session in plaintext");
    }
    const encrypted = this.#safeStorage.encryptString(JSON.stringify(session));
    await this.#fs.mkdir(this.#dirPath, { recursive: true });
    await this.#fs.writeFile(this.#filePath, encrypted);
  }

  /** Best-effort delete of the persisted session (sign-out / refresh revoked). */
  async clear(): Promise<void> {
    try {
      await this.#fs.rm(this.#filePath, { force: true });
    } catch {
      // Nothing to clear / already gone — non-fatal.
    }
  }
}
