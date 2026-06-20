# syntax=docker/dockerfile:1.7
# BUILD/PACKAGING container ONLY — the desktop app RUNS NATIVELY on the player's
# machine (it must reach 127.0.0.1 LCU / :2999). This image compiles the
# TypeScript and can produce a Linux-side bundle for CI artifact assembly, but
# signed native installers are built on per-OS CI runners (macOS notarization +
# Windows Authenticode), NOT here (PLAN.md §6 desktop packaging).
FROM lol/node-base:dev AS builder

WORKDIR /build

# The @lol/lcu path dep is resolved from the repo (build context = repo root).
COPY libs/lol-lcu /build/libs/lol-lcu
COPY services/desktop/package.json /build/services/desktop/package.json
COPY services/desktop/tsconfig.json /build/services/desktop/tsconfig.json
COPY services/desktop/src /build/services/desktop/src

WORKDIR /build/services/desktop
RUN npm install --no-audit --no-fund \
    && npm run build

# Artifacts (dist/) are copied out by CI; there is no runtime stage on purpose.
