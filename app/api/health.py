import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.conjunction import HealthResponse, WorkerStatus

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
def healthz(request: Request, response: Response, db: Session = Depends(get_db)) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        db_status = "up"
    except SQLAlchemyError:
        logger.exception("healthz_db_check_failed")
        db_status = "down"
        response.status_code = 503

    worker = getattr(request.app.state, "worker", None)
    worker_status = worker.status() if worker else WorkerStatus(enabled=False, running=False)
    return HealthResponse(
        status="ok" if db_status == "up" else "degraded",
        database=db_status,
        worker=worker_status,
    )
