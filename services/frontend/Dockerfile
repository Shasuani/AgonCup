# syntax=docker/dockerfile:1.7
# Multi-stage build for the LeagueOfLeagues admin SPA (build context = repo root).
# Build the node base first:  docker build -t lol/node-base:dev infra/images/node-base

# ---------- builder ----------------------------------------------------------
FROM lol/node-base:dev AS builder

# Preserve the monorepo layout so the @lol/clients-ts file: dep + the vite alias
# (../../libs/lol-clients-ts/src) resolve. Bring in BOTH the lib and the app.
WORKDIR /repo
COPY libs/lol-clients-ts /repo/libs/lol-clients-ts
COPY services/frontend /repo/services/frontend

WORKDIR /repo/services/frontend
# No committed lockfile yet -> npm install (a tracked follow-up). vite compiles
# the lol-clients-ts TypeScript source directly via the configured alias, so the
# lib does NOT need a separate dist build for the bundle.
RUN npm install --no-audit --no-fund
RUN npm run build

# ---------- runtime ----------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# SPA fallback + security headers (CSP, etc.) — see nginx.conf.
COPY services/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /repo/services/frontend/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
