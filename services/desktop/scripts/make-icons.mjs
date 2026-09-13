/**
 * Render build/icon.svg into the icon files electron-builder embeds
 * (electron-builder.yml `mac.icon` / `win.icon`):
 *
 *   build/icon.icns  macOS app bundle + .dmg  (packed with `iconutil`, so this
 *                    script runs on a Mac — which is where `npm run dist:all`
 *                    runs anyway, see infra/publish-desktop.sh)
 *   build/icon.ico   Windows exe, taskbar, NSIS installer/uninstaller
 *   build/icon.png   1024² master; the dock icon for `electron .` runs
 *                    (src/main.ts createWindow — a packaged build carries the
 *                    icon inside the bundle instead)
 *
 * ...and, so the website carries the SAME icon, into services/frontend/public/
 * (services/frontend/index.html links them):
 *
 *   favicon.svg           the tile full bleed, fine detail stripped (Chrome/Firefox)
 *   favicon.ico           16/32/48 px full-bleed tile (Safari ignores SVG favicons)
 *   apple-touch-icon.png  180² full bleed (iOS rounds the corners itself)
 *   og-image.png          public/og-image.svg — the social share card, which
 *                         embeds the same mark — rendered at 1200×630
 *
 * Every size is rendered from the vector at that exact pixel size (Chromium
 * inside Electron, offscreen, transparent), not downscaled from 1024, so the
 * small taskbar/Finder sizes stay crisp. Run: `npm run icons` (or
 * `npm run icons -- desktop` / `npm run icons -- web` for one side) — and commit
 * the outputs: electron-builder, the browsers and the OG scrapers read the
 * files, not the SVG.
 */
import { app, BrowserWindow } from "electron";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const buildDir = join(here, "..", "build");
/** The website's static files (served as-is by vite / nginx). */
const webDir = join(here, "..", "..", "frontend", "public");
const execFileP = promisify(execFile);

/** Apple's iconset naming: [file stem, pixel size]. */
const MAC_ICONSET = [
  ["icon_16x16", 16], ["icon_16x16@2x", 32],
  ["icon_32x32", 32], ["icon_32x32@2x", 64],
  ["icon_128x128", 128], ["icon_128x128@2x", 256],
  ["icon_256x256", 256], ["icon_256x256@2x", 512],
  ["icon_512x512", 512], ["icon_512x512@2x", 1024],
];
/** Sizes Windows actually picks from an .ico (Explorer, taskbar, alt-tab, title bar). */
const WIN_SIZES = [256, 128, 64, 48, 40, 32, 24, 20, 16];
/** favicon.ico: browser tab (16), retina tab / bookmarks (32), Windows site shortcuts (48). */
const FAVICON_SIZES = [48, 32, 16];
const APPLE_TOUCH_SIZE = 180;
const OG_SIZE = { width: 1200, height: 630 };

/** Hairlines, glow and shadow are noise at 32 px and below — strip them. */
const FINE_DETAIL_MAX = 32;

/**
 * Adapt the master SVG for one platform + size.
 *  - win: crop the viewBox to the 824² tile (full bleed, the Windows convention)
 *         and drop the macOS drop shadow that would bleed into the corners.
 *  - small: remove class="fine" elements.
 */
function variant(svg, { platform, size }) {
  let out = svg.replace(/<\?xml[^>]*\?>\s*/, "").replace(/<!--[\s\S]*?-->\s*/g, "");
  if (platform === "win") {
    out = out.replace('viewBox="0 0 1024 1024"', 'viewBox="100 100 824 824"');
    out = out.replace(/<rect class="fine mac-shadow"[^>]*\/>\s*/, "");
  }
  if (size <= FINE_DETAIL_MAX) {
    out = out.replace(/<(?:rect|path) class="fine[^"]*"[^>]*\/>\s*/g, "");
  }
  return out.replace('width="1024" height="1024"', `width="${size}" height="${size}"`);
}

/** Scratch dir for the per-size HTML wrappers the windows load from disk. */
let scratch = null;

/** Render an SVG string to a `width`×`height` PNG buffer with a transparent background. */
async function renderPng(svg, width, height = width) {
  scratch ??= await mkdtemp(join(tmpdir(), "lol-icons-"));
  console.log(height === width ? `render ${width}px` : `render ${width}×${height}px`);
  const win = new BrowserWindow({
    show: false,
    width,
    height,
    useContentSize: true,
    transparent: true,
    frame: false,
    webPreferences: { offscreen: true },
  });
  try {
    const html =
      `<!doctype html><html><head><meta charset="utf-8"><style>` +
      `html,body{margin:0;padding:0;background:transparent;width:${width}px;height:${height}px;overflow:hidden}` +
      `svg{display:block}</style></head><body>${svg}</body></html>`;
    const page = join(scratch, `icon-${width}x${height}-${Date.now()}.html`);
    await writeFile(page, html, "utf8");
    await win.loadFile(page);
    // Offscreen windows emit `paint` per frame; wait for one after load so the
    // capture is not a blank first frame.
    await new Promise((resolve) => {
      win.webContents.once("paint", () => resolve());
      win.webContents.invalidate();
    });
    let image = await win.webContents.capturePage({ x: 0, y: 0, width, height });
    const got = image.getSize();
    if (got.width !== width || got.height !== height) {
      image = image.resize({ width, height, quality: "best" });
    }
    return image.toPNG();
  } finally {
    win.destroy();
  }
}

