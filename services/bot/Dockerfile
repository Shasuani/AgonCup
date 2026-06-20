# syntax=docker/dockerfile:1.7
# Multi-stage build for the LeagueOfLeagues Discord bot (discord.py).
# Builds on the shared python base image. Build the base first:
#   docker build -t lol/python-base:dev infra/images/python-base   (or `make build-bases`)

# ---------- builder ----------------------------------------------------------
FROM lol/python-base:dev AS builder

WORKDIR /build

COPY libs/lol-schemas /build/libs/lol-schemas
COPY libs/lol-discord /build/libs/lol-discord
COPY services/bot/pyproject.toml /build/services/bot/pyproject.toml
COPY services/bot/README.md /build/services/bot/README.md
COPY services/bot/src /build/services/bot/src

WORKDIR /build/services/bot

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

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/bot/src /app/src

ENV PYTHONPATH=/app/src

USER app

# The bot has no inbound port; liveness is the gateway connection itself.
# A deeper readiness probe is added with monitoring in a later phase.
CMD ["python", "-m", "app.main"]
