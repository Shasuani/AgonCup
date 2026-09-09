#!/usr/bin/env bash
#
# Build the desktop client installers on a Mac and upload them to the website.
#
#     infra/publish-desktop.sh            # build both installers, then upload them
#     infra/publish-desktop.sh --no-build # upload what services/desktop/release/ already holds
#     infra/publish-desktop.sh --list     # just log in and show what the server has now
#
# Nothing about the installers goes through git (release/, *.dmg and *.exe are
# gitignored) or the pipeline. The upload goes over SFTP as the dedicated
# `lolupload` account, which is chrooted to the server's downloads folder — the
# folder Caddy serves at https://lol.gameras.gr/downloads/ (the pipeline only
# makes sure it exists). FileZilla with the same account (protocol SFTP, port
# 20002) does the same job by hand; this script is that without the clicking.
#
# Five files, whose names carry NO version (electron-builder.yml → artifactName),
# so each release overwrites the previous one and nothing accumulates:
#     LeagueOfLeagues-Setup.exe   Windows installer (site button + Windows self-update)
#     LeagueOfLeagues.dmg         macOS installer (site button)
#     LeagueOfLeagues.zip         what installed macOS clients download to update
#     latest.yml, latest-mac.yml  tell installed clients a new version exists
# Each file goes up under a temporary name and is renamed into place (atomic on
# the server, so nobody ever downloads a half-written installer); installers
# first and the feeds LAST — a client that reads latest.yml immediately
# downloads the installer it names, which must already be complete.
#
# PASSWORD — never stored in the repo. Looked up in this order:
#   1. $LOL_UPLOAD_PASSWORD in the environment,
#   2. the macOS Keychain item "lol-desktop-upload" (the script offers to save
#      it there the first time),
#   3. otherwise it asks.
# Needs macOS (for the .dmg), Node 22 on PATH, and `expect` (ships with macOS)
# to answer the password prompt.
#
# Release = bump "version" in services/desktop/package.json
# (`npm version minor --no-git-tag-version` there), run this. Installed clients
# ask the backend (GET /api/v1/desktop/version, which reads the feeds) at start
# and every 30 minutes and update only when the version number grew. To force
# old clients to update, set DESKTOP_MINIMUM_VERSION on the backend.
#
# Server (override via env):
#     UPLOAD_HOST=lol.gameras.gr UPLOAD_PORT=20002 UPLOAD_USER=lolupload UPLOAD_DIR=/downloads

set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-}"
case "$MODE" in
    ""|--no-build|--list) ;;
    *) echo "usage: $0 [--no-build|--list]" >&2; exit 2 ;;
esac

UPLOAD_HOST="${UPLOAD_HOST:-lol.gameras.gr}"
UPLOAD_PORT="${UPLOAD_PORT:-20002}"
UPLOAD_USER="${UPLOAD_USER:-lolupload}"
UPLOAD_DIR="${UPLOAD_DIR:-/downloads}"      # as seen from inside the account's chroot
KEYCHAIN_SERVICE="lol-desktop-upload"
TARGET="$UPLOAD_USER@$UPLOAD_HOST"

RELEASE="${DESKTOP_RELEASE_DIR:-services/desktop/release}"

# Must match electron-builder.yml (artifactName), src/update/installers.ts,
# the backend's app/desktop/service.py and the SPA's DownloadClient component.
INSTALLERS=(
    "$RELEASE/LeagueOfLeagues-Setup.exe"
    "$RELEASE/LeagueOfLeagues.dmg"
    "$RELEASE/LeagueOfLeagues.zip"
)
FEEDS=(
    "$RELEASE/latest.yml"
    "$RELEASE/latest-mac.yml"
)

_die() { echo "ERROR: $*" >&2; exit 1; }

command -v expect >/dev/null 2>&1 || _die "'expect' not found (it ships with macOS; on Linux: apt install expect)."

