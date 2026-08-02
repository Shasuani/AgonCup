# syntax=docker/dockerfile:1.7
# Multi-stage build for the LeagueOfLeagues worker (arq).
# Builds on the shared python base image (PLAN.md §6 / SHARED CONVENTIONS).
# Build the base first:  docker build -t lol/python-base:dev infra/images/python-base
# (or `make build-bases` from the repo root).

# ---------- builder ----------------------------------------------------------
# CI builds the base image into the GitLab registry (a fresh dind daemon has no
# local `lol/python-base:dev`), so the base reference is a build-arg. The default keeps
# `make up` / local builds working unchanged.
ARG PYTHON_BASE_IMAGE=lol/python-base:dev

FROM ${PYTHON_BASE_IMAGE} AS builder

# The base image drops to the unprivileged runtime user, but the BUILD stage has
# to write outside its home (the venv at /opt/venv, the /repo tree). Build as root;
# the runtime stage below switches back to the non-root user.
USER root

WORKDIR /build

# Copy the shared libs first so their path deps resolve, then the worker project.
# (Build context is the repo root.)
COPY libs/lol-schemas /build/libs/lol-schemas
COPY libs/lol-core /build/libs/lol-core
# Keep in sync with [tool.uv.sources] in services/worker/pyproject.toml.
COPY libs/lol-riot /build/libs/lol-riot
COPY services/worker/pyproject.toml /build/services/worker/pyproject.toml
COPY services/worker/uv.lock /build/services/worker/uv.lock
COPY services/worker/README.md /build/services/worker/README.md
COPY services/worker/src /build/services/worker/src

WORKDIR /build/services/worker

ENV UV_PROJECT_ENVIRONMENT=/opt/venv
# --no-editable is load-bearing — see the comment in services/backend/Dockerfile:
# an editable path dep only writes a .pth pointing into /build, which the runtime
# stage never receives. --no-install-project skips lol-worker; its source ships
# at /app/src.
RUN uv venv /opt/venv \
    && uv sync --frozen --no-dev --no-editable --no-install-project

# ---------- runtime ----------------------------------------------------------
FROM ${PYTHON_BASE_IMAGE} AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/worker/src /app/src

# src/ layout: make app importable.
ENV PYTHONPATH=/app/src

USER app

# The worker has no HTTP port; liveness = the Redis broker is reachable.
# (A deeper check — round-tripping the `ping` job — lands with monitoring in a
# later phase; PLAN.md §6 healthchecks-on-every-long-running-service.)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,redis; redis.Redis.from_url(os.environ.get('REDIS_URL','redis://redis:6379/0')).ping()" || exit 1

CMD ["arq", "app.main.WorkerSettings"]
