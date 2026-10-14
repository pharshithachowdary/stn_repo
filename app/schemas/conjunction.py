"""Pydantic V2 contracts.

IMPORTANT: ``CDMIn`` validates and carries the payload *exactly as received*
(miss distance in METERS). Unit conversion deliberately does NOT happen here -
it lives at the database boundary in ``app.repositories.conjunction_repository``.
"""
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.conjunction_event import ObjectType

NoradId = Annotated[int, Field(strict=True, ge=1, le=999_999_999)]


class CDMIn(BaseModel):
    # extra="forbid": an unexpected key (e.g. someone sending `miss_distance_km`)
    # is a contract violation, not something to silently ignore. This is the main
    # defence against km-vs-m unit mix-ups.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message_id: Annotated[str, Field(min_length=1, max_length=64)]
    event_time_utc: AwareDatetime
    primary_norad_id: NoradId
    primary_object_type: ObjectType
    secondary_norad_id: NoradId
    secondary_object_type: ObjectType
    miss_distance_m: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]

    # Data honesty: optional AND defaulting to None. No placeholder values, ever.
    collision_probability: (
        Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None
    ) = None
    relative_velocity_kms: (
        Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)] | None
    ) = None

    @field_validator("primary_object_type", "secondary_object_type", mode="before")
    @classmethod
    def _normalise_object_type(cls, v: Any) -> Any:
        # Feeds disagree on "ROCKET BODY" vs "rocket_body"; normalise spelling only.
        if isinstance(v, str):
            return v.strip().upper().replace(" ", "_")
        return v

    @field_validator("event_time_utc")
    @classmethod
    def _to_utc(cls, v: datetime) -> datetime:
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _distinct_objects(self) -> "CDMIn":
        if self.primary_norad_id == self.secondary_norad_id:
            raise ValueError("primary_norad_id and secondary_norad_id must differ")
        return self


# ----------------------------------------------------------------------------
# Responses
# ----------------------------------------------------------------------------
class ConjunctionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    message_id: str
    event_time_utc: datetime
    primary_norad_id: int
    primary_object_type: ObjectType
    secondary_norad_id: int
    secondary_object_type: ObjectType
    miss_distance_km: float
    collision_probability: float | None
    relative_velocity_kms: float | None
    created_at: datetime


class ActiveConjunctionsPage(BaseModel):
    items: list[ConjunctionOut]
    total: int
    limit: int
    offset: int


class RejectedRecord(BaseModel):
    index: int
    message_id: str | None
    errors: list[dict[str, Any]]


class IngestResponse(BaseModel):
    received: int
    persisted: int
    duplicates: int
    dropped_by_triage: int
    rejected: int
    persisted_message_ids: list[str]
    duplicate_message_ids: list[str]
    dropped_message_ids: list[str]
    rejected_records: list[RejectedRecord]


class WorkerStatus(BaseModel):
    enabled: bool
    running: bool
    last_run_utc: datetime | None = None
    last_outcome: str | None = None


class HealthResponse(BaseModel):
    status: str
    database: str
    worker: WorkerStatus
