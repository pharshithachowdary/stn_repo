"""Test harness. Runs against a REAL PostgreSQL (the code uses ON CONFLICT and
advisory locks, which SQLite cannot emulate), with the schema built by Alembic -
so the migrations themselves are exercised by every test run."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Must happen BEFORE any `app` import: point the app at the test DB. -------
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg2://stn:stn@localhost:5432/stn_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["WORKER_ENABLED"] = "false"  # tests drive the worker explicitly

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return cfg


def _ensure_test_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    assert url.database and url.database.endswith("_test"), (
        "Refusing to run: TEST_DATABASE_URL must point at a database whose name ends in '_test' "
        "(the suite TRUNCATEs tables)."
    )
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database():
    _ensure_test_database()
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    from app.db.session import dispose_engines

    dispose_engines()


@pytest.fixture(autouse=True)
def _clean_tables(_database):
    from app.db.session import get_engine

    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE conjunction_events RESTART IDENTITY"))
    yield


@pytest.fixture
def client(_database):
    from app.main import app

    with TestClient(app) as c:
        yield c


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def future_iso(hours: float = 48) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def past_iso(hours: float = 48) -> str:
    return future_iso(-hours)


def make_cdm(**overrides) -> dict:
    """A valid payload matching the assignment's data contract.
    Pass ``key=...`` (Ellipsis) to OMIT a key entirely."""
    base = {
        "message_id": "CDM-2026-X992",
        "event_time_utc": future_iso(),
        "primary_norad_id": 25544,
        "primary_object_type": "PAYLOAD",
        "secondary_norad_id": 41235,
        "secondary_object_type": "DEBRIS",
        "miss_distance_m": 450.2,
        "collision_probability": 0.00012,
        "relative_velocity_kms": 11.4,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not ...}


def fetch_row(message_id: str) -> dict | None:
    """Read the raw DB row, bypassing the ORM/API so tests see what is truly stored."""
    from app.db.session import get_engine

    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT * FROM conjunction_events WHERE message_id = :m"), {"m": message_id}
        ).mappings().first()
    return dict(row) if row else None


def count_rows() -> int:
    from app.db.session import get_engine

    with get_engine().connect() as conn:
        return conn.execute(text("SELECT count(*) FROM conjunction_events")).scalar_one()
