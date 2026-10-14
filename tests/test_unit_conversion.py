"""4. Correct unit transformation: miss_distance_m (payload) -> miss_distance_km (storage)."""
import pytest

from app.repositories.conjunction_repository import meters_to_kilometers, to_row
from app.schemas.conjunction import CDMIn
from tests.conftest import fetch_row, make_cdm

URL = "/api/v1/conjunctions/ingest"


@pytest.mark.parametrize(
    "meters, km", [(450.2, 0.4502), (0, 0.0), (1000, 1.0), (1, 0.001), (123_456.7, 123.4567)]
)
def test_stored_value_is_meters_divided_by_1000(client, meters, km):
    assert client.post(URL, json=make_cdm(miss_distance_m=meters)).status_code == 201
    row = fetch_row("CDM-2026-X992")
    assert row["miss_distance_km"] == pytest.approx(km, rel=1e-12, abs=1e-12)
    assert "miss_distance_m" not in row  # there is no meters column at all


def test_conversion_happens_at_db_boundary_not_in_schema():
    cdm = CDMIn.model_validate(make_cdm(miss_distance_m=450.2))
    # The validated model still speaks METERS, exactly as received...
    assert cdm.miss_distance_m == 450.2
    assert not hasattr(cdm, "miss_distance_km")
    # ...and the mapper that builds the DB row is where kilometres appear.
    row = to_row(cdm, "api")
    assert row["miss_distance_km"] == pytest.approx(0.4502)
    assert "miss_distance_m" not in row


def test_meters_to_kilometers_helper():
    assert meters_to_kilometers(2500) == 2.5


def test_api_exposes_kilometres(client):
    client.post(URL, json=make_cdm(miss_distance_m=2500))
    item = client.get("/api/v1/conjunctions/active").json()["items"][0]
    assert item["miss_distance_km"] == 2.5
    assert "miss_distance_m" not in item


def test_min_miss_distance_km_filter_uses_kilometres(client):
    client.post(URL, json=make_cdm(message_id="NEAR", miss_distance_m=500))  # 0.5 km
    client.post(URL, json=make_cdm(message_id="FAR", miss_distance_m=5000, primary_norad_id=9))  # 5 km
    got = client.get("/api/v1/conjunctions/active", params={"min_miss_distance_km": 1}).json()
    assert [i["message_id"] for i in got["items"]] == ["FAR"]
