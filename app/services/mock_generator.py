"""Internal mock data generator for the background worker (no external feed needed)."""
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

_OBJECT_TYPES = ["PAYLOAD", "DEBRIS", "ROCKET_BODY", "UNKNOWN"]
_OBJECT_WEIGHTS = [0.30, 0.45, 0.15, 0.10]  # debris-heavy, so the Triage Gate gets exercised


def _norad_pair(rng: random.Random) -> tuple[int, int]:
    a = rng.randint(10_000, 60_000)
    b = rng.randint(10_000, 60_000)
    while b == a:
        b = rng.randint(10_000, 60_000)
    return a, b


def generate_mock_record(rng: random.Random, now: datetime) -> dict[str, Any]:
    primary, secondary = _norad_pair(rng)
    event_time = now + timedelta(minutes=rng.randint(60, 7 * 24 * 60))
    record: dict[str, Any] = {
        "message_id": f"CDM-MOCK-{uuid.UUID(int=rng.getrandbits(128)).hex[:12].upper()}",
        "event_time_utc": event_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "primary_norad_id": primary,
        "primary_object_type": rng.choices(_OBJECT_TYPES, _OBJECT_WEIGHTS)[0],
        "secondary_norad_id": secondary,
        "secondary_object_type": rng.choices(_OBJECT_TYPES, _OBJECT_WEIGHTS)[0],
        "miss_distance_m": round(rng.uniform(5.0, 50_000.0), 1),
    }
    # Mimic flaky upstreams: the optional metrics are sometimes explicit null,
    # sometimes omitted entirely. We never invent a value for them.
    roll = rng.random()
    if roll < 0.55:
        record["collision_probability"] = float(f"{10 ** rng.uniform(-9, -3):.3e}")
    elif roll < 0.80:
        record["collision_probability"] = None
    roll = rng.random()
    if roll < 0.60:
        record["relative_velocity_kms"] = round(rng.uniform(0.1, 15.0), 2)
    elif roll < 0.85:
        record["relative_velocity_kms"] = None
    return record


def generate_mock_batch(
    rng: random.Random | None = None,
    *,
    min_size: int = 5,
    max_size: int = 10,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return a batch of ``min_size..max_size`` randomized mock CDM payloads."""
    rng = rng or random.Random()
    now = now or datetime.now(timezone.utc)
    size = rng.randint(min_size, max_size)
    return [generate_mock_record(rng, now) for _ in range(size)]
