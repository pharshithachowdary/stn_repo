#!/bin/sh
# 1) wait for Postgres  2) run Alembic migrations  3) start Uvicorn.
# Compose already gates startup on the db healthcheck (depends_on: service_healthy);
# the loop below makes the container equally safe when started by other means.
set -eu

echo "[entrypoint] waiting for database..."
python - <<'PY'
import sys, time
from sqlalchemy import create_engine, text
from app.config import get_settings

engine = create_engine(get_settings().database_url, connect_args={"connect_timeout": 3})
for attempt in range(1, 31):
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        print(f"[entrypoint] database ready (attempt {attempt})")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"[entrypoint] not ready ({type(exc).__name__}); retrying...")
        time.sleep(2)
print("[entrypoint] database never became ready", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] running alembic migrations..."
alembic upgrade head

echo "[entrypoint] starting uvicorn..."
# ONE worker process on purpose: the APScheduler worker lives inside the app process.
# Scale out with more containers (the advisory lock keeps ingestion single-flight).
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
