"""Engine / session management.

Two independent engines (and therefore two connection pools) are used:

* the **API engine**   - used by request handlers through the ``get_db`` dependency;
* the **worker engine** - used only by the background ingestion worker.

Keeping them separate means a burst of background inserts can never exhaust the
connections that ``/healthz`` and ``/active`` need, which is a key part of
keeping API latency flat while ingestion runs.
"""
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _build_engine(
    *,
    pool_size: int,
    max_overflow: int,
    statement_timeout_ms: int,
    application_name: str,
) -> Engine:
    s = get_settings()
    return create_engine(
        s.database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=s.db_pool_timeout_seconds,
        pool_recycle=s.db_pool_recycle_seconds,
        pool_pre_ping=True,  # transparently discard connections killed by a DB restart
        connect_args={
            "connect_timeout": s.db_connect_timeout_seconds,
            "options": f"-c statement_timeout={statement_timeout_ms}",
            "application_name": application_name,
        },
    )


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    return _build_engine(
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        statement_timeout_ms=s.db_api_statement_timeout_ms,
        application_name="stn-api",
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@lru_cache
def get_worker_engine() -> Engine:
    s = get_settings()
    return _build_engine(
        pool_size=s.worker_db_pool_size,
        max_overflow=s.worker_db_max_overflow,
        statement_timeout_ms=s.worker_db_statement_timeout_ms,
        application_name="stn-worker",
    )


@lru_cache
def get_worker_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_worker_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always released to the pool."""
    db = get_session_factory()()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def dispose_engines() -> None:
    for factory in (get_engine, get_worker_engine):
        if factory.cache_info().currsize:
            factory().dispose()
