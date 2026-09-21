# syntax=docker/dockerfile:1.7
# Multi-stage build for the AgonCup backend (FastAPI on uvicorn).
# Builds on the shared python base image (PLAN.md §6 / SHARED CONVENTIONS).
# The base image provides Python 3.12, uv, build essentials, and a non-root
# "app" user. Build the base first:  docker build -t lol/python-base:dev infra/images/python-base

# CI builds the base image into the GitLab registry (there is no local
# `lol/python-base:dev` inside a fresh dind daemon), so the base reference is a
# build-arg. The default keeps `make up` / local builds working unchanged.
ARG PYTHON_BASE_IMAGE=lol/python-base:dev

# ---------- builder ----------------------------------------------------------
FROM ${PYTHON_BASE_IMAGE} AS builder

# The base image drops to the unprivileged runtime user, but the BUILD stage has
# to write outside its home (the venv at /opt/venv, the /repo tree). Build as root;
# the runtime stage below switches back to the non-root user.
USER root

WORKDIR /build

# uv manages the environment. Copy the shared libs first so their path deps
# resolve, then the backend project. (Build context is the repo root.)
COPY libs/lol-schemas /build/libs/lol-schemas
COPY libs/lol-core /build/libs/lol-core
# Keep this list in sync with [tool.uv.sources] in services/backend/pyproject.toml —
# a missing path dep fails the build with "Distribution not found at: file:///...".
COPY libs/lol-realtime /build/libs/lol-realtime
COPY libs/lol-riot /build/libs/lol-riot
COPY services/backend/pyproject.toml /build/services/backend/pyproject.toml
COPY services/backend/uv.lock /build/services/backend/uv.lock
COPY services/backend/README.md /build/services/backend/README.md
COPY services/backend/src /build/services/backend/src

WORKDIR /build/services/backend

# Create a self-contained virtualenv at /opt/venv with the app + libs installed.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
# --no-editable is load-bearing: [tool.uv.sources] marks the shared libs
# `editable = true` for host dev, and an editable install only writes a .pth
# pointing at /build/libs/*/src — a path that does not exist in the runtime
# stage, which copies /opt/venv alone. The image then builds and pushes fine and
# dies at import with ModuleNotFoundError. --no-install-project skips lol-backend
# itself; its source ships separately at /app/src (see PYTHONPATH below).
RUN uv venv /opt/venv \
    && uv sync --frozen --no-dev --no-editable --no-install-project

# ---------- runtime ----------------------------------------------------------
FROM ${PYTHON_BASE_IMAGE} AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production

WORKDIR /app

# Bring the resolved venv and the application source from the builder.
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/backend/alembic.ini /app/alembic.ini
COPY --chown=app:app services/backend/alembic /app/alembic
COPY --chown=app:app services/backend/src /app/src
# Deploy-time migration entrypoint ONLY. The rest of services/backend/scripts/
# is dev tooling (the tournament simulator can DELETE data) and is deliberately
# NOT shipped in the runtime image.
COPY --chown=app:app services/backend/scripts/migrate.sh /app/scripts/migrate.sh

# src/ layout: make app importable.
ENV PYTHONPATH=/app/src

# Non-root runtime (user provided by the base image).
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
