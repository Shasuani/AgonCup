// Download tiles for the desktop client — one square per OS, Windows and Mac.
//
// The installers are served from /downloads/ on THIS origin: the edge Caddy
// exposes the server's downloads directory there (infra/publish-desktop.sh
// fills it), so no CSP change and no version number here — the two names below
// are stable and always point at the newest build. The visitor's own OS gets
// the highlighted tile; the other stays one click away (people download for a
// teammate's machine too).

export const WINDOWS_INSTALLER_URL = "/downloads/AgonCup-Setup.exe";
export const MAC_INSTALLER_URL = "/downloads/AgonCup.dmg";

export type DesktopOs = "windows" | "mac" | "other";

/** Best-effort OS sniff from the user agent. Only picks which tile is
 * highlighted — it never hides one. */
export function detectDesktopOs(userAgent: string): DesktopOs {
  if (/windows/i.test(userAgent)) return "windows";
  if (/macintosh|mac os x/i.test(userAgent)) return "mac";
  return "other";
}

// Brand glyphs (Simple Icons, CC0). Filled shapes on the 24-grid, painted with
// currentColor so they follow the tile's text color in both themes. Kept out
// of <Icon> on purpose: that set is stroked line icons, these are logos.
function WindowsGlyph(): JSX.Element {
  return (
    <svg viewBox="0 0 24 24" width="40" height="40" aria-hidden="true" focusable="false">
      <path
        fill="currentColor"
        d="M0 3.449 9.75 2.1v9.451H0m10.949-9.602L24 0v11.4H10.949M0 12.6h9.75v9.451L0 20.699M10.949 12.6H24V24l-12.9-1.801"
      />
    </svg>
  );
}

function AppleGlyph(): JSX.Element {
  return (
    <svg viewBox="0 0 24 24" width="40" height="40" aria-hidden="true" focusable="false">
      <path
        fill="currentColor"
        d="M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701"
      />
    </svg>
  );
}

interface TileProps {
  os: "windows" | "mac";
  href: string;
  name: string;
  meta: string;
  recommended: boolean;
  glyph: JSX.Element;
}

function DownloadTile({ os, href, name, meta, recommended, glyph }: TileProps): JSX.Element {
  return (
    <a
      href={href}
      download
      data-download-tile
      data-os={os}
      data-recommended={recommended || undefined}
      aria-label={`Download for ${name}`}
    >
      {recommended && <span data-download-tile-badge>For this device</span>}
      <span data-download-tile-glyph>{glyph}</span>
      <span data-download-tile-name>{name}</span>
      <span data-download-tile-meta>{meta}</span>
    </a>
  );
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
      <div data-download-client-tiles>
        <DownloadTile
          os="windows"
          href={WINDOWS_INSTALLER_URL}
          name="Windows"
          meta="Windows 10 / 11"
          recommended={detected === "windows"}
          glyph={<WindowsGlyph />}
        />
        <DownloadTile
          os="mac"
          href={MAC_INSTALLER_URL}
          name="Mac"
          meta="Intel & Apple Silicon"
          recommended={detected === "mac"}
          glyph={<AppleGlyph />}
        />
      </div>
    </section>
  );
}
