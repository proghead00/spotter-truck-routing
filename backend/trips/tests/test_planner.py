from datetime import UTC, datetime, timedelta

import pytest

from trips import planner
from trips.geo import Line, haversine_mi
from trips.providers import FakeRouter, Forecast, RawRoute
from trips.risk import Risk

CHICAGO = {"label": "Chicago", "lat": 41.8781, "lon": -87.6298}
ST_LOUIS = {"label": "St. Louis", "lat": 38.6270, "lon": -90.1994}
H = 3600


@pytest.mark.parametrize(
    "length, interval, expected",
    [
        (100, 25, [0, 25, 50, 75, 100]),
        (110, 25, [0, 25, 50, 75, 100, 110]),
        (100.3, 25, [0, 25, 50, 75, 100.3]),  # tiny remainder folds into the last checkpoint
        (8, 10, [0, 8]),
        (120, 50, [0, 50, 100, 120]),
    ],
)
def test_checkpoint_miles(length, interval, expected):
    assert planner.checkpoint_miles(length, interval) == pytest.approx(expected)


def test_owned_spans_cover_route_exactly_once():
    miles = [0, 25, 50, 60]
    spans = planner.owned_spans(miles)
    assert spans == [(0, 12.5), (12.5, 37.5), (37.5, 55), (55, 60)]
    assert sum(b - a for a, b in spans) == 60


@pytest.mark.parametrize(
    "drive_h, elapsed_h",
    [
        (0, 0),
        (5, 5),
        (8, 8),  # break is taken only before driving past 8 h
        (9, 9.5),
        (11, 11.5),  # arriving at the 11 h limit needs no rest
        (12, 11 + 0.5 + 10 + 1),
        (30, 2 * 21.5 + 8),
        (31, 2 * 21.5 + 9.5),
    ],
)
def test_hos_elapsed(drive_h, elapsed_h):
    assert planner.hos_elapsed(drive_h * H) == pytest.approx(elapsed_h * H)


def test_hos_stops():
    assert planner.hos_stops(7 * H) == []
    assert planner.hos_stops(10 * H) == [(8 * H, "break", 1800)]
    assert planner.hos_stops(11 * H) == [(8 * H, "break", 1800)]
    assert planner.hos_stops(20 * H) == [(8 * H, "break", 1800), (11 * H, "rest", 36000), (19 * H, "break", 1800)]


def test_forecast_at_interpolates_wind_and_buckets_precip():
    t0 = 1_800_000_000 // H * H
    f = Forecast(
        times=[t0, t0 + H, t0 + 2 * H],
        wind_mph=[10, 20, 30],
        gust_mph=[15, 25, 35],
        rain_in=[0.0, 0.3, 0.6],  # 0.3 fell during [t0, t0+1h], 0.6 during [t0+1h, t0+2h]
        snow_in=[0, 0, 0],
    )
    wind, gust, rain, _ = f.at(t0 + H / 2)
    assert (wind, gust, rain) == (15, 20, 0.3)
    assert f.at(t0 + 1.5 * H)[2] == 0.6
    assert f.at(t0 + 99 * H)[2] == 0.6  # clamps past the end


def test_truck_seconds_caps_speed():
    line = Line([(0.0, 0.0), (0.0, 1.0)])  # ~69 mi
    fast = RawRoute(line.coords, [1800], 1800, line.length_mi, "")  # car does it in 30 min
    assert planner.truck_seconds(fast, line)[-1] == pytest.approx(line.length_mi / 65 * H)
    slow = RawRoute(line.coords, [2 * H], 2 * H, line.length_mi, "")
    assert planner.truck_seconds(slow, line)[-1] == 2 * H
    no_annotations = RawRoute(line.coords, None, 2 * H, line.length_mi, "")
    assert planner.truck_seconds(no_annotations, line)[-1] == 2 * H


def _summary(no_travel=0, severe=0, high=0, avg=0.0, minutes=60):
    return {
        "miles_by_level": {"low": 0, "moderate": 0, "high": high, "severe": severe, "no_travel": no_travel},
        "avg_risk": avg,
        "duration_s": minutes * 60,
    }


@pytest.mark.parametrize(
    "a, b, winner, reason",
    [
        (_summary(severe=10, minutes=60), _summary(severe=0, high=90, minutes=300), "B", "Fewest Severe miles"),
        (_summary(high=10), _summary(high=5, avg=2.0, minutes=90), "B", "Fewest High miles"),
        (_summary(avg=0.8), _summary(avg=0.3, minutes=120), "B", "Lowest average risk"),
        (_summary(minutes=60), _summary(minutes=55), "B", "Shortest travel time"),
        (_summary(no_travel=1), _summary(severe=200), "B", "Fewest No Travel miles"),
        (_summary(severe=0.3), _summary(severe=0.2, minutes=90), "A", "Shortest travel time"),  # sub-mile noise
    ],
)
def test_recommendation_follows_priority_order(a, b, winner, reason):
    routes = [{"id": "A", "summary": a}, {"id": "B", "summary": b}]
    rec = planner.recommend(routes)
    assert rec["route_id"] == winner
    assert rec["reason"].startswith(reason)
    assert [r["recommended"] for r in routes].count(True) == 1


def test_recommendation_warns_when_every_route_is_unsafe():
    routes = [{"id": "A", "summary": _summary(no_travel=5)}, {"id": "B", "summary": _summary(no_travel=9)}]
    assert "No Travel" in planner.recommend(routes)["warning"]


