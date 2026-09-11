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
    # Notice a wedged connection within a minute instead of hanging on it.
    -o ServerAliveInterval=15
    -o ServerAliveCountMax=4
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

# Size of a remote file in bytes — empty when it does not exist, and ALSO empty
# when the server could not be asked. That second case is the whole point: this
# runs right after a transfer, i.e. right after the connection may have died,
# and under `set -e -o pipefail` a failing sftp here used to abort the script
# instead of letting _put resume (2026-09-11: a .dmg stalled at 51%, the size
# check hit "server not responding", exit 255, nothing renamed — but nothing
# resumed either).
_remote_size() {
    { _sftp "ls -l $1" 2>/dev/null || true; } | tr -d '\r' | awk -v f="$1" '$NF == f && $1 ~ /^-/ { print $5 }' | tail -1
}

# Upload one local file as <name>.uploading. Big files over a home uplink stall
# and get cut ("Connection closed by remote host" mid-file), so this is a
# resume loop, not a single transfer: whatever the server already holds under
# the temporary name is finished with sftp's reput rather than started over —
# including on a fresh run after a previous one died — and only a remote size
# equal to the local one counts as done. A temp file LARGER than the local one
# is a leftover from another build and is replaced from scratch.
_put() {
    local name remote local_size remote_size attempt=0
    name="$(basename "$1")"
    remote="$UPLOAD_DIR/$name.uploading"
    local_size="$(wc -c < "$1" | tr -d ' ')"
    echo "--- $name ($(du -h "$1" | cut -f1 | tr -d ' '))"
    remote_size="$(_remote_size "$remote")"
    while :; do
        if [ "$remote_size" = "$local_size" ]; then
            [ "$attempt" -eq 0 ] && echo "    already on the server ($remote_size bytes) — skipping"
            return 0
        fi
        attempt=$((attempt + 1))
        # Patient on purpose: the link to the server drops for minutes at a
        # time (2026-09-11: three outages during one release), and each retry
        # is cheap because it resumes rather than restarts.
        if [ "$attempt" -gt 30 ]; then
            _die "$name: still incomplete after $((attempt - 1)) attempts (remote has ${remote_size:-0} of $local_size bytes)."
        fi
        if [ -z "$remote_size" ] || [ "$remote_size" -eq 0 ] || [ "$remote_size" -gt "$local_size" ]; then
            _run scp "${SSH_OPTS[@]}" "$1" "$TARGET:$remote" || true
        else
            echo "    server has ${remote_size}/$local_size bytes — resuming (attempt $attempt)"
            _sftp "reput $1 $remote" || true
        fi
        remote_size="$(_remote_size "$remote")"
        if [ "$remote_size" != "$local_size" ]; then
            if [ -z "$remote_size" ]; then
                echo "    server not reachable — retrying in 20 s (attempt $attempt)"
            else
                echo "    connection dropped at $remote_size/$local_size bytes — retrying in 20 s (attempt $attempt)"
            fi
            sleep 20
        fi
    done
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
