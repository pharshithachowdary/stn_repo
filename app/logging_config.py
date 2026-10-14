import logging


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
    )
    # APScheduler logs every job execution at INFO; keep our own logs readable.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
