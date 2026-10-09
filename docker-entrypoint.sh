#!/bin/sh
# Container entrypoint: apply database migrations, then start the process.
#
# Platforms with a pre-deploy hook (e.g. Render) can run `alembic upgrade head`
# before the release goes live; platforms that start the container directly
# (e.g. Blitz) rely on this step. Alembic only applies the forward, additive
# revisions under migrations/versions and never drops existing data.
#
# Behaviour:
#   AUTO_CREATE_SCHEMA=true        -> skip (local dev creates tables in-app)
#   a database URL is configured   -> run migrations (retry, then fail fast)
#   no database URL is configured  -> skip and warn so the app still serves
set -eu

url_configured=0
for candidate in \
    "${DATABASE_URL:-}" \
    "${DATABASE_URI:-}" \
    "${POSTGRES_URL:-}" \
    "${POSTGRESQL_URL:-}" \
    "${DB_URL:-}"
do
    if [ -n "$candidate" ]; then
        url_configured=1
        break
    fi
done

# Some container platforms build the image without the alembic scripts folder
# (e.g. blitz.cloud when docker-compose.yml drives the build). Without the
# scripts the migration CLI cannot run, so fall back to the app creating its
# own schema. This keeps the container bootable; migration scripts never leave
# the repo, so alembic keeps working in CI and on any host that packages them.
REPO_DIR="${REPO_DIR:-$(pwd)}"
if [ ! -d "${REPO_DIR}/migrations" ]; then
    echo "[entrypoint] WARNING: migrations/ is not packaged (looked in ${REPO_DIR});" >&2
    echo "[entrypoint] forcing AUTO_CREATE_SCHEMA=true so the app creates its schema." >&2
    export AUTO_CREATE_SCHEMA=true
fi

if [ "${AUTO_CREATE_SCHEMA:-false}" = "true" ]; then
    echo "[entrypoint] AUTO_CREATE_SCHEMA=true; the app creates the schema; skipping migrations."
elif [ "$url_configured" -eq 1 ]; then
    echo "[entrypoint] Applying database migrations (alembic upgrade head)..."
    attempt=1
    max_attempts="${MIGRATE_MAX_ATTEMPTS:-5}"
    while true; do
        if alembic upgrade head; then
            echo "[entrypoint] Database schema is up to date."
            break
        fi
        if [ "$attempt" -ge "$max_attempts" ]; then
            echo "[entrypoint] ERROR: database migrations failed after ${attempt} attempt(s)." >&2
            echo "[entrypoint] Check DATABASE_URL and that the database is reachable." >&2
            exit 1
        fi
        echo "[entrypoint] Migration attempt ${attempt} failed; retrying in ${MIGRATE_RETRY_SECONDS:-3}s..." >&2
        attempt=$((attempt + 1))
        sleep "${MIGRATE_RETRY_SECONDS:-3}"
    done
else
    echo "[entrypoint] WARNING: no database URL configured (expected DATABASE_URL)." >&2
    echo "[entrypoint] Skipping migrations; starting anyway (/health reports the database state)." >&2
fi

exec "$@"