# -----------------------------------------------------------------------------
# Build
# -----------------------------------------------------------------------------
if [ "$MODE" = "" ]; then
    if [ "$(uname -s)" != "Darwin" ]; then
        _die "the .dmg can only be built on macOS — run this on a Mac (--no-build uploads an existing release/)."
    fi
    node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
    if [ "$node_major" -lt 22 ]; then
        _die "Node 22+ is needed on PATH (found: $(node --version 2>/dev/null || echo none))."
    fi
    # Old outputs (including versioned names from before the stable-name change)
    # would otherwise sit next to the new files.
    rm -f "$RELEASE"/*.exe "$RELEASE"/*.dmg "$RELEASE"/*.zip "$RELEASE"/*.blockmap "$RELEASE"/latest*.yml
    echo "=== building installers (services/desktop: npm run dist:all)"
    (cd services/desktop && npm run dist:all)
fi

if [ "$MODE" != "--list" ]; then
    missing=0
    for f in "${INSTALLERS[@]}" "${FEEDS[@]}"; do
        if [ ! -f "$f" ]; then
            echo "ERROR: missing $f" >&2
            missing=1
        fi
    done
    if [ "$missing" -ne 0 ]; then
        _die "build first (run without --no-build, on a Mac); latest*.yml need 'publish' in electron-builder.yml."
    fi
fi

# -----------------------------------------------------------------------------
# Password
# -----------------------------------------------------------------------------
if [ -z "${LOL_UPLOAD_PASSWORD:-}" ] && [ "$(uname -s)" = "Darwin" ]; then
    LOL_UPLOAD_PASSWORD="$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$UPLOAD_USER" -w 2>/dev/null || true)"
fi
if [ -z "${LOL_UPLOAD_PASSWORD:-}" ]; then
    if [ ! -t 0 ]; then
        _die "no password: set LOL_UPLOAD_PASSWORD, or run this once in a terminal to store it in the Keychain."
    fi
    read -rs -p "SFTP password for $TARGET: " LOL_UPLOAD_PASSWORD
    echo
    [ -n "$LOL_UPLOAD_PASSWORD" ] || _die "empty password."
    if [ "$(uname -s)" = "Darwin" ]; then
        read -r -p "Save it in the macOS Keychain for next time? [y/N] " yn
        case "$yn" in
            y|Y)
                security add-generic-password -U -s "$KEYCHAIN_SERVICE" -a "$UPLOAD_USER" -w "$LOL_UPLOAD_PASSWORD"
                echo "saved — Keychain Access, item \"$KEYCHAIN_SERVICE\" (delete it there to change it)"
                ;;
        esac
    fi
fi
export LOL_UPLOAD_PASSWORD

# -----------------------------------------------------------------------------
# SSH plumbing: scp/sftp driven by expect, which types the password.
# -----------------------------------------------------------------------------
EXPECT_SCRIPT="$(mktemp)"
trap 'rm -f "$EXPECT_SCRIPT"' EXIT
cat > "$EXPECT_SCRIPT" <<'EXP'
# Runs the command given as arguments; answers the password prompt from the
# environment (never from the command line, so it shows up in no process list).
set timeout -1
spawn -noecho {*}$argv
expect {
    -re {[Pp]assword: ?$}       { send -- "$env(LOL_UPLOAD_PASSWORD)\r"; exp_continue }
    -re {\(yes/no[^)]*\)\? ?$}   { send -- "yes\r"; exp_continue }
    eof
}
lassign [wait] pid spawnid oserr code
exit $code
EXP
_run() { expect -f "$EXPECT_SCRIPT" "$@"; }

# The account is password-only and chrooted: don't offer keys (an agent full of
# them trips "Too many authentication failures"), and use SFTP-mode scp
# (OpenSSH 9+ default; the legacy protocol needs a shell the account lacks).
SSH_OPTS=(
    -P "$UPLOAD_PORT"
    -o StrictHostKeyChecking=accept-new
    -o PubkeyAuthentication=no
    -o PreferredAuthentications=password,keyboard-interactive
)

# One sftp session running the given batch commands (aborts on the first error).
_sftp() {
    local batch
    batch="$(mktemp)"
    printf '%s\n' "$@" > "$batch"
    # -oBatchMode=no must come BEFORE -b (which would otherwise disable the prompt).
    _run sftp -oBatchMode=no "${SSH_OPTS[@]}" -b "$batch" "$TARGET"
    local code=$?
    rm -f "$batch"
    return $code
}

if [ "$MODE" = "--list" ]; then
    echo "=== $TARGET:$UPLOAD_DIR"
    _sftp "ls -l $UPLOAD_DIR"
    exit 0
fi

# Upload one local file as <name>.uploading (scp shows a progress meter).
_put() {
    local name
    name="$(basename "$1")"
    echo "--- $name ($(du -h "$1" | cut -f1 | tr -d ' '))"
    _run scp "${SSH_OPTS[@]}" "$1" "$TARGET:$UPLOAD_DIR/$name.uploading"
}

# Rename <name>.uploading -> <name> for every given local file, in one session.
# sftp's rename uses OpenSSH's posix-rename, i.e. it replaces the old file
# atomically like rename(2).
_commit() {
    local cmds=() f name
    for f in "$@"; do
        name="$(basename "$f")"
        cmds+=("rename $UPLOAD_DIR/$name.uploading $UPLOAD_DIR/$name")
    done
    _sftp "${cmds[@]}"
}

echo "=== uploading to $TARGET:$UPLOAD_DIR"
for f in "${INSTALLERS[@]}"; do _put "$f"; done
_commit "${INSTALLERS[@]}"
for f in "${FEEDS[@]}"; do _put "$f"; done
_commit "${FEEDS[@]}"

echo "=== published (HTTP status from the site):"
for f in "${INSTALLERS[@]}" "${FEEDS[@]}"; do
    url="https://$UPLOAD_HOST/downloads/$(basename "$f")"
    printf '    %s  %s\n' "$(curl -sI -o /dev/null -w '%{http_code}' "$url" || echo '???')" "$url"
done
echo "    https://$UPLOAD_HOST/api/v1/desktop/version  (what installed clients ask; cached 5 min)"
