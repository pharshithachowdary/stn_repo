from alembic import command
from sqlalchemy import inspect, text

from app.db.session import get_engine
from tests.conftest import alembic_config


def test_healthz_reports_api_database_and_worker(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] == "up"
    assert body["worker"]["enabled"] is False  # disabled for tests


def test_healthz_returns_503_when_database_is_unreachable(client, monkeypatch):
    from app.main import app
    from app.db.session import get_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    dead = create_engine("postgresql+psycopg2://x:y@127.0.0.1:1/nope", connect_args={"connect_timeout": 1})

    def broken_db():
        with sessionmaker(bind=dead)() as s:
            yield s

    app.dependency_overrides[get_db] = broken_db
    try:
        r = client.get("/healthz")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 503
    assert r.json()["status"] == "degraded" and r.json()["database"] == "down"


def test_schema_has_expected_indexes_and_nullable_metrics():
    insp = inspect(get_engine())
    idx = {i["name"]: i for i in insp.get_indexes("conjunction_events")}
    assert {"ix_conjunction_events_event_time_utc", "ix_conjunction_events_primary_norad_id"} <= set(idx)
    assert idx["uq_conjunction_events_message_id"]["unique"]
    cols = {c["name"]: c for c in insp.get_columns("conjunction_events")}
    assert cols["collision_probability"]["nullable"] and cols["relative_velocity_kms"]["nullable"]
    assert not cols["collision_probability"]["default"]  # no DB-level default either
    assert not cols["relative_velocity_kms"]["default"]


def test_migrations_are_reversible_and_reproducible():
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    with get_engine().connect() as c:
        assert c.execute(text("SELECT to_regclass('conjunction_events')")).scalar() is None
        assert c.execute(text("SELECT 1 FROM pg_type WHERE typname='object_type'")).first() is None
    command.upgrade(cfg, "head")
    assert "conjunction_events" in inspect(get_engine()).get_table_names()


def test_database_rejects_out_of_range_values_even_if_app_is_bypassed():
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with get_engine().begin() as c:
            c.execute(text(
                "INSERT INTO conjunction_events (message_id,event_time_utc,primary_norad_id,primary_object_type,"
                "secondary_norad_id,secondary_object_type,miss_distance_km,collision_probability) "
                "VALUES ('bad',now(),1,'PAYLOAD',2,'PAYLOAD',1.0,7.0)"
            ))
