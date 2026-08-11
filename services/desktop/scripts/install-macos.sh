#!/usr/bin/env bash
#
# Build the desktop client and INSTALL it to a fixed location on macOS, wired to
# a chosen backend. One command; the installed app is always the latest build.
#
#   make desktop-mac                                  # -> /Applications, prod backend
#   BACKEND_URL=http://localhost:8000 make desktop-mac
#   APP_DEST="$HOME/Applications/LeagueOfLeagues.app" make desktop-mac
#
# WHY INSTALL INSTEAD OF `npm start`
# ----------------------------------
# `npm start` runs whatever happens to be in dist/ at that moment, from a shell
# that carries BACKEND_URL/REALTIME_URL in its environment. Double-clicking an
# app gets neither: no env, and no guarantee the bundle is current. This script
# closes both gaps — it rebuilds, bakes the urls into the staged bundle, and
# replaces the installed app, so launching from Spotlight/Finder always runs the
# latest build against the right backend.
#
# WHY --dir + asar:false + A STABLE LOCAL SIGNATURE
# -------------------------------------------------
# A .dmg is a distribution format; we only need the .app, and `--mac dir` skips
# the disk-image step. asar is disabled so the url baking below can rewrite a
# real file in the staged bundle (never the repo's dist/, which stays a clean dev
# build). Rewriting files breaks any signature, so we sign LAST — with a
# self-signed identity created on first run, NOT ad-hoc (see step 6: an ad-hoc
# signature changes every build, which cost the user their Keychain session and
# a re-pair every time). Hardened runtime is off: it is a notarization
# requirement, and a locally-signed hardened bundle is the shape macOS is most
# suspicious of.
#
# For anything SHIPPED, use the signed electron-builder pipeline instead
# (.github/workflows/desktop-build.yml); this is a dev install, not a release.
set -euo pipefail

#: The stable local code-signing identity (see step 6 — it is what keeps the
#: saved session readable across reinstalls).
SIGN_IDENTITY="LeagueOfLeagues Dev"

BACKEND_URL="${BACKEND_URL:-https://lol.gameras.gr}"
# The client reads TWO urls (src/main.ts backendBaseUrl / the realtime socket).
# Setting only BACKEND_URL leaves the lobby WebSocket pointed at localhost, which
# does not surface until orchestration actually runs — derive it by default.
REALTIME_URL="${REALTIME_URL:-$(printf '%s' "$BACKEND_URL" \
  | sed -e 's|^https://|wss://|' -e 's|^http://|ws://|')/api/v1/realtime/ws}"
APP_DEST="${APP_DEST:-/Applications/LeagueOfLeagues.app}"

DESKTOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_DIR/../.." && pwd)"
LCU_DIR="$REPO_ROOT/libs/lol-lcu"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
die() { printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "This script is macOS-only (use build-win.sh for Windows)."

# --- guard the destination ---------------------------------------------------
# APP_DEST is deleted on every run. Only ever delete something this script
# produced — a typo'd APP_DEST must not eat an unrelated application.
case "$APP_DEST" in
  *.app) : ;;
  *) die "APP_DEST must end in .app: $APP_DEST" ;;
esac
if [ -e "$APP_DEST" ] && [ ! -e "$APP_DEST/Contents/Resources/app/package.json" ] \
   && [ "${FORCE:-0}" != "1" ]; then
  die "Refusing to replace $APP_DEST: not a previous build of this script.
Re-run with FORCE=1 if you are sure."
fi

# --- node 22 -----------------------------------------------------------------
# services/desktop pins Electron 43, which needs Node >= 22 to build. The shell
# default here is Node 20, so fall back to an nvm-installed 22 rather than
# failing with an opaque tsc/vite error.
node_major() { node -v 2>/dev/null | sed -e 's/^v//' -e 's/\..*//'; }
if [ "$(node_major)" -lt 22 ] 2>/dev/null; then
  NODE22="$(ls -d "$HOME"/.nvm/versions/node/v22.* 2>/dev/null | sort -V | tail -1)"
  [ -n "$NODE22" ] || die "Node >= 22 required (found $(node -v 2>/dev/null || echo none)).
Install it: nvm install 22"
  export PATH="$NODE22/bin:$PATH"
  say "using Node $(node -v) from $NODE22"
fi

# --- 1. @lol/lcu -------------------------------------------------------------
# A file: dep whose package main/types point at ./dist, which is not in git.
say "building @lol/lcu"
cd "$LCU_DIR"
[ -d node_modules ] || npm ci --no-audit --no-fund
npm run build

# --- 2. desktop main + renderer ----------------------------------------------
say "building desktop (tsc + vite)"
cd "$DESKTOP_DIR"
[ -d node_modules ] || npm ci --no-audit --no-fund
npm run build

# --- 3. package the .app -----------------------------------------------------
# CSC_IDENTITY_AUTO_DISCOVERY=false so the build does not silently pick up a
# Developer ID from the login keychain — the signature is applied in step 6, and
# a build that depends on which certs happen to be installed is not repeatable.
say "packaging (electron-builder --mac dir)"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64) EB_ARCH="--arm64" ;;
  x86_64) EB_ARCH="--x64" ;;
  *) die "unsupported architecture: $ARCH" ;;
esac
rm -rf release
CSC_IDENTITY_AUTO_DISCOVERY=false npx electron-builder \
  --config electron-builder.yml \
  --mac dir "$EB_ARCH" \
  -c.asar=false \
  -c.mac.hardenedRuntime=false

