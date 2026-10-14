"""1. Successful ingestion and database persistence (+ strict-contract behaviour)."""
from tests.conftest import count_rows, fetch_row, make_cdm

URL = "/api/v1/conjunctions/ingest"


def test_single_payload_is_persisted(client):
    r = client.post(URL, json=make_cdm())
    assert r.status_code == 201
    body = r.json()
    assert body["received"] == 1 and body["persisted"] == 1
    assert body["persisted_message_ids"] == ["CDM-2026-X992"]

    row = fetch_row("CDM-2026-X992")
    assert row is not None
    assert row["primary_norad_id"] == 25544
    assert row["secondary_norad_id"] == 41235
    assert row["primary_object_type"] == "PAYLOAD"
    assert row["secondary_object_type"] == "DEBRIS"
    assert row["collision_probability"] == 0.00012
    assert row["relative_velocity_kms"] == 11.4
    assert row["ingest_source"] == "api"
    assert row["event_time_utc"].utcoffset().total_seconds() == 0


def test_batch_payload_is_persisted(client):
    batch = [make_cdm(message_id=f"CDM-B-{i}", primary_norad_id=1000 + i) for i in range(5)]
    r = client.post(URL, json=batch)
    assert r.status_code == 201
    assert r.json()["persisted"] == 5
    assert count_rows() == 5


def test_redelivered_message_id_is_idempotent(client):
    assert client.post(URL, json=make_cdm()).status_code == 201
    r = client.post(URL, json=make_cdm(miss_distance_m=999.0))
    assert r.status_code == 200
    assert r.json()["duplicates"] == 1 and r.json()["persisted"] == 0
    assert count_rows() == 1
    assert fetch_row("CDM-2026-X992")["miss_distance_km"] == 450.2 / 1000  # first write wins


def test_duplicate_inside_one_batch_counts_once(client):
    r = client.post(URL, json=[make_cdm(), make_cdm()])
    assert r.json()["persisted"] == 1 and r.json()["duplicates"] == 1
    assert count_rows() == 1


def test_mixed_batch_returns_207_and_keeps_good_records(client):
    good = make_cdm(message_id="GOOD")
    bad = make_cdm(message_id="BAD", miss_distance_m="not-a-number")
    r = client.post(URL, json=[bad, good])
    assert r.status_code == 207
    body = r.json()
    assert body["persisted"] == 1 and body["rejected"] == 1
    assert body["rejected_records"][0]["index"] == 0
    assert body["rejected_records"][0]["message_id"] == "BAD"
    assert fetch_row("GOOD") is not None and fetch_row("BAD") is None


def test_fully_invalid_request_is_422_and_persists_nothing(client):
    r = client.post(URL, json=make_cdm(primary_object_type="SPACESHIP"))
    assert r.status_code == 422
    assert count_rows() == 0


def test_unit_mismatch_field_is_rejected_not_ignored(client):
    r = client.post(URL, json=make_cdm(miss_distance_km=0.45))
    assert r.status_code == 422
    assert r.json()["rejected_records"][0]["errors"][0]["type"] == "extra_forbidden"


def test_missing_required_field_rejected(client):
    r = client.post(URL, json=make_cdm(miss_distance_m=...))
    assert r.status_code == 422 and count_rows() == 0


def test_naive_timestamp_rejected(client):
    r = client.post(URL, json=make_cdm(event_time_utc="2030-01-01T00:00:00"))
    assert r.status_code == 422


def test_out_of_range_probability_and_negative_distance_rejected(client):
    assert client.post(URL, json=make_cdm(collision_probability=1.5)).status_code == 422
    assert client.post(URL, json=make_cdm(miss_distance_m=-1)).status_code == 422


def test_same_object_on_both_sides_rejected(client):
    r = client.post(URL, json=make_cdm(secondary_norad_id=25544))
    assert r.status_code == 422


def test_object_type_spelling_is_normalised(client):
    r = client.post(URL, json=make_cdm(primary_object_type="rocket body"))
    assert r.status_code == 201
    assert fetch_row("CDM-2026-X992")["primary_object_type"] == "ROCKET_BODY"


def test_timezone_offsets_are_normalised_to_utc(client):
    r = client.post(URL, json=make_cdm(event_time_utc="2030-10-15T14:00:00+05:30"))
    assert r.status_code == 201
    ts = fetch_row("CDM-2026-X992")["event_time_utc"]
    assert (ts.year, ts.month, ts.day, ts.hour, ts.minute) == (2030, 10, 15, 8, 30)


def test_oversized_and_empty_batches(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_ingest_batch_size", 2)
    batch = [make_cdm(message_id=f"M{i}") for i in range(3)]
    assert client.post(URL, json=batch).status_code == 413
    assert client.post(URL, json=[]).status_code == 422
    assert count_rows() == 0


def test_body_must_be_object_or_list(client):
    assert client.post(URL, json="hello").status_code == 422
    assert client.post(URL, json=[1, "x"]).status_code == 422
