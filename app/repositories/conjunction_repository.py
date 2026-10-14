"""Persistence layer - the *database boundary*.

All unit conversion happens here (``to_row``), never in the Pydantic schemas.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import ConjunctionEvent
from app.schemas.conjunction import CDMIn

METERS_PER_KILOMETER = 1000.0


def meters_to_kilometers(meters: float) -> float:
    return meters / METERS_PER_KILOMETER


def to_row(cdm: CDMIn, source: str) -> dict[str, Any]:
    """Map a validated payload to a ``conjunction_events`` row.

    Optional metrics are passed through untouched: ``None`` stays ``None`` and a
    legitimate ``0.0`` stays ``0.0`` (we test ``is None``, never truthiness).
    """
    return {
        "message_id": cdm.message_id,
        "event_time_utc": cdm.event_time_utc,
        "primary_norad_id": cdm.primary_norad_id,
        "primary_object_type": cdm.primary_object_type,
        "secondary_norad_id": cdm.secondary_norad_id,
        "secondary_object_type": cdm.secondary_object_type,
        "miss_distance_km": meters_to_kilometers(cdm.miss_distance_m),  # <- the ONLY conversion
        "collision_probability": cdm.collision_probability,
        "relative_velocity_kms": cdm.relative_velocity_kms,
        "ingest_source": source,
    }


def insert_events(db: Session, cdms: list[CDMIn], source: str) -> set[str]:
    """Bulk-insert, skipping already-seen ``message_id`` values.

    Returns the set of message_ids that were actually inserted. Does not commit.
    """
    if not cdms:
        return set()
    stmt = (
        pg_insert(ConjunctionEvent)
        .values([to_row(c, source) for c in cdms])
        .on_conflict_do_nothing(index_elements=["message_id"])
        .returning(ConjunctionEvent.message_id)
    )
    return set(db.execute(stmt).scalars().all())


def _active_filters(
    now: datetime,
    min_miss_distance_km: float | None,
    norad_id: int | None,
    has_collision_prob: bool | None,
) -> list[Any]:
    conds: list[Any] = [ConjunctionEvent.event_time_utc > now]  # "active" == strictly in the future
    if min_miss_distance_km is not None:
        conds.append(ConjunctionEvent.miss_distance_km >= min_miss_distance_km)
    if norad_id is not None:
        conds.append(
            (ConjunctionEvent.primary_norad_id == norad_id)
            | (ConjunctionEvent.secondary_norad_id == norad_id)
        )
    if has_collision_prob is True:
        conds.append(ConjunctionEvent.collision_probability.is_not(None))
    elif has_collision_prob is False:
        conds.append(ConjunctionEvent.collision_probability.is_(None))
    return conds


def list_active(
    db: Session,
    *,
    now: datetime,
    min_miss_distance_km: float | None,
    norad_id: int | None,
    has_collision_prob: bool | None,
    limit: int,
    offset: int,
) -> tuple[list[ConjunctionEvent], int]:
    conds = _active_filters(now, min_miss_distance_km, norad_id, has_collision_prob)
    total = db.execute(
        select(func.count()).select_from(ConjunctionEvent).where(*conds)
    ).scalar_one()
    rows = (
        db.execute(
            select(ConjunctionEvent)
            .where(*conds)
            .order_by(ConjunctionEvent.event_time_utc, ConjunctionEvent.id)  # stable paging
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total
