#!/usr/bin/env bash
# Render start command. Tries Alembic first; falls back to create_all so a
# fresh database is never the reason a demo fails.
set -u
echo "[start] applying database migrations..."
if ! alembic upgrade head; then
  echo "[start] alembic failed - falling back to metadata.create_all()"
  python -m app.bootstrap_db
fi
echo "[start] launching uvicorn on port ${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
