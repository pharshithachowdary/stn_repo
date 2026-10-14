from tests.conftest import future_iso, make_cdm, past_iso

INGEST = "/api/v1/conjunctions/ingest"
ACTIVE = "/api/v1/conjunctions/active"


def _ids(resp):
    return [i["message_id"] for i in resp.json()["items"]]


def test_only_strictly_future_events_are_active(client):
    client.post(INGEST, json=[
        make_cdm(message_id="PAST", event_time_utc=past_iso(1)),
        make_cdm(message_id="FUTURE", event_time_utc=future_iso(1)),
    ])
    r = client.get(ACTIVE)
    assert r.status_code == 200
    assert _ids(r) == ["FUTURE"] and r.json()["total"] == 1


def test_results_ordered_by_event_time(client):
    client.post(INGEST, json=[
        make_cdm(message_id="C", event_time_utc=future_iso(30)),
        make_cdm(message_id="A", event_time_utc=future_iso(10)),
        make_cdm(message_id="B", event_time_utc=future_iso(20)),
    ])
    assert _ids(client.get(ACTIVE)) == ["A", "B", "C"]


def test_norad_filter_matches_primary_or_secondary(client):
    client.post(INGEST, json=[
        make_cdm(message_id="P", primary_norad_id=111, secondary_norad_id=222),
        make_cdm(message_id="S", primary_norad_id=333, secondary_norad_id=111),
        make_cdm(message_id="X", primary_norad_id=444, secondary_norad_id=555),
    ])
    assert sorted(_ids(client.get(ACTIVE, params={"norad_id": 111}))) == ["P", "S"]


def test_filters_combine_with_and(client):
    client.post(INGEST, json=[
        make_cdm(message_id="HIT", primary_norad_id=1, miss_distance_m=3000, collision_probability=1e-4),
        make_cdm(message_id="CLOSE", primary_norad_id=1, miss_distance_m=100, collision_probability=1e-4),
        make_cdm(message_id="NOPROB", primary_norad_id=1, miss_distance_m=3000, collision_probability=...),
    ])
    r = client.get(ACTIVE, params={"norad_id": 1, "min_miss_distance_km": 1, "has_collision_prob": True})
    assert _ids(r) == ["HIT"]


def test_limit_and_offset_paginate_without_gaps_or_overlap(client):
    client.post(INGEST, json=[
        make_cdm(message_id=f"E{i:02d}", event_time_utc=future_iso(10 + i)) for i in range(7)
    ])
    p1 = client.get(ACTIVE, params={"limit": 3, "offset": 0}).json()
    p2 = client.get(ACTIVE, params={"limit": 3, "offset": 3}).json()
    p3 = client.get(ACTIVE, params={"limit": 3, "offset": 6}).json()
    assert p1["total"] == p2["total"] == p3["total"] == 7
    got = [i["message_id"] for p in (p1, p2, p3) for i in p["items"]]
    assert got == [f"E{i:02d}" for i in range(7)]
    assert (p1["limit"], p1["offset"]) == (3, 0)


def test_pagination_bounds_are_enforced(client):
    assert client.get(ACTIVE, params={"limit": 0}).status_code == 422
    assert client.get(ACTIVE, params={"limit": 501}).status_code == 422
    assert client.get(ACTIVE, params={"offset": -1}).status_code == 422
    assert client.get(ACTIVE, params={"min_miss_distance_km": -1}).status_code == 422


def test_default_limit_is_applied(client):
    assert client.get(ACTIVE).json()["limit"] == 50


def test_empty_result(client):
    assert client.get(ACTIVE).json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
