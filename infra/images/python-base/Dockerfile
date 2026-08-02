# infra/images/python-base/Dockerfile
#
# Shared Python base image for all server-side Python services (backend, worker, bot).
# Pinned toolchain once, reused everywhere. Build and tag as `lol/python-base:dev`:
#
#     docker build -t lol/python-base:dev infra/images/python-base
#
# (or run `make build-bases` from the repo root, which builds this first).
#
# Service Dockerfiles (services/backend, services/worker, ...) are MULTI-STAGE and
# start `FROM lol/python-base:dev`, so this image MUST be built before them.

FROM python:3.12-slim-bookworm

# --- OS toolchain ----------------------------------------------------------------
# Deliberately NO build-essential. The service Dockerfiles use this image for BOTH
# their builder AND their runtime stage, so anything installed here ships to
# production — and the toolchain (gcc, cpp, binutils, perl, dpkg-dev) accounted for
# 189 of the 213 HIGH/CRITICAL findings `security:images` reported, on top of the
# 24 the upstream python:3.12-slim image carries on its own.
#
# It is not needed: every dependency of backend, worker and bot resolves to a
# manylinux wheel, verified by running `uv sync --frozen` for all three in a bare
# python:3.12-slim container with no compiler present. If a future dependency ships
# only an sdist the build will fail loudly here — add build-essential to that
# service's BUILDER stage then, never to this shared base.
#
# curl is gone for the same reason: nothing used it. Every HEALTHCHECK in the three
# services shells out to `python`, not curl, and uv arrives as a static binary via
# the COPY below rather than being fetched.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- uv (fast Python package/resolver manager) -----------------------------------
# Copy the statically-linked `uv` binary straight from the official Astral image.
# This is the recommended, reproducible install path (no pip bootstrap needed).
# Pin the uv image tag for reproducibility.
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /uvx /usr/local/bin/

# uv configuration:
#   - UV_LINK_MODE=copy  : avoid hardlink warnings across mounted volumes.
#   - UV_COMPILE_BYTECODE : faster cold starts in containers.
#   - UV_PYTHON_DOWNLOADS=never : use the system interpreter we already have.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# --- non-root runtime user -------------------------------------------------------
# All services run as the unprivileged `app` user (uid/gid 10001). The app's source
# and virtualenv live under /app, owned by `app`.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app \
    && mkdir -p /app \
    && chown -R app:app /app

# --- sane workdir ----------------------------------------------------------------
WORKDIR /app

# Put the project's uv-managed virtualenv on PATH so service stages can just call
# `python`, `uvicorn`, `arq`, etc. once they `uv sync`.
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

USER app

# Sanity: the base image itself just drops to a shell; service images add the app.
CMD ["python", "--version"]
