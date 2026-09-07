// Download buttons for the desktop client — one for Windows, one for Mac.
//
// The installers are served from /downloads/ on THIS origin: the edge Caddy
// exposes the server's downloads directory there (infra/publish-desktop.sh
// fills it), so no CSP change and no version number here — the two names below
// are stable and always point at the newest build. The visitor's own OS gets
// the primary button; the other stays one click away (people download for a
// teammate's machine too).

import { Icon } from "./Icon";

export const WINDOWS_INSTALLER_URL = "/downloads/LeagueOfLeagues-Setup.exe";
export const MAC_INSTALLER_URL = "/downloads/LeagueOfLeagues.dmg";

export type DesktopOs = "windows" | "mac" | "other";

/** Best-effort OS sniff from the user agent. Only picks which button is
 * emphasised — it never hides one. */
export function detectDesktopOs(userAgent: string): DesktopOs {
  if (/windows/i.test(userAgent)) return "windows";
  if (/macintosh|mac os x/i.test(userAgent)) return "mac";
  return "other";
}

export interface DownloadClientProps {
  /** Override the sniffed OS (tests, storybook-style previews). */
  os?: DesktopOs;
  /** Compact = no intro copy; for surfaces that already explain the client. */
  compact?: boolean;
}

export function DownloadClient({ os, compact = false }: DownloadClientProps): JSX.Element {
  const detected =
    os ?? detectDesktopOs(typeof navigator === "undefined" ? "" : navigator.userAgent);
  return (
    <section data-download-client aria-labelledby="download-client-heading">
      <h2 id="download-client-heading">Get the desktop client</h2>
      {!compact && (
        <p data-download-client-copy>
          Players run matches through the desktop client: it opens the custom lobby,
          invites the teams and reports the result. It checks for updates on its own.
        </p>
      )}
      <div data-download-client-buttons>
        <a
          href={WINDOWS_INSTALLER_URL}
          download
          data-variant={detected === "windows" ? "primary" : "secondary"}
        >
          <Icon name="download" size={18} />
          Download for Windows
        </a>
        <a
          href={MAC_INSTALLER_URL}
          download
          data-variant={detected === "mac" ? "primary" : "secondary"}
        >
          <Icon name="download" size={18} />
          Download for Mac
        </a>
      </div>
      <p data-download-client-note>
        Windows 10/11 (64-bit) · macOS (Intel and Apple Silicon)
      </p>
    </section>
  );
}
