#!/usr/bin/env bash
#
# Build the desktop client installers and put them on the website.
#
#     infra/publish-desktop.sh            # build both installers, then upload
#     infra/publish-desktop.sh --no-build # upload what is already in release/
#
# Run it on a Mac (the .dmg can only be built there; the Windows installer
# cross-builds fine from macOS). Needs Node 22 on PATH and SSH access to the
# server as the deploy user — the same host/user/port the GitLab deploy job uses
# (docs/gitlab-cicd.md §"Server access"). Override any of them via env:
#
#     DEPLOY_HOST=rust.gameras.gr DEPLOY_USER=loltournament DEPLOY_SSH_PORT=20002
#     DEPLOY_PATH=/home/$DEPLOY_USER/lol
#
# What happens:
#   1. `npm run dist:all` in services/desktop -> release/: one universal .dmg,
#      one Windows .exe (+ .blockmap), latest.yml and latest-mac.yml.
#   2. The files are copied to <DEPLOY_PATH>/edge/downloads on the server. Caddy
#      serves that directory at https://<domain>/downloads/ (the site block in
#      .gitlab-ci.yml; mounted read-only by infra/docker-compose.edge.yml).
#      Installers first, the yml feed files LAST — an installed client that reads
#      latest.yml must find the installer it names already there.
#   3. The stable names the website's buttons link at — LeagueOfLeagues.dmg and
#      LeagueOfLeagues-Setup.exe — are hard-linked to the versioned files just
#      uploaded, so the buttons never need to know a version number.
#
# Shipping a new version = bump "version" in services/desktop/package.json and
# run this again. Installed Windows clients pick it up on their own (electron-
# updater reads latest.yml); Mac clients are shown a prompt that opens the .dmg
# (unsigned builds cannot self-update on macOS — see services/desktop/src/updater.ts).
# Older versioned files are left in place; delete them by hand if disk matters.

set -euo pipefail

cd "$(dirname "$0")/.."

DEPLOY_HOST="${DEPLOY_HOST:-rust.gameras.gr}"
DEPLOY_USER="${DEPLOY_USER:-loltournament}"
SSH_PORT="${DEPLOY_SSH_PORT:-20002}"
DEPLOY_ROOT="${DEPLOY_PATH:-/home/$DEPLOY_USER/lol}"
DEST_DIR="$DEPLOY_ROOT/edge/downloads"
TARGET="$DEPLOY_USER@$DEPLOY_HOST"

# Must match src/updater.ts (MAC_INSTALLER_FILE / WINDOWS_INSTALLER_FILE) and
# the SPA's DownloadClient component.
MAC_STABLE="LeagueOfLeagues.dmg"
WIN_STABLE="LeagueOfLeagues-Setup.exe"

RELEASE="services/desktop/release"

if [ "${1:-}" != "--no-build" ]; then
    echo "=== building installers (services/desktop: npm run dist:all)"
    (cd services/desktop && npm run dist:all)
fi

# Exactly one .dmg and one .exe are expected in release/ (the previous build's
# files are removed by electron-builder only for the same version, so a stale
# release/ from another version would make the glob ambiguous — fail loudly).
_only_one() {
    local pattern="$1" label="$2"
    local matches=()
    while IFS= read -r -d '' f; do matches+=("$f"); done < <(find "$RELEASE" -maxdepth 1 -name "$pattern" -print0)
    if [ "${#matches[@]}" -ne 1 ]; then
        echo "ERROR: expected exactly one $label in $RELEASE, found ${#matches[@]}:" >&2
        printf '  %s\n' "${matches[@]}" >&2
        echo "Clear $RELEASE and rebuild (drop --no-build)." >&2
        exit 1
    fi
    printf '%s' "${matches[0]}"
}

DMG="$(_only_one '*.dmg' '.dmg')"
EXE="$(_only_one '*.exe' '.exe')"
EXE_BLOCKMAP="$EXE.blockmap"
for f in "$EXE_BLOCKMAP" "$RELEASE/latest.yml" "$RELEASE/latest-mac.yml"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: missing $f — is 'publish' still set in electron-builder.yml?" >&2
        exit 1
    fi
done

echo "=== uploading to $TARGET:$DEST_DIR (port $SSH_PORT)"
echo "    $(basename "$DMG")"
echo "    $(basename "$EXE")"
ssh -p "$SSH_PORT" "$TARGET" "mkdir -p '$DEST_DIR'"
# Installers first, feed files last (see the header).
scp -P "$SSH_PORT" "$DMG" "$EXE" "$EXE_BLOCKMAP" "$TARGET:$DEST_DIR/"
scp -P "$SSH_PORT" "$RELEASE/latest.yml" "$RELEASE/latest-mac.yml" "$TARGET:$DEST_DIR/"

echo "=== pointing the stable names at this version"
ssh -p "$SSH_PORT" "$TARGET" "cd '$DEST_DIR' \
    && ln -f '$(basename "$DMG")' '$MAC_STABLE' \
    && ln -f '$(basename "$EXE")' '$WIN_STABLE' \
    && ls -l"

echo "=== published:"
echo "    https://lol.gameras.gr/downloads/$MAC_STABLE"
echo "    https://lol.gameras.gr/downloads/$WIN_STABLE"
echo "    https://lol.gameras.gr/downloads/latest.yml  (Windows auto-update feed)"
