import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.api.v1 import conjunctions
from app.config import get_settings
from app.db.session import dispose_engines, get_worker_session_factory
from app.logging_config import configure_logging
from app.worker.runner import IngestionWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    worker = IngestionWorker(settings, get_worker_session_factory())
    app.state.worker = worker
    if settings.worker_enabled:
        worker.start()  # spawns its own thread; returns immediately
    else:
        logger.info("worker_disabled")
    try:
        yield
    finally:
        worker.stop()
        dispose_engines()


def create_app() -> FastAPI:
    app = FastAPI(
        title="STN Orbital Conjunction Ingestion & Screening Service",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(conjunctions.router)
    return app


app = create_app()
