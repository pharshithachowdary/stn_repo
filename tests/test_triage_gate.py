"""2. The Triage Gate drops debris-on-debris events before persistence."""
import logging

from app.repositories import conjunction_repository as repo
from tests.conftest import count_rows, fetch_row, make_cdm

URL = "/api/v1/conjunctions/ingest"


def test_debris_on_debris_is_dropped_and_not_persisted(client):
    r = client.post(
        URL,
        json=make_cdm(message_id="DD-1", primary_object_type="DEBRIS", secondary_object_type="DEBRIS"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dropped_by_triage"] == 1 and body["persisted"] == 0
    assert body["dropped_message_ids"] == ["DD-1"]
    assert fetch_row("DD-1") is None
    assert count_rows() == 0


def test_drop_is_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.services.ingestion"):
        client.post(
            URL,
            json=make_cdm(message_id="DD-LOG", primary_object_type="DEBRIS", secondary_object_type="DEBRIS"),
        )
    assert any("triage_drop" in m and "DD-LOG" in m for m in caplog.messages)


def test_dropped_event_never_reaches_the_persistence_layer(client, monkeypatch):
    calls = []
    real = repo.insert_events

    def spy(db, cdms, source):
        calls.append([c.message_id for c in cdms])
        return real(db, cdms, source)

    monkeypatch.setattr(repo, "insert_events", spy)
    client.post(
        URL,
        json=make_cdm(message_id="DD-SPY", primary_object_type="DEBRIS", secondary_object_type="DEBRIS"),
    )
    assert calls == [[]]  # repository was handed nothing to write


def test_only_debris_vs_debris_is_dropped(client):
    combos = [
        ("PAYLOAD", "DEBRIS"),
        ("DEBRIS", "PAYLOAD"),
        ("PAYLOAD", "PAYLOAD"),
        ("DEBRIS", "ROCKET_BODY"),
        ("UNKNOWN", "DEBRIS"),
    ]
    batch = [
        make_cdm(message_id=f"C{i}", primary_object_type=a, secondary_object_type=b)
        for i, (a, b) in enumerate(combos)
    ]
    r = client.post(URL, json=batch)
    assert r.status_code == 201
    assert r.json()["persisted"] == len(combos) and r.json()["dropped_by_triage"] == 0


def test_debris_is_dropped_regardless_of_input_casing(client):
    r = client.post(
        URL, json=make_cdm(primary_object_type="debris", secondary_object_type=" Debris ")
    )
    assert r.json()["dropped_by_triage"] == 1 and count_rows() == 0


def test_mixed_batch_drops_only_the_debris_pairs(client):
    batch = [
        make_cdm(message_id="KEEP-1"),
        make_cdm(message_id="DROP-1", primary_object_type="DEBRIS", secondary_object_type="DEBRIS"),
        make_cdm(message_id="KEEP-2", primary_norad_id=7, secondary_norad_id=8),
        make_cdm(message_id="DROP-2", primary_object_type="DEBRIS", secondary_object_type="DEBRIS"),
    ]
    body = client.post(URL, json=batch).json()
    assert body["persisted"] == 2 and body["dropped_by_triage"] == 2
    assert sorted(body["dropped_message_ids"]) == ["DROP-1", "DROP-2"]
    assert count_rows() == 2
