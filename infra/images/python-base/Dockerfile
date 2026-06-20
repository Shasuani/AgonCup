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

# --- OS toolchain (build essentials + curl for healthchecks / uv install) --------
# Keep this layer small but real: build-essential is needed for any package that
# compiles C extensions; curl is used by healthchecks and to fetch uv if needed.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- uv (fast Python package/resolver manager) -----------------------------------
# Copy the statically-linked `uv` binary straight from the official Astral image.
# This is the recommended, reproducible install path (no pip bootstrap needed).
# Pin the uv image tag for reproducibility.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

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
