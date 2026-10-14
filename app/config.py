"""Application settings, loaded from environment variables (12-factor style)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database -----------------------------------------------------------
    database_url: str = "postgresql+psycopg2://stn:stn@localhost:5432/stn"

    # API pool: sized for the FastAPI thread pool.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 5
    db_pool_recycle_seconds: int = 1800
    db_connect_timeout_seconds: int = 3
    # Hard ceiling for any single API query; protects latency under load.
    db_api_statement_timeout_ms: int = 5000

    # Worker gets its OWN small pool so it can never starve the API of connections.
    worker_db_pool_size: int = 2
    worker_db_max_overflow: int = 0
    worker_db_statement_timeout_ms: int = 30000

    # --- Background ingestion worker ---------------------------------------
    worker_enabled: bool = True
    worker_interval_seconds: int = 30
    worker_batch_min: int = 5
    worker_batch_max: int = 10
    # Load-testing knob: simulates a slow/CPU-heavy screening step inside the
    # worker thread so you can prove the API stays responsive. 0 in production.
    worker_simulated_latency_seconds: float = 0.0

    # --- API ----------------------------------------------------------------
    max_ingest_batch_size: int = 1000
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
