/**
 * Real {@link LcuRuntime} for the desktop main process — the live IO wiring the
 * audited `@lol/lcu` adapter needs to talk to the local League client.
 *
 * It supplies:
 *   - an {@link HttpTransport} backed by Node's `https` module, using a dedicated
 *     `https.Agent` scoped to the loopback LCU origin. The LCU serves HTTPS on
 *     127.0.0.1 with a per-install self-signed cert, so the agent disables cert
 *     verification (`rejectUnauthorized:false`) — but ONLY on THIS agent
 *     instance, never process-global (see transport.ts header). All LCU/:2999
 *     traffic is loopback-only, so this is scoped to localhost.
 *   - a {@link FileSystemReader} reading the lockfile bytes + mtime via
 *     `node:fs/promises`.
 *   - {@link Clock} / {@link Sleeper} / {@link RandomSource} from the system.
 *
 * USAGE: the desktop verify flow (`getCurrentSummoner` + `readLockfileAuth`,
 * read-only) and the orchestration adapter behind it. Lobby writes DO travel
 * this transport — every one of them through the audited adapter's write gate
 * (per-session opt-in, kill-switch, presence, pacing), never from here.
 * IMPORT-SAFETY: constructing the runtime opens no sockets and reads no files —
 * IO happens only when the adapter calls into it.
 */

import { Agent, request as httpsRequest } from "node:https";
import { readFile, stat } from "node:fs/promises";

import type {
  Clock,
  FileSystemReader,
  HttpRequest,
  HttpResponse,
  HttpTransport,
  LcuRuntime,
  LockfileSnapshot,
  RandomSource,
  Sleeper,
} from "@lol/lcu";

/**
 * A loopback-scoped HTTPS agent that accepts the LCU's self-signed certificate.
 * `rejectUnauthorized:false` is confined to this agent — never set globally and
 * never reused for non-loopback traffic. `keepAlive` avoids per-call handshakes.
 */
const LOOPBACK_AGENT = new Agent({ rejectUnauthorized: false, keepAlive: true });

/** Node `https`-backed transport scoped to the local LCU / Live-Client origins. */
class NodeHttpsTransport implements HttpTransport {
  request(req: HttpRequest): Promise<HttpResponse> {
    return new Promise<HttpResponse>((resolve, reject) => {
      const url = new URL(req.url);
      const clientReq = httpsRequest(
        {
          protocol: url.protocol,
          hostname: url.hostname,
          port: url.port,
          path: `${url.pathname}${url.search}`,
          method: req.method,
          headers: req.headers,
          agent: LOOPBACK_AGENT,
        },
        (res) => {
          const chunks: Buffer[] = [];
          res.on("data", (chunk: Buffer) => chunks.push(chunk));
          res.on("end", () => {
            resolve({
              status: res.statusCode ?? 0,
              body: Buffer.concat(chunks).toString("utf8"),
            });
          });
        },
      );
      clientReq.on("error", reject);
      if (req.body !== undefined) {
        clientReq.write(req.body);
      }
      clientReq.end();
    });
  }
}

/** `node:fs/promises`-backed lockfile reader (bytes + mtime for rotation). */
class NodeFileSystemReader implements FileSystemReader {
  async readLockfile(path: string): Promise<LockfileSnapshot> {
    const [contents, info] = await Promise.all([
      readFile(path, "utf8"),
      stat(path),
    ]);
    return { contents, mtimeMs: info.mtimeMs };
  }
}

const SYSTEM_CLOCK: Clock = { nowMs: () => Date.now() };

const SYSTEM_SLEEPER: Sleeper = {
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
};

const SYSTEM_RANDOM: RandomSource = { next: () => Math.random() };

/**
 * Build the live {@link LcuRuntime} the adapter runs over in the desktop main
 * process. Pure constructor — opens no sockets / reads no files on call.
 */
export function createRealLcuRuntime(): LcuRuntime {
  return {
    http: new NodeHttpsTransport(),
    fs: new NodeFileSystemReader(),
    clock: SYSTEM_CLOCK,
    sleeper: SYSTEM_SLEEPER,
    random: SYSTEM_RANDOM,
  };
}
