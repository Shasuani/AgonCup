# syntax=docker/dockerfile:1.7
# Multi-stage build for the LeagueOfLeagues admin SPA.
# Build stage uses the shared node base image; runtime serves static files via nginx.
# Build the base first:  docker build -t lol/node-base:dev infra/images/node-base

# ---------- builder ----------------------------------------------------------
FROM lol/node-base:dev AS builder

WORKDIR /build

# Install deps against the lockfile-less package.json (Phase 0). Later: copy a
# committed lockfile first for cached, reproducible installs.
COPY services/frontend/package.json /build/package.json
RUN npm install

COPY services/frontend/ /build/
RUN npm run build

# ---------- runtime ----------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