STAGED="$(find release -maxdepth 2 -name '*.app' -print -quit)"
[ -n "$STAGED" ] || die "electron-builder produced no .app under release/"
echo "staged: $STAGED"

# --- 4. bake the urls as defaults --------------------------------------------
# Both urls are read from env at RUNTIME, and a Finder-launched app inherits no
# shell environment — so without this the installed client silently talks to
# localhost:8000 and fails with "Backend unreachable: fetch failed". We rewrite
# the fallbacks in the STAGED copy only; the repo's dist/ stays a dev build.
#
# Verified rather than assumed: if a refactor changes these literals the sed
# quietly no-ops and we would install a localhost build, so a missing
# replacement is a hard failure.
say "baking backend urls into the staged bundle"
MAIN_JS="$STAGED/Contents/Resources/app/dist/main.js"
[ -f "$MAIN_JS" ] || die "expected $MAIN_JS (is asar still enabled?)"
sed -i '' \
  -e "s|\"http://localhost:8000\"|\"$BACKEND_URL\"|g" \
  -e "s|\"ws://localhost:8000/api/v1/realtime/ws\"|\"$REALTIME_URL\"|g" \
  "$MAIN_JS"
for expected in "$BACKEND_URL" "$REALTIME_URL"; do
  grep -qF "\"$expected\"" "$MAIN_JS" \
    || die "FAILED to bake $expected — the fallback literal in src/main.ts changed."
done
# Only meaningful when we baked something ELSE in: installing a deliberately
# local build (BACKEND_URL=http://localhost:8000, to test against `make
# dev-backend`) leaves the literal in place by design, and this guard used to
# reject that as a failed bake.
case "$BACKEND_URL$REALTIME_URL" in
  *localhost:8000*) ;;
  *)
    if grep -qF 'localhost:8000' "$MAIN_JS"; then
      die "A localhost:8000 fallback survived baking."
    fi
    ;;
esac

# --- 5. install --------------------------------------------------------------
# Quit the running copy first: replacing a bundle under a live process leaves it
# in a half-swapped state, and the next launch reads a mix of both builds.
say "installing to $APP_DEST"
osascript -e 'tell application "System Events" to if exists (process "LeagueOfLeagues") then tell application "LeagueOfLeagues" to quit' \
  >/dev/null 2>&1 || true
rm -rf "$APP_DEST"
mkdir -p "$(dirname "$APP_DEST")"
ditto "$STAGED" "$APP_DEST"

# --- 6. sign + de-quarantine -------------------------------------------------
# Signed with a STABLE local identity, not ad-hoc, and this is the whole point:
# macOS binds a Keychain item to the signature of the app that created it, and an
# ad-hoc signature is a hash of the bundle — so every reinstall was a different
# app as far as the Keychain was concerned. It asked for the login password, then
# refused the key that decrypts the stored session, so the client came up
# unpaired and the pairing code had to be redeemed again. Every single build.
#
# A self-signed certificate keeps the designated requirement fixed across
# rebuilds (identifier + leaf certificate), so the session survives. Created once
# on first run; nothing about it is secret, and it is trusted only for this user.
say "signing + clearing quarantine"
if ! security find-identity -v -p codesigning | grep -q "$SIGN_IDENTITY"; then
  say "creating the local signing identity (first run only)"
  CERT_DIR="$HOME/.config/leagueofleagues"
  mkdir -p "$CERT_DIR"
  cat > "$CERT_DIR/codesign.cnf" <<'CNF'
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = LeagueOfLeagues Dev
[v3]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF
  openssl req -x509 -newkey rsa:2048 -days 3650 -nodes \
    -keyout "$CERT_DIR/codesign.key" -out "$CERT_DIR/codesign.crt" \
    -config "$CERT_DIR/codesign.cnf" >/dev/null 2>&1
  # -legacy: Keychain cannot read a PKCS#12 sealed with OpenSSL 3's default MAC.
  openssl pkcs12 -export -legacy -passout pass:lol \
    -inkey "$CERT_DIR/codesign.key" -in "$CERT_DIR/codesign.crt" \
    -out "$CERT_DIR/codesign.p12" >/dev/null 2>&1
  # -A: let codesign use the key without a prompt on every build.
  security import "$CERT_DIR/codesign.p12" -k "$HOME/Library/Keychains/login.keychain-db" \
    -P lol -T /usr/bin/codesign -A >/dev/null
  # Trusted for code signing only, in the USER's keychain — no admin rights, and
  # it makes the certificate no more trusted than it needs to be.
  security add-trusted-cert -r trustRoot -p codeSign \
    -k "$HOME/Library/Keychains/login.keychain-db" "$CERT_DIR/codesign.crt" >/dev/null
  security find-identity -v -p codesigning | grep -q "$SIGN_IDENTITY" \
    || die "could not create the local signing identity"
fi

xattr -dr com.apple.quarantine "$APP_DEST" 2>/dev/null || true
codesign --force --deep --sign "$SIGN_IDENTITY" "$APP_DEST"
codesign --verify --deep "$APP_DEST" || die "signature failed to verify"

say "done"
cat <<EOF
  installed $APP_DEST
  backend   $BACKEND_URL
  realtime  $REALTIME_URL
  launch    Spotlight/Finder "LeagueOfLeagues", or: open -a "$APP_DEST"
            (both urls are baked in; env vars still override when set)
EOF