class OneRouteRouter(FakeRouter):
    """Router that never returns alternatives, so detours must be forced."""

    def __init__(self):
        self.calls = []

    def route(self, waypoints, alternatives=0):
        self.calls.append(waypoints)
        if len(waypoints) == 2:
            return [self._arc(waypoints[0], waypoints[1], 0.0, "direct")]
        a, via, b = waypoints
        first, second = self._arc(a, via, 0, "x"), self._arc(via, b, 0, "y")
        coords = first.coords + second.coords[1:]
        return [RawRoute(coords, None, first.duration_s + second.duration_s, 0, f"via {via[0]:.2f}")]


def test_find_routes_forces_distinct_detours():
    router = OneRouteRouter()
    routes = planner.find_routes(router, (41.88, -87.63), (38.63, -90.20))
    assert len(routes) == 3
    assert len(router.calls) > 1
    for i, r in enumerate(routes):
        for other in routes[i + 1 :]:
            assert planner._overlap(r, other) < 0.85


def test_find_routes_raises_when_unroutable():
    class Nothing:
        def route(self, waypoints, alternatives=0):
            return []

    with pytest.raises(planner.NoRouteError):
        planner.find_routes(Nothing(), (0, 0), (1, 1))


@pytest.fixture
def plan():
    departure = datetime.now(UTC) + timedelta(minutes=5)
    return planner.plan_trip(CHICAGO, ST_LOUIS, departure, 35_000, 25)


def test_plan_trip_end_to_end_with_fake_providers(plan):
    assert [r["id"] for r in plan["routes"]] == ["A", "B", "C"]
    departure = datetime.fromisoformat(plan["departure"])
    for route in plan["routes"]:
        cps = route["checkpoints"]
        assert cps[0]["mile"] == 0
        assert cps[-1]["mile"] == pytest.approx(route["distance_mi"], abs=0.1)
        assert all(b["mile"] - a["mile"] <= 25.01 for a, b in zip(cps, cps[1:]))
        etas = [datetime.fromisoformat(c["eta"]) for c in cps]
        assert etas[0] == departure and etas == sorted(etas)
        assert sum(route["summary"]["miles_by_level"].values()) == pytest.approx(route["distance_mi"], abs=0.3)
        assert route["summary"]["max_level"] == max(c["level"] for c in cps)
        assert route["spans"][0]["start_mi"] == 0
        assert route["spans"][-1]["end_mi"] == pytest.approx(route["distance_mi"], abs=0.1)
        assert route["track"][0][2] == 0 and route["track"][-1][2] == route["duration_s"]
        start, end = route["checkpoints"][0], route["checkpoints"][-1]
        assert haversine_mi((start["lat"], start["lon"]), (CHICAGO["lat"], CHICAGO["lon"])) < 1
        assert haversine_mi((end["lat"], end["lon"]), (ST_LOUIS["lat"], ST_LOUIS["lon"])) < 1


def test_plan_trip_recommends_safest_route(plan):
    # The fake storm sits on the direct line, so the recommendation must not be the fastest route.
    keys = {r["id"]: planner.rank_key(r["summary"]) for r in plan["routes"]}
    rec = plan["recommendation"]["route_id"]
    assert keys[rec] == min(keys.values())
    fastest = min(plan["routes"], key=lambda r: r["duration_s"])["id"]
    assert rec != fastest
    assert any(c["level"] >= Risk.HIGH for r in plan["routes"] for c in r["checkpoints"])


def test_plan_trip_heatmap_shape(plan):
    heat = plan["heatmap"]
    assert len(heat["times"]) == 49
    assert heat["times"][1] - heat["times"][0] == 3600
    n = len(heat["cells"])
    assert 0 < n <= 200
    assert all(0 <= c < heat["cols"] * heat["rows"] for c in heat["cells"])
    for key in ("risk", "wind", "rain", "snow"):
        assert len(heat[key]) == 49 and all(len(frame) == n for frame in heat[key])
    (south, west), (north, east) = heat["bounds"]
    assert south < ST_LOUIS["lat"] < north and west < ST_LOUIS["lon"] < east


def test_long_trip_includes_hos_stops():
    denver = {"label": "Denver", "lat": 39.7392, "lon": -104.9903}
    plan = planner.plan_trip(CHICAGO, denver, datetime.now(UTC), 20_000, 50)
    route = plan["routes"][0]
    assert [s["kind"] for s in route["stops"]][:2] == ["break", "rest"]
    assert route["duration_s"] > route["summary"]["drive_s"] + 10 * H
    times = [t for *_, t in route["track"]]
    assert times == sorted(times)
    no_hos = planner.plan_trip(CHICAGO, denver, datetime.now(UTC), 20_000, 50, hos=False)
    assert no_hos["routes"][0]["stops"] == []
    assert no_hos["routes"][0]["duration_s"] == no_hos["routes"][0]["summary"]["drive_s"]


def test_heatmap_failure_still_returns_routes(monkeypatch):
    from trips.providers import FakeWeather, ProviderError

    real = FakeWeather.hourly
    calls = []

    def flaky(self, points, start, end, max_locations=None):
        calls.append(len(points))
        if len(calls) == 2:  # second call is the heatmap
            raise ProviderError("api.open-meteo.com is rate-limiting requests")
        return real(self, points, start, end, max_locations)

    monkeypatch.setattr(FakeWeather, "hourly", flaky)
    plan = planner.plan_trip(CHICAGO, ST_LOUIS, datetime.now(UTC), 35_000, 25)
    assert plan["heatmap"] is None
    assert "heatmap is unavailable" in plan["notices"][0]
    assert len(plan["routes"]) == 3 and plan["recommendation"]["route_id"]


def test_plan_without_problems_has_no_notices(plan):
    assert plan["notices"] == []
