from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.repositories import conjunction_repository as repo
from app.schemas.conjunction import ActiveConjunctionsPage, ConjunctionOut, IngestResponse
from app.services.ingestion import ingest_raw_records

router = APIRouter(prefix="/api/v1/conjunctions", tags=["conjunctions"])

_EXAMPLE = {
    "message_id": "CDM-2026-X992",
    "event_time_utc": "2026-10-15T08:30:00Z",
    "primary_norad_id": 25544,
    "primary_object_type": "PAYLOAD",
    "secondary_norad_id": 41235,
    "secondary_object_type": "DEBRIS",
    "miss_distance_m": 450.2,
    "collision_probability": 0.00012,
    "relative_velocity_kms": 11.4,
}


# NOTE: plain `def` handlers run in FastAPI's threadpool, so validation and
# blocking DB I/O never run on (or block) the asyncio event loop.
@router.post(
    "/ingest",
    response_model=IngestResponse,
    summary="Ingest one CDM or a batch (JSON list) of CDMs",
    responses={
        201: {"description": "At least one record persisted, none rejected"},
        200: {"description": "Processed; nothing new persisted (dropped / duplicate)"},
        207: {"description": "Partial success: some records rejected, the rest processed"},
        413: {"description": "Batch exceeds MAX_INGEST_BATCH_SIZE"},
        422: {"description": "Every record in the request was invalid"},
    },
)
def ingest(
    response: Response,
    payload: Annotated[
        dict[str, Any] | list[Any],
        Body(openapi_examples={"single": {"value": _EXAMPLE}, "batch": {"value": [_EXAMPLE]}}),
    ],
    db: Session = Depends(get_db),
) -> IngestResponse:
    items = payload if isinstance(payload, list) else [payload]
    limit = get_settings().max_ingest_batch_size
    if len(items) > limit:
        raise HTTPException(
            413, f"Batch too large (max {limit} records)"
        )
    if not items:
        raise HTTPException(422, "Batch is empty")

    result = ingest_raw_records(db, items, source="api")

    if len(result.rejected) == result.received:
        response.status_code = 422
    elif result.rejected:
        response.status_code = 207
    elif result.persisted:
        response.status_code = 201
    else:
        response.status_code = 200

    return IngestResponse(
        received=result.received,
        persisted=result.persisted,
        duplicates=len(result.duplicate_ids),
        dropped_by_triage=len(result.dropped_ids),
        rejected=len(result.rejected),
        persisted_message_ids=result.persisted_ids,
        duplicate_message_ids=result.duplicate_ids,
        dropped_message_ids=result.dropped_ids,
        rejected_records=result.rejected,
    )


@router.get(
    "/active",
    response_model=ActiveConjunctionsPage,
    summary="List active (future) conjunctions",
)
def list_active(
    db: Session = Depends(get_db),
    min_miss_distance_km: Annotated[
        float | None, Query(ge=0, description="Only events with miss distance >= this (km)")
    ] = None,
    norad_id: Annotated[
        int | None, Query(ge=1, description="Match events where this object is primary OR secondary")
    ] = None,
    has_collision_prob: Annotated[
        bool | None,
        Query(description="true: only events WITH a collision probability; false: only WITHOUT"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ActiveConjunctionsPage:
    rows, total = repo.list_active(
        db,
        now=datetime.now(timezone.utc),
        min_miss_distance_km=min_miss_distance_km,
        norad_id=norad_id,
        has_collision_prob=has_collision_prob,
        limit=limit,
        offset=offset,
    )
    return ActiveConjunctionsPage(
        items=[ConjunctionOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
