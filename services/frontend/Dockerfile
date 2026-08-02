# syntax=docker/dockerfile:1.7
# Multi-stage build for the LeagueOfLeagues admin SPA (build context = repo root).
# Build the node base first:  docker build -t lol/node-base:dev infra/images/node-base

# ---------- builder ----------------------------------------------------------
# CI builds the base image into the GitLab registry (a fresh dind daemon has no
# local `lol/node-base:dev`), so the base reference is a build-arg. The default keeps
# `make up` / local builds working unchanged.
ARG NODE_BASE_IMAGE=lol/node-base:dev

FROM ${NODE_BASE_IMAGE} AS builder

# The base image drops to the unprivileged runtime user, but the BUILD stage has
# to write outside its home (the venv at /opt/venv, the /repo tree). Build as root;
# the runtime stage below switches back to the non-root user.
USER root

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
# NODE_ENV=production ONLY for the build step, not for `npm install` above —
# npm would skip devDependencies and vite itself would disappear.
#
# It has to be set at all because lol/node-base pins NODE_ENV=development (right
# for dev tooling), and Vite derives isProduction from process.env.NODE_ENV
# BEFORE falling back to the build mode. Inheriting it makes `vite build` emit a
# DEVELOPMENT bundle: React's jsxDEV runtime with per-element line numbers, dev
# warnings, and every `import.meta.env.DEV` block retained — which is how the
# "Dev login (local only)" button reached the deployed SPA.
RUN NODE_ENV=production npm run build

# ---------- runtime ----------------------------------------------------------
FROM nginx:1.29-alpine AS runtime

# Patch the OS packages the nginx image ships. The tag already tracks a current
# Alpine (3.23.x); it is the packages baked into it that lag, and they are the
# ONLY source of OS CVEs in this stack — the Python images are Debian-based.
# Without this, `security:images` reports c-ares/curl/openssl/expat/libxml2/
# nghttp2 findings that a plain `apk upgrade` clears entirely.
RUN apk upgrade --no-cache

# SPA fallback + security headers (CSP, etc.) — see nginx.conf.
COPY services/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /repo/services/frontend/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