/**
 * Pack PNG images into an .ico (PNG-compressed entries, supported since Vista;
 * Explorer, the taskbar and NSIS all read them). Layout: 6-byte header,
 * 16-byte directory entry per image, then the image blobs.
 */
function packIco(entries) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type: 1 = icon
  header.writeUInt16LE(entries.length, 4);
  const dir = Buffer.alloc(16 * entries.length);
  let offset = header.length + dir.length;
  entries.forEach(({ size, png }, i) => {
    const o = i * 16;
    dir.writeUInt8(size >= 256 ? 0 : size, o); // 0 means 256
    dir.writeUInt8(size >= 256 ? 0 : size, o + 1);
    dir.writeUInt8(0, o + 2); // palette colours (none)
    dir.writeUInt8(0, o + 3); // reserved
    dir.writeUInt16LE(1, o + 4); // colour planes
    dir.writeUInt16LE(32, o + 6); // bits per pixel
    dir.writeUInt32LE(png.length, o + 8);
    dir.writeUInt32LE(offset, o + 12);
    offset += png.length;
  });
  return Buffer.concat([header, dir, ...entries.map((e) => e.png)]);
}

/** The website's icons + share card, from the same master SVG (see the header). */
async function web(svg) {
  // The vector favicon is the 32 px Windows variant verbatim: full bleed, no
  // hairlines/glow (noise at tab size) — browsers scale it to whatever they need.
  const favSvg = variant(svg, { platform: "win", size: 32 }).replace(
    "<svg ",
    '<svg role="img" aria-label="LeagueOfLeagues" ',
  );
  await writeFile(join(webDir, "favicon.svg"), `${favSvg}\n`, "utf8");
  console.log("wrote frontend/public/favicon.svg");

  const favEntries = [];
  for (const size of FAVICON_SIZES) {
    favEntries.push({ size, png: await renderPng(variant(svg, { platform: "win", size }), size) });
  }
  await writeFile(join(webDir, "favicon.ico"), packIco(favEntries));
  console.log(`wrote frontend/public/favicon.ico (${FAVICON_SIZES.join(", ")} px)`);

  await writeFile(
    join(webDir, "apple-touch-icon.png"),
    await renderPng(variant(svg, { platform: "win", size: APPLE_TOUCH_SIZE }), APPLE_TOUCH_SIZE),
  );
  console.log(`wrote frontend/public/apple-touch-icon.png (${APPLE_TOUCH_SIZE} px)`);

  const og = await readFile(join(webDir, "og-image.svg"), "utf8");
  await writeFile(join(webDir, "og-image.png"), await renderPng(og, OG_SIZE.width, OG_SIZE.height));
  console.log(`wrote frontend/public/og-image.png (${OG_SIZE.width}×${OG_SIZE.height} px)`);
}

async function main() {
  const svg = await readFile(join(buildDir, "icon.svg"), "utf8");
  // `npm run icons -- web` / `-- desktop` renders one side; no argument = both.
  const picked = process.argv.filter((a) => a === "desktop" || a === "web");
  const want = (side) => picked.length === 0 || picked.includes(side);

  if (want("web")) await web(svg);
  if (!want("desktop")) {
    if (scratch !== null) await rm(scratch, { recursive: true, force: true });
    return;
  }

  // macOS: iconset -> icns. Sizes that appear twice (32, 256, 512) are rendered once.
  const iconset = join(buildDir, "icon.iconset");
  await rm(iconset, { recursive: true, force: true });
  await mkdir(iconset, { recursive: true });
  const macCache = new Map();
  for (const [stem, size] of MAC_ICONSET) {
    if (!macCache.has(size)) macCache.set(size, await renderPng(variant(svg, { platform: "mac", size }), size));
    await writeFile(join(iconset, `${stem}.png`), macCache.get(size));
  }
  await writeFile(join(buildDir, "icon.png"), macCache.get(1024));
  if (process.platform === "darwin") {
    await execFileP("iconutil", ["-c", "icns", iconset, "-o", join(buildDir, "icon.icns")]);
    console.log("wrote build/icon.icns");
  } else {
    console.log("skipped build/icon.icns (needs macOS iconutil)");
  }
  await rm(iconset, { recursive: true, force: true });
  console.log("wrote build/icon.png");

  // Windows: full-bleed tile at each size Windows reads -> icon.ico.
  const winEntries = [];
  for (const size of WIN_SIZES) {
    winEntries.push({ size, png: await renderPng(variant(svg, { platform: "win", size }), size) });
  }
  await writeFile(join(buildDir, "icon.ico"), packIco(winEntries));
  console.log(`wrote build/icon.ico (${WIN_SIZES.join(", ")} px)`);
  if (scratch !== null) await rm(scratch, { recursive: true, force: true });
}

app.disableHardwareAcceleration();
// Each size gets its own (destroyed) window; without this Electron quits the
// moment the first one closes and the run ends after a single render.
app.on("window-all-closed", () => {});
app.whenReady().then(main).then(
  () => app.exit(0),
  (err) => {
    console.error(err);
    app.exit(1);
  },
);
