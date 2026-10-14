"""Module C: background ingestion + API responsiveness."""
import random
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import text

from app.config import Settings
from app.db.session import get_engine, get_worker_session_factory
from app.services.mock_generator import generate_mock_batch
from app.worker.runner import INGEST_ADVISORY_LOCK_KEY, IngestionWorker
from tests.conftest import count_rows, make_cdm


def _worker(**settings_kw) -> IngestionWorker:
    return IngestionWorker(
        Settings(**settings_kw), get_worker_session_factory(), rng=random.Random(42)
    )


# --- mock generator ---------------------------------------------------------
def test_mock_generator_yields_5_to_10_records_and_all_are_valid():
    from app.schemas.conjunction import CDMIn

    now = datetime.now(timezone.utc)
    for seed in range(50):
        batch = generate_mock_batch(random.Random(seed), now=now)
        assert 5 <= len(batch) <= 10
        for rec in batch:
            cdm = CDMIn.model_validate(rec)
            assert cdm.event_time_utc > now  # mock events are "active"


def test_mock_generator_exercises_missing_metrics_and_debris():
    batch = [r for s in range(30) for r in generate_mock_batch(random.Random(s))]
    assert any("collision_probability" not in r for r in batch)  # omitted
    assert any(r.get("collision_probability", 1) is None for r in batch)  # explicit null
    assert any(
        r["primary_object_type"] == r["secondary_object_type"] == "DEBRIS" for r in batch
    )


# --- worker -----------------------------------------------------------------
def test_run_once_ingests_a_batch(client):
    result = _worker().run_once()
    assert result is not None
    assert 5 <= result.received <= 10
    accounted = result.persisted + len(result.duplicate_ids) + len(result.dropped_ids) + len(result.rejected)
    assert accounted == result.received
    assert result.rejected == []
    assert count_rows() == result.persisted
    with get_engine().connect() as c:
        sources = c.execute(text("SELECT DISTINCT ingest_source FROM conjunction_events")).scalars().all()
    assert set(sources) <= {"worker"}


def test_run_once_never_raises_even_if_the_db_work_fails():
    def boom(*_a, **_k):
        raise RuntimeError("upstream exploded")

    w = IngestionWorker(Settings(), get_worker_session_factory(), batch_factory=boom)
    assert w.run_once() is None
    assert w.status().last_outcome == "error"


def test_advisory_lock_prevents_overlapping_runners(client):
    holder = get_engine().connect()
    try:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": INGEST_ADVISORY_LOCK_KEY})
        w = _worker()
        assert w.run_once() is None  # skipped, did not block
        assert w.status().last_outcome == "skipped_lock_held"
        assert count_rows() == 0
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": INGEST_ADVISORY_LOCK_KEY})
        holder.close()


def test_scheduler_lifecycle_runs_a_job_and_stops_cleanly(client):
    w = IngestionWorker(
        Settings(worker_interval_seconds=1), get_worker_session_factory(), rng=random.Random(7)
    )
    w.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and w.status().last_outcome is None:
            time.sleep(0.05)
        assert w.status().running is True
        assert w.status().last_outcome == "ok"
    finally:
        w.stop()
    assert w.status().running is False
    assert count_rows() >= 0


# --- the key requirement: API stays < 100 ms during a slow ingestion --------
def test_api_stays_under_100ms_while_a_slow_batch_is_being_processed(client):
    client.post("/api/v1/conjunctions/ingest", json=[make_cdm(message_id=f"S{i}", primary_norad_id=i + 1) for i in range(20)])
    for path in ("/healthz", "/api/v1/conjunctions/active"):  # warm pools / code paths
        client.get(path)

    slow = _worker(worker_simulated_latency_seconds=2.0)
    t = threading.Thread(target=slow.run_once, name="slow-ingest")
    t.start()
    time.sleep(0.2)  # let it acquire its lock and start "processing"

    latencies: list[float] = []
    try:
        for _ in range(25):
            for path in ("/healthz", "/api/v1/conjunctions/active?limit=50"):
                t0 = time.perf_counter()
                r = client.get(path)
                latencies.append(time.perf_counter() - t0)
                assert r.status_code == 200
        still_running = t.is_alive()
    finally:
        t.join()

    assert still_running, "ingestion finished too early - the test did not overlap with it"
    assert max(latencies) < 0.100, f"slowest call {max(latencies)*1000:.1f}ms"
