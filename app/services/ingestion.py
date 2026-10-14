"""Ingestion pipeline shared by the REST API and the background worker.

    raw JSON -> validate (per record) -> Triage Gate -> persist (DB boundary) -> commit
"""
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import ObjectType
from app.repositories import conjunction_repository as repo
from app.schemas.conjunction import CDMIn

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    received: int = 0
    persisted_ids: list[str] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)
    dropped_ids: list[str] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)

    @property
    def persisted(self) -> int:
        return len(self.persisted_ids)


def should_drop_by_triage(cdm: CDMIn) -> bool:
    """Triage Gate: debris-on-debris conjunctions are not worth any DB work."""
    return cdm.primary_object_type is ObjectType.DEBRIS and (
        cdm.secondary_object_type is ObjectType.DEBRIS
    )


def ingest_raw_records(db: Session, raw_items: list[Any], *, source: str) -> IngestResult:
    """Validate, triage and persist a batch. One bad record never poisons the rest."""
    result = IngestResult(received=len(raw_items))
    to_persist: list[CDMIn] = []

    for index, item in enumerate(raw_items):
        try:
            cdm = CDMIn.model_validate(item)
        except ValidationError as exc:
            message_id = item.get("message_id") if isinstance(item, dict) else None
            result.rejected.append(
                {
                    "index": index,
                    "message_id": message_id if isinstance(message_id, str) else None,
                    "errors": exc.errors(include_url=False, include_context=False, include_input=False),
                }
            )
            logger.warning("ingest_reject source=%s index=%d message_id=%s", source, index, message_id)
            continue

        if should_drop_by_triage(cdm):
            # Dropped BEFORE persistence: no insert, no DB round-trip for this record.
            result.dropped_ids.append(cdm.message_id)
            logger.info(
                "triage_drop source=%s message_id=%s reason=debris_vs_debris primary=%d secondary=%d",
                source,
                cdm.message_id,
                cdm.primary_norad_id,
                cdm.secondary_norad_id,
            )
            continue

        to_persist.append(cdm)

    inserted_ids = repo.insert_events(db, to_persist, source)
    db.commit()

    seen: set[str] = set()
    for cdm in to_persist:
        if cdm.message_id in inserted_ids and cdm.message_id not in seen:
            result.persisted_ids.append(cdm.message_id)
            seen.add(cdm.message_id)
        else:  # already in DB, or repeated inside this very batch
            result.duplicate_ids.append(cdm.message_id)

    logger.info(
        "ingest_done source=%s received=%d persisted=%d duplicates=%d dropped=%d rejected=%d",
        source,
        result.received,
        result.persisted,
        len(result.duplicate_ids),
        len(result.dropped_ids),
        len(result.rejected),
    )
    return result
