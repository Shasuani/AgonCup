#!/usr/bin/env bash
#
# Build the desktop client for Windows FROM WSL and stage it on a Windows-visible
# path, wired to a chosen backend. One command, no Wine.
#
#   make desktop-win                               # -> J:\loltournament, devel backend
#   BACKEND_URL=http://localhost:8000 make desktop-win
#   OUT_DIR=/mnt/c/tmp/lol make desktop-win
#
# WHY NOT electron-builder
# ------------------------
# `electron-builder --win` (even with --dir) shells out to rcedit to stamp exe
# metadata, and on Linux that runs under Wine — "wine is required" is a hard
# failure. Wine is a ~1 GB dependency to gain branding on a build nobody ships.
# So this script does what --dir does minus the branding: unpack the official
# win32-x64 Electron, drop the app into resources/app, done. The result is a
# real Windows build that runs natively (it must — the client talks to the LCU
# on 127.0.0.1), it just carries Electron's own icon and version metadata.
#
# For anything SHIPPED, use the signed electron-builder pipeline instead
# (.github/workflows/desktop-build.yml); this is a test harness, not a release.
set -euo pipefail

BACKEND_URL="${BACKEND_URL:-https://lol.gameras.gr}"
OUT_DIR="${OUT_DIR:-/mnt/j/loltournament}"
# The client reads TWO urls (src/main.ts backendBaseUrl / the realtime socket).
# Setting only BACKEND_URL leaves the lobby WebSocket pointed at localhost, which
# does not surface until orchestration actually runs — derive it by default.
REALTIME_URL="${REALTIME_URL:-$(printf '%s' "$BACKEND_URL" \
  | sed -e 's|^https://|wss://|' -e 's|^http://|ws://|')/api/v1/realtime/ws}"

DESKTOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_DIR/../.." && pwd)"
LCU_DIR="$REPO_ROOT/libs/lol-lcu"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# --- guard the output directory ---------------------------------------------
# OUT_DIR is wiped on every run. Only ever wipe something this script produced
# (or an empty/new directory) — a typo'd OUT_DIR must not eat a real folder.
if [ -e "$OUT_DIR" ]; then
  if [ ! -d "$OUT_DIR" ]; then
    echo "OUT_DIR exists and is not a directory: $OUT_DIR" >&2
    exit 1
  fi
  if [ -n "$(ls -A "$OUT_DIR" 2>/dev/null)" ] \
     && [ ! -f "$OUT_DIR/LeagueOfLeagues.exe" ] \
     && [ "${FORCE:-0}" != "1" ]; then
    echo "Refusing to wipe $OUT_DIR: not empty and not a previous build." >&2
    echo "Re-run with FORCE=1 if you are sure." >&2
    exit 1
  fi
fi

# --- 1. @lol/lcu ------------------------------------------------------------
# A file: dep whose package main/types point at ./dist, which is not in git.
say "building @lol/lcu"
cd "$LCU_DIR"
[ -d node_modules ] || npm ci --no-audit --no-fund
npm run build

# --- 2. desktop main + renderer ---------------------------------------------
say "building desktop (tsc + vite)"
cd "$DESKTOP_DIR"
[ -d node_modules ] || npm ci --no-audit --no-fund
npm run build

# --- 3. Windows Electron runtime --------------------------------------------
# @electron/get ships with electron; it serves the local cache and only hits the
# network when the zip is missing.
say "resolving Electron win32-x64"
ELECTRON_ZIP="$(node -e '
const { downloadArtifact } = require("@electron/get");
const version = require("electron/package.json").version;
downloadArtifact({ version, platform: "win32", arch: "x64", artifactName: "electron" })
  .then((p) => console.log(p))
  .catch((e) => { console.error(e); process.exit(1); });
')"
echo "$ELECTRON_ZIP"

# --- 4. stage ---------------------------------------------------------------
say "staging into $OUT_DIR"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
unzip -q "$ELECTRON_ZIP" -d "$OUT_DIR"
mv "$OUT_DIR/electron.exe" "$OUT_DIR/LeagueOfLeagues.exe"

# Electron resolves resources/app.asar, then resources/app, then the bundled
# default_app.asar — so an unpacked resources/app takes precedence as-is.
APP="$OUT_DIR/resources/app"
mkdir -p "$APP/node_modules/@lol/lcu"
cp -r dist dist-renderer static package.json "$APP/"
cp -r node_modules/ws "$APP/node_modules/"          # the only runtime dep besides @lol/lcu
cp -r "$LCU_DIR/dist" "$LCU_DIR/package.json" "$APP/node_modules/@lol/lcu/"

# --- 5. bake the urls as defaults -------------------------------------------
# Both urls are read from env at RUNTIME, so a bare double-click on the exe would
# silently talk to localhost:8000 and fail with "Backend unreachable: fetch
# failed". Rewriting the fallbacks in the STAGED copy (never the repo) makes the
# exe work on its own; run.cmd below still sets the env and wins.
#
# Verified rather than assumed: if a refactor changes these literals the sed
# quietly no-ops and we would ship a localhost build, so a missing replacement is
# a hard failure.
say "baking backend urls into the staged build"
sed -i \
  -e "s|\"http://localhost:8000\"|\"$BACKEND_URL\"|g" \
  -e "s|\"ws://localhost:8000/api/v1/realtime/ws\"|\"$REALTIME_URL\"|g" \
  "$APP/dist/main.js"
for expected in "$BACKEND_URL" "$REALTIME_URL"; do
  grep -qF "\"$expected\"" "$APP/dist/main.js" || {
    echo "FAILED to bake $expected — the fallback literal in src/main.ts changed." >&2
    exit 1
  }
done
grep -qF 'localhost:8000' "$APP/dist/main.js" && {
  echo "A localhost:8000 fallback survived baking." >&2
  exit 1
}

# --- 6. launcher ------------------------------------------------------------
# CRLF: .cmd files are parsed by cmd.exe, which is happier with DOS endings.
printf '@echo off\r\nsetlocal\r\nset "BACKEND_URL=%s"\r\nset "REALTIME_URL=%s"\r\nstart "" "%%~dp0LeagueOfLeagues.exe"\r\n' \
  "$BACKEND_URL" "$REALTIME_URL" > "$OUT_DIR/run.cmd"

say "done"
cat <<EOF
  output    $OUT_DIR
  backend   $BACKEND_URL
  realtime  $REALTIME_URL
  launch    run.cmd (preferred — sets both urls in env); the exe alone now works
            too, since the same urls are baked in as defaults.
EOF
