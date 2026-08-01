#!/bin/sh
#
# Deploy-time database migration entrypoint. Runs INSIDE the backend container:
#
#     docker compose -f docker-compose.prod.yml exec -T backend /app/scripts/migrate.sh
#
# Plain `alembic upgrade head` — it works on an empty database and on an existing
# one alike. (It did not always: the old 0001..0012 chain built its tables from
# the LIVE ORM metadata in 0002, so on a fresh database 0004..0012 re-added
# columns that already existed. The chain is now a single static baseline; see
# alembic/versions/0001_baseline.py.)
#
# Also rotates the app_rw login off the dev-only password baked into the
# baseline. Do this in every real environment — app_rw is the non-BYPASSRLS role
# the request path authenticates as, and 'app_rw'/'app_rw' is public in the repo.
#
# Env (already present in the container via env_file):
#   DATABASE_URL      owner/superuser DSN — what alembic uses (alembic/env.py
#                     reads it through app.config.get_settings)
#   APP_DB_PASSWORD   optional; new password for the app_rw role

set -eu

cd /app

echo "==> alembic upgrade head"
alembic upgrade head

# --- rotate the app_rw password off the baseline's dev default ---------------
if [ -n "${APP_DB_PASSWORD:-}" ]; then
    echo "==> rotating the app_rw role password"
    python - <<'PY'
import asyncio
import os

import asyncpg


async def main() -> None:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    password = os.environ["APP_DB_PASSWORD"]
    # ALTER ROLE cannot take a bind parameter for the password, so quote it as a
    # literal (doubling embedded single quotes) — the value comes from our own
    # CI variable, never from user input.
    literal = "'" + password.replace("'", "''") + "'"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"ALTER ROLE app_rw WITH PASSWORD {literal}")
    finally:
        await conn.close()


asyncio.run(main())
PY
fi

echo "==> migrations complete: $(alembic current 2>/dev/null | tail -1)"
