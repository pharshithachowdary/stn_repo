"""Background ingestion worker.

Runs on an APScheduler ``BackgroundScheduler``: jobs execute on a dedicated
thread, entirely outside the asyncio event loop that serves HTTP, and use a
dedicated DB connection pool. See README ("Concurrency") for the full story.
"""
import logging
import random
import time
from datetime import datetime, timezone
from typing import Callable

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.schemas.conjunction import WorkerStatus
from app.services.ingestion import IngestResult, ingest_raw_records
from app.services.mock_generator import generate_mock_batch

logger = logging.getLogger(__name__)

# Arbitrary app-wide constant for pg_try_advisory_xact_lock ("STN" + 01).
INGEST_ADVISORY_LOCK_KEY = 0x53544E01
JOB_ID = "mock-cdm-ingestion"


class IngestionWorker:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        batch_factory: Callable[..., list] = generate_mock_batch,
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._batch_factory = batch_factory
        self._rng = rng
        self._scheduler: BackgroundScheduler | None = None
        self._last_run_utc: datetime | None = None
        self._last_outcome: str | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._scheduler is not None:
            return
        self._scheduler = BackgroundScheduler(
            executors={"default": ThreadPoolExecutor(max_workers=1)},
            job_defaults={
                "coalesce": True,  # if ticks were missed, run once - not N times
                "max_instances": 1,  # a slow batch is never overlapped by the next tick
                "misfire_grace_time": 15,
            },
            timezone=timezone.utc,
        )
        self._scheduler.add_job(
            self.run_once,
            IntervalTrigger(seconds=self._settings.worker_interval_seconds),
            id=JOB_ID,
            next_run_time=datetime.now(timezone.utc),  # first batch immediately
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("worker_started interval=%ss", self._settings.worker_interval_seconds)

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=True)
            self._scheduler = None
            logger.info("worker_stopped")

    def status(self) -> WorkerStatus:
        return WorkerStatus(
            enabled=self._settings.worker_enabled,
            running=bool(self._scheduler and self._scheduler.running),
            last_run_utc=self._last_run_utc,
            last_outcome=self._last_outcome,
        )

    # -- the job -----------------------------------------------------------
    def run_once(self) -> IngestResult | None:
        """One ingestion tick. Never raises: a failed tick must not kill the scheduler."""
        started = time.perf_counter()
        try:
            with self._session_factory() as session:
                # Cross-process guard: if another replica is mid-batch, skip this tick.
                # The xact-level lock is released automatically on commit/rollback.
                got_lock = session.execute(
                    text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": INGEST_ADVISORY_LOCK_KEY}
                ).scalar_one()
                if not got_lock:
                    self._record("skipped_lock_held")
                    logger.info("worker_skip reason=another_runner_active")
                    return None

                s = self._settings
                batch = self._batch_factory(
                    self._rng, min_size=s.worker_batch_min, max_size=s.worker_batch_max
                )
                if s.worker_simulated_latency_seconds > 0:
                    # Stand-in for heavy screening work. Blocks THIS worker thread only.
                    time.sleep(s.worker_simulated_latency_seconds)

                result = ingest_raw_records(session, batch, source="worker")
            self._record("ok")
            logger.info("worker_tick_ok elapsed_ms=%.1f", (time.perf_counter() - started) * 1000)
            return result
        except Exception:
            self._record("error")
            logger.exception("worker_tick_failed")
            return None

    def _record(self, outcome: str) -> None:
        self._last_run_utc = datetime.now(timezone.utc)
        self._last_outcome = outcome
