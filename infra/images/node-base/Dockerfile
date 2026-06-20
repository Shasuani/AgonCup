# infra/images/node-base/Dockerfile
#
# Shared Node.js base image for all TypeScript components (frontend, desktop build,
# lol-lcu, lol-clients-ts). Pinned toolchain once, reused everywhere.
# Build and tag as `lol/node-base:dev`:
#
#     docker build -t lol/node-base:dev infra/images/node-base
#
# (or run `make build-bases` from the repo root).
#
# TS service Dockerfiles are MULTI-STAGE and start `FROM lol/node-base:dev`, so
# this image MUST be built before them.

FROM node:22-bookworm-slim

# --- OS toolchain ----------------------------------------------------------------
# build-essential: native node-gyp addons. curl/git: fetch + healthchecks + tooling.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Enable corepack so pnpm/yarn are available without a global install.
RUN corepack enable

ENV NODE_ENV=development \
    npm_config_update_notifier=false

# --- non-root runtime user -------------------------------------------------------
# The official node image already ships an unprivileged `node` user (uid/gid 1000).
# Use it; just create and hand over the workdir.
RUN mkdir -p /app && chown -R node:node /app

WORKDIR /app

USER node

CMD ["node", "--version"]
