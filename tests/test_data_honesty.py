"""3. Data honesty: None stays None and is NEVER fabricated."""
import random

from app.config import Settings
from app.db.session import get_worker_session_factory
from app.schemas.conjunction import CDMIn
from app.worker.runner import IngestionWorker
from tests.conftest import fetch_row, make_cdm

URL = "/api/v1/conjunctions/ingest"


def test_schema_defaults_for_optional_metrics_are_none():
    for name in ("collision_probability", "relative_velocity_kms"):
        assert CDMIn.model_fields[name].default is None
    cdm = CDMIn.model_validate(make_cdm(collision_probability=..., relative_velocity_kms=...))
    assert cdm.collision_probability is None and cdm.relative_velocity_kms is None


def test_omitted_fields_are_stored_as_null(client):
    r = client.post(URL, json=make_cdm(collision_probability=..., relative_velocity_kms=...))
    assert r.status_code == 201
    row = fetch_row("CDM-2026-X992")
    assert row["collision_probability"] is None  # not 0.0
    assert row["relative_velocity_kms"] is None  # not 7.5


def test_explicit_nulls_are_stored_as_null(client):
    r = client.post(URL, json=make_cdm(collision_probability=None, relative_velocity_kms=None))
    assert r.status_code == 201
    row = fetch_row("CDM-2026-X992")
    assert row["collision_probability"] is None and row["relative_velocity_kms"] is None


def test_each_field_is_independent(client):
    client.post(URL, json=make_cdm(message_id="A", collision_probability=..., relative_velocity_kms=9.9))
    client.post(URL, json=make_cdm(message_id="B", collision_probability=1e-5, relative_velocity_kms=...))
    a, b = fetch_row("A"), fetch_row("B")
    assert a["collision_probability"] is None and a["relative_velocity_kms"] == 9.9
    assert b["collision_probability"] == 1e-5 and b["relative_velocity_kms"] is None


def test_genuine_zero_is_not_confused_with_missing(client):
    """A real 0.0 is data; only absence is null. Guards against `value or None` bugs."""
    client.post(URL, json=make_cdm(collision_probability=0.0, relative_velocity_kms=0.0))
    row = fetch_row("CDM-2026-X992")
    assert row["collision_probability"] == 0.0
    assert row["relative_velocity_kms"] == 0.0


def test_api_returns_json_null_not_a_placeholder(client):
    client.post(URL, json=make_cdm(collision_probability=..., relative_velocity_kms=...))
    item = client.get("/api/v1/conjunctions/active").json()["items"][0]
    assert item["collision_probability"] is None
    assert item["relative_velocity_kms"] is None


def test_has_collision_prob_filter_distinguishes_null_from_zero(client):
    client.post(URL, json=make_cdm(message_id="NULLP", collision_probability=...))
    client.post(URL, json=make_cdm(message_id="ZEROP", collision_probability=0.0, primary_norad_id=3))
    with_p = client.get("/api/v1/conjunctions/active", params={"has_collision_prob": True}).json()
    without_p = client.get("/api/v1/conjunctions/active", params={"has_collision_prob": False}).json()
    assert [i["message_id"] for i in with_p["items"]] == ["ZEROP"]
    assert [i["message_id"] for i in without_p["items"]] == ["NULLP"]


def test_worker_path_never_fabricates_metrics(client):
    """Records with omitted/null metrics must land as NULL via the worker path too."""

    def batch(rng, **_):
        return [make_cdm(message_id="W-NULL", collision_probability=..., relative_velocity_kms=None)]

    worker = IngestionWorker(
        Settings(), get_worker_session_factory(), batch_factory=batch, rng=random.Random(1)
    )
    worker.run_once()
    row = fetch_row("W-NULL")
    assert row["collision_probability"] is None and row["relative_velocity_kms"] is None
    assert row["ingest_source"] == "worker"
