"""create conjunction_events

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# create_type=False: the type is created/dropped explicitly below, so the
# migration is deterministic and reversible.
object_type = postgresql.ENUM(
    "PAYLOAD", "ROCKET_BODY", "DEBRIS", "UNKNOWN", "OTHER", name="object_type", create_type=False
)


def upgrade() -> None:
    object_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "conjunction_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("event_time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("primary_norad_id", sa.Integer(), nullable=False),
        sa.Column("primary_object_type", object_type, nullable=False),
        sa.Column("secondary_norad_id", sa.Integer(), nullable=False),
        sa.Column("secondary_object_type", object_type, nullable=False),
        sa.Column("miss_distance_km", sa.Double(), nullable=False),
        # Nullable on purpose: NULL == "not provided upstream". No defaults.
        sa.Column("collision_probability", sa.Double(), nullable=True),
        sa.Column("relative_velocity_kms", sa.Double(), nullable=True),
        sa.Column("ingest_source", sa.String(16), nullable=False, server_default="api"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("miss_distance_km >= 0", name="ck_conjunction_events_miss_distance_nonneg"),
        sa.CheckConstraint(
            "collision_probability >= 0 AND collision_probability <= 1",
            name="ck_conjunction_events_collision_probability_range",
        ),
        sa.CheckConstraint(
            "relative_velocity_kms >= 0", name="ck_conjunction_events_relative_velocity_nonneg"
        ),
        sa.CheckConstraint(
            "primary_norad_id <> secondary_norad_id", name="ck_conjunction_events_distinct_objects"
        ),
    )
    op.create_index("uq_conjunction_events_message_id", "conjunction_events", ["message_id"], unique=True)
    op.create_index("ix_conjunction_events_event_time_utc", "conjunction_events", ["event_time_utc"])
    op.create_index("ix_conjunction_events_primary_norad_id", "conjunction_events", ["primary_norad_id"])
    op.create_index(
        "ix_conjunction_events_secondary_norad_id", "conjunction_events", ["secondary_norad_id"]
    )


def downgrade() -> None:
    op.drop_table("conjunction_events")  # drops its indexes and constraints too
    object_type.drop(op.get_bind(), checkfirst=True)
