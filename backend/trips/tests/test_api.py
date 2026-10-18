from datetime import UTC, datetime, timedelta

import pytest
import responses
from django.core.cache import cache
from rest_framework.test import APIClient

from trips import providers
from trips.geo import encode_polyline


@pytest.fixture
def client():
    cache.clear()
    return APIClient()


def body(**overrides):
    data = {
        "origin": "Chicago",
        "destination": {"label": "St. Louis", "lat": 38.627, "lon": -90.1994},
        "departure": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "load_lb": 42000,
        "interval_mi": 10,
    }
    return {**data, **overrides}


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_plan_happy_path(client):
    res = client.post("/api/plan", body(), format="json")
    assert res.status_code == 200, res.content
    data = res.json()
    assert data["origin"]["label"].startswith("Chicago")
    assert data["interval_mi"] == 10
    assert len(data["routes"]) == 3
    assert data["recommendation"]["route_id"] in {"A", "B", "C"}


def test_plan_hos_toggle(client):
    assert client.post("/api/plan", body(), format="json").json()["hos"] is True
    assert client.post("/api/plan", body(hos=False), format="json").json()["hos"] is False


def test_plan_accepts_coordinate_text(client):
    res = client.post("/api/plan", body(origin="41.8781, -87.6298"), format="json")
    assert res.status_code == 200
    assert res.json()["origin"]["lat"] == pytest.approx(41.8781)


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"interval_mi": 30}, "interval_mi"),
        ({"load_lb": -1}, "load_lb"),
        ({"load_lb": 90_000}, "load_lb"),
        ({"departure": (datetime.now(UTC) - timedelta(hours=3)).isoformat()}, "departure"),
        ({"departure": (datetime.now(UTC) + timedelta(days=9)).isoformat()}, "departure"),
        ({"departure": "tomorrow"}, "departure"),
        ({"origin": "Atlantis"}, "origin"),
        ({"origin": ""}, "origin"),
        ({"origin": {"lat": 120, "lon": 0}}, "origin"),
        ({"origin": {"label": "St. Louis", "lat": 38.627, "lon": -90.1994}}, "destination"),
    ],
)
def test_plan_validation(client, overrides, field):
    res = client.post("/api/plan", body(**overrides), format="json")
    assert res.status_code == 400
    assert field in res.json()


def test_plan_reports_upstream_failure_as_502(client, monkeypatch):
    def boom(*args, **kwargs):
        raise providers.ProviderError("api.open-meteo.com is rate-limiting requests")

    monkeypatch.setattr(providers.FakeWeather, "hourly", boom)
    res = client.post("/api/plan", body(), format="json")
    assert res.status_code == 502
    assert "rate-limiting" in res.json()["detail"]


def test_plan_reports_no_route_as_422(client, monkeypatch):
    monkeypatch.setattr(providers.FakeRouter, "route", lambda self, w, alternatives=0: [])
    res = client.post("/api/plan", body(), format="json")
    assert res.status_code == 422


def test_geocode(client):
    assert client.get("/api/geocode?q=d").json() == []
    places = client.get("/api/geocode?q=Den").json()
    assert places[0]["label"].startswith("Denver") and {"lat", "lon"} <= places[0].keys()


def test_plan_is_rate_limited(client, monkeypatch):
    from trips.views import PlanThrottle

    monkeypatch.setattr(PlanThrottle, "THROTTLE_RATES", {"plan": "1/min"})
    assert client.post("/api/plan", body(), format="json").status_code == 200
    assert client.post("/api/plan", body(), format="json").status_code == 429


# --------------------------------------------------------------------------- live provider parsing (HTTP mocked)


@responses.activate
def test_osrm_parsing():
    cache.clear()
    coords = [(41.0, -87.0), (41.0, -87.5), (41.2, -88.0)]
    responses.get(
        "https://router.project-osrm.org/route/v1/driving/-87.00000,41.00000;-88.00000,41.20000",
        json={
            "code": "Ok",
            "routes": [
                {
                    "geometry": encode_polyline(coords),
                    "distance": 80467.2,
                    "duration": 3000,
                    "legs": [{"summary": "I 80, I 80, US 30", "annotation": {"duration": [1000, 2000]}}],
                }
            ],
        },
    )
    (route,) = providers.OSRMRouter().route([coords[0], coords[-1]], alternatives=3)
    assert route.coords == coords
    assert route.seg_seconds == [1000, 2000]
    assert route.distance_mi == pytest.approx(50)
    assert route.name == "I 80, US 30"
    assert "alternatives=3" in responses.calls[0].request.url


@responses.activate
def test_osrm_no_route_and_errors():
    cache.clear()
    url = "https://router.project-osrm.org/route/v1/driving/0.00000,0.00000;1.00000,1.00000"
    responses.get(url, json={"code": "NoRoute"})
    assert providers.OSRMRouter().route([(0, 0), (1, 1)]) == []
    cache.clear()
    responses.replace(responses.GET, url, status=500)
    with pytest.raises(providers.ProviderError):
        providers.OSRMRouter().route([(0, 0), (1, 1)])


@responses.activate
def test_open_meteo_parsing_converts_units_and_dedupes():
    cache.clear()
    hourly = {
        "time": [1_800_000_000, 1_800_003_600],
        "wind_speed_10m": [30.0, None],
        "wind_gusts_10m": [40.0, 41.0],
        "rain": [2.54, 0.0],
        "showers": [2.54, 0.0],
        "snowfall": [2.54, 5.08],
    }
    responses.get("https://api.open-meteo.com/v1/forecast", json={"hourly": hourly})
    start = datetime(2027, 1, 15, 8, tzinfo=UTC)
    forecasts = providers.OpenMeteoWeather().hourly([(41.001, -87.001), (41.002, -87.002)], start, start)
    assert len(forecasts) == 2 and forecasts[0] is forecasts[1]  # snapped to one location, fetched once
    f = forecasts[0]
    assert f.wind_mph == [30.0, 0.0]
    assert f.rain_in == pytest.approx([0.2, 0.0])  # (2.54 + 2.54) mm
    assert f.snow_in == pytest.approx([1.0, 2.0])  # cm -> in
    url = responses.calls[0].request.url
    assert "wind_speed_unit=mph" in url and "start_hour=2027-01-15T08%3A00" in url
    assert len(responses.calls) == 1


@responses.activate
def test_open_meteo_rate_limit_is_reported():
    cache.clear()
    responses.get("https://api.open-meteo.com/v1/forecast", status=429)
    start = datetime(2027, 1, 15, 8, tzinfo=UTC)
    with pytest.raises(providers.ProviderError, match="rate-limiting"):
        providers.OpenMeteoWeather().hourly([(40, -90)], start, start)
    assert len(responses.calls) == 2  # retried once


@responses.activate
def test_photon_parsing():
    cache.clear()
    responses.get(
        "https://photon.komoot.io/api/",
        json={
            "features": [
                {
                    "geometry": {"coordinates": [-96.797, 32.7767]},
                    "properties": {"name": "Dallas", "city": "Dallas", "state": "Texas", "country": "United States"},
                }
            ]
        },
    )
    (place,) = providers.PhotonGeocoder().search("Dallas")
    assert place.label == "Dallas, Texas, United States"
    assert (place.lat, place.lon) == (32.7767, -96.797)
