# syntax=docker/dockerfile:1.7
# Multi-stage build for the LeagueOfLeagues backend (FastAPI on uvicorn).
# Builds on the shared python base image (PLAN.md §6 / SHARED CONVENTIONS).
# The base image provides Python 3.12, uv, build essentials, and a non-root
# "app" user. Build the base first:  docker build -t lol/python-base:dev infra/images/python-base

# ---------- builder ----------------------------------------------------------
FROM lol/python-base:dev AS builder

WORKDIR /build

# uv manages the environment. Copy the shared libs first so their path deps
# resolve, then the backend project. (Build context is the repo root.)
COPY libs/lol-schemas /build/libs/lol-schemas
COPY libs/lol-core /build/libs/lol-core
COPY services/backend/pyproject.toml /build/services/backend/pyproject.toml
COPY services/backend/README.md /build/services/backend/README.md
COPY services/backend/src /build/services/backend/src

WORKDIR /build/services/backend

# Create a self-contained virtualenv at /opt/venv with the app + libs installed.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python .

# ---------- runtime ----------------------------------------------------------
FROM lol/python-base:dev AS runtime

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

# src/ layout: make app importable.
ENV PYTHONPATH=/app/src

# Non-root runtime (user provided by the base image).
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
