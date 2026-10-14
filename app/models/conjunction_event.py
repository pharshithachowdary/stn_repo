import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ObjectType(str, enum.Enum):
    """CCSDS CDM ``OBJECT_TYPE`` values. Names == values so the PG enum is readable."""

    PAYLOAD = "PAYLOAD"
    ROCKET_BODY = "ROCKET_BODY"
    DEBRIS = "DEBRIS"
    UNKNOWN = "UNKNOWN"
    OTHER = "OTHER"


object_type_enum = SAEnum(ObjectType, name="object_type", native_enum=True)


class ConjunctionEvent(Base):
    __tablename__ = "conjunction_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    primary_norad_id: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_object_type: Mapped[ObjectType] = mapped_column(object_type_enum, nullable=False)
    secondary_norad_id: Mapped[int] = mapped_column(Integer, nullable=False)
    secondary_object_type: Mapped[ObjectType] = mapped_column(object_type_enum, nullable=False)

    # Stored in KILOMETERS (converted from the payload's meters at the DB boundary).
    miss_distance_km: Mapped[float] = mapped_column(Double, nullable=False)
    # NULL means "upstream did not provide it". Never defaulted, never estimated.
    collision_probability: Mapped[float | None] = mapped_column(Double, nullable=True)
    relative_velocity_kms: Mapped[float | None] = mapped_column(Double, nullable=True)

    ingest_source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="api")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # message_id uniqueness doubles as the idempotency key for re-delivered feeds.
        Index("uq_conjunction_events_message_id", "message_id", unique=True),
        Index("ix_conjunction_events_event_time_utc", "event_time_utc"),
        Index("ix_conjunction_events_primary_norad_id", "primary_norad_id"),
        Index("ix_conjunction_events_secondary_norad_id", "secondary_norad_id"),
        CheckConstraint("miss_distance_km >= 0", name="ck_conjunction_events_miss_distance_nonneg"),
        # NULL passes CHECK constraints, so these never force a value for missing data.
        CheckConstraint(
            "collision_probability >= 0 AND collision_probability <= 1",
            name="ck_conjunction_events_collision_probability_range",
        ),
        CheckConstraint(
            "relative_velocity_kms >= 0", name="ck_conjunction_events_relative_velocity_nonneg"
        ),
        CheckConstraint(
            "primary_norad_id <> secondary_norad_id", name="ck_conjunction_events_distinct_objects"
        ),
    )
