"""Third-party data: OSRM (routing), Open-Meteo (weather), Photon (geocoding).

Each provider has a deterministic `Fake*` twin used by tests and the end-to-end suite
(`PROVIDERS=fake`), so no test depends on the network.
"""

import hashlib
import json
import math
import time
from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, datetime

import requests
from django.conf import settings
from django.core.cache import cache

from .geo import METERS_PER_MILE, LatLon, bearing, decode_polyline, destination, haversine_mi, interpolate

TIMEOUT_S = 10
session = requests.Session()
session.headers["User-Agent"] = "spotter-weather-routing/1.0 (assessment demo)"


class ProviderError(Exception):
    """An upstream API failed or returned something unusable."""


def _get_json(url: str, params: dict, cache_s: int) -> dict | list:
    key = "http:" + hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()
    if (hit := cache.get(key)) is not None:
        return hit
    try:
        response = session.get(url, params=params, timeout=TIMEOUT_S)
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(1.5)  # one polite retry for rate limits and blips
            response = session.get(url, params=params, timeout=TIMEOUT_S)
        if response.status_code == 429:
            raise ProviderError(f"{url.split('/')[2]} is rate-limiting requests; try again in a minute.")
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ProviderError(f"{url.split('/')[2]} request failed: {exc}") from exc
    cache.set(key, data, cache_s)
    return data


# --------------------------------------------------------------------------- routing


@dataclass
class RawRoute:
    coords: list[LatLon]
    seg_seconds: list[float] | None  # per coordinate pair, None if unavailable
    duration_s: float
    distance_mi: float
    name: str


class OSRMRouter:
    def route(self, waypoints: list[LatLon], alternatives: int = 0) -> list[RawRoute]:
        path = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in waypoints)
        params = {"overview": "full", "geometries": "polyline", "annotations": "duration", "steps": "true"}
        if alternatives:
            params["alternatives"] = alternatives
        data = _get_json(f"{settings.OSRM_URL}/route/v1/driving/{path}", params, cache_s=6 * 3600)
        if data.get("code") != "Ok":
            if data.get("code") in ("NoRoute", "NoSegment"):
                return []
            raise ProviderError(f"OSRM: {data.get('message') or data.get('code')}")
        routes = []
        for r in data["routes"]:
            coords = decode_polyline(r["geometry"])
            seg = [d for leg in r["legs"] for d in leg.get("annotation", {}).get("duration", [])]
            names = [leg["summary"] for leg in r["legs"] if leg.get("summary")]
            routes.append(
                RawRoute(
                    coords=coords,
                    seg_seconds=seg if len(seg) == len(coords) - 1 else None,
                    duration_s=r["duration"],
                    distance_mi=r["distance"] / METERS_PER_MILE,
                    name=", ".join(dict.fromkeys(", ".join(names).split(", "))) if names else "",
                )
            )
        return routes


class FakeRouter:
    """Three bowed lines between the endpoints: straight, north-ish and south-ish."""

    def route(self, waypoints: list[LatLon], alternatives: int = 0) -> list[RawRoute]:
        a, b = waypoints[0], waypoints[-1]
        bows = (0.0, 0.18, -0.25)[: 1 + min(alternatives, 2)] if len(waypoints) == 2 else (0.0,)
        return [self._arc(a, b, bow, name) for bow, name in zip(bows, ("I-Fast", "US-North", "SR-South"))]

    @staticmethod
    def _arc(a: LatLon, b: LatLon, bow: float, name: str) -> RawRoute:
        dist, brg = haversine_mi(a, b), bearing(a, b)
        coords = []
        for i in range(201):
            t = i / 200
            along = destination(a, brg, dist * t)
            coords.append(destination(along, brg + 90, bow * dist * math.sin(math.pi * t)))
        seg_mi = [haversine_mi(p, q) for p, q in zip(coords, coords[1:])]
        mph = 60 - 6 * abs(bow) / 0.25  # detours use slower roads
        seg_s = [m / mph * 3600 for m in seg_mi]
        return RawRoute(coords, seg_s, sum(seg_s), sum(seg_mi), name)


# --------------------------------------------------------------------------- weather


@dataclass
class Forecast:
    """Hourly series for one point. Precipitation is the total for the hour ending at `times[i]`."""

    times: list[int]  # unix seconds, hourly
    wind_mph: list[float]
    gust_mph: list[float]
    rain_in: list[float]
    snow_in: list[float]

    def at(self, t: float) -> tuple[float, float, float, float]:
        """(wind, gust, rain, snow) at unix time `t`.

        Wind is instantaneous, so it is interpolated between hours. Precipitation is an
        hourly total labelled by the hour it ends, so we take the hour being driven in.
        """
        wind = interpolate(self.times, self.wind_mph, t)
        gust = interpolate(self.times, self.gust_mph, t)
        ending = t // 3600 * 3600 + 3600
        i = min(max(bisect_left(self.times, ending), 0), len(self.times) - 1)
        return wind, gust, self.rain_in[i], self.snow_in[i]


def _hour(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:00")


class OpenMeteoWeather:
    CHUNK = 100  # locations per request
    SNAP_DEG = 0.05  # ~3.5 mi, finer than the forecast models; lets nearby points share a fetch

    def hourly(
        self, points: list[LatLon], start: datetime, end: datetime, max_locations: int | None = None
    ) -> list[Forecast]:
        """Forecasts for `points`, fetching each distinct grid point once.

        Every location counts against Open-Meteo's per-minute quota, so with `max_locations`
        the snapping grid is coarsened until the distinct points fit the budget.
        """
        step = self.SNAP_DEG
        while True:
            snap = [(round(lat / step) * step, round(lon / step) * step) for lat, lon in points]
            unique = list(dict.fromkeys(snap))
            if not max_locations or len(unique) <= max_locations:
                break
            step *= 1.25
        # Sequential on purpose: the free tier rate-limits bursts per minute.
        fetched = [
            f for i in range(0, len(unique), self.CHUNK) for f in self._fetch(unique[i : i + self.CHUNK], start, end)
        ]
        by_point = dict(zip(unique, fetched))
        return [by_point[p] for p in snap]

    def _fetch(self, points: list[LatLon], start: datetime, end: datetime) -> list[Forecast]:
        params = {
            "latitude": ",".join(f"{p[0]:.3f}" for p in points),
            "longitude": ",".join(f"{p[1]:.3f}" for p in points),
            "hourly": "wind_speed_10m,wind_gusts_10m,rain,showers,snowfall",
            "wind_speed_unit": "mph",  # explicit units: mph, mm, cm (converted below)
            "timezone": "GMT",
            "timeformat": "unixtime",
            "start_hour": _hour(start),
            "end_hour": _hour(end),
        }
        if settings.OPEN_METEO_API_KEY:
            params["apikey"] = settings.OPEN_METEO_API_KEY
        data = _get_json(f"{settings.OPEN_METEO_URL}/v1/forecast", params, cache_s=30 * 60)
        if isinstance(data, dict):
            if data.get("error"):
                raise ProviderError(f"Open-Meteo: {data.get('reason')}")
            data = [data]  # single-location responses are not wrapped in a list
        return [self._parse(item["hourly"]) for item in data]

    @staticmethod
    def _parse(h: dict) -> Forecast:
        def col(name: str, scale: float = 1.0) -> list[float]:
            return [(v or 0.0) * scale for v in h[name]]

        rain_mm = [r + s for r, s in zip(col("rain"), col("showers"))]  # liquid precipitation
        return Forecast(
            times=h["time"],
            wind_mph=col("wind_speed_10m"),
            gust_mph=col("wind_gusts_10m"),
            rain_in=[mm / 25.4 for mm in rain_mm],
            snow_in=col("snowfall", 1 / 2.54),  # cm -> in
        )


class FakeWeather:
    """A storm cell over central Illinois at the requested start hour, drifting east. Calm elsewhere.

    The storm is anchored to the window start (not the wall clock) so every run sees the same weather.
    """

    CENTER, RADIUS_MI, DRIFT_MPH = (39.6, -89.9), 70.0, 12.0

    def hourly(
        self, points: list[LatLon], start: datetime, end: datetime, max_locations: int | None = None
    ) -> list[Forecast]:
        t0 = int(start.timestamp()) // 3600 * 3600
        times = list(range(t0, int(end.timestamp()) + 1, 3600))
        centers = [destination(self.CENTER, 90, self.DRIFT_MPH * (t - t0) / 3600) for t in times]
        return [self._point(p, times, centers) for p in points]

    def _point(self, p: LatLon, times: list[int], centers: list[LatLon]) -> Forecast:
        k = [math.exp(-((haversine_mi(p, c) / self.RADIUS_MI) ** 2)) for c in centers]
        wind = [12 + 46 * x for x in k]
        rain = [0.02 + 0.7 * x for x in k]
        return Forecast(times, wind, [w * 1.4 for w in wind], rain, [0.0] * len(times))


# --------------------------------------------------------------------------- geocoding


@dataclass
class Place:
    label: str
    lat: float
    lon: float


class PhotonGeocoder:
    def search(self, query: str, limit: int = 5) -> list[Place]:
        data = _get_json(f"{settings.PHOTON_URL}/api/", {"q": query, "limit": limit, "lang": "en"}, cache_s=24 * 3600)
        places = []
        for f in data.get("features", []):
            p = f["properties"]
            parts = [
                p.get("name"),
                p.get("city") if p.get("city") != p.get("name") else None,
                p.get("state"),
                p.get("country"),
            ]
            lon, lat = f["geometry"]["coordinates"]
            places.append(Place(", ".join(dict.fromkeys(x for x in parts if x)), lat, lon))
        return places


class FakeGeocoder:
    PLACES = [
        Place("Chicago, Illinois, United States", 41.8781, -87.6298),
        Place("St. Louis, Missouri, United States", 38.6270, -90.1994),
        Place("Indianapolis, Indiana, United States", 39.7684, -86.1581),
        Place("Kansas City, Missouri, United States", 39.0997, -94.5786),
        Place("Dallas, Texas, United States", 32.7767, -96.7970),
        Place("Denver, Colorado, United States", 39.7392, -104.9903),
    ]

    def search(self, query: str, limit: int = 5) -> list[Place]:
        q = query.strip().lower()
        return [p for p in self.PLACES if p.label.lower().startswith(q)][:limit]


def get_providers():
    if settings.PROVIDERS == "fake":
        return FakeRouter(), FakeWeather(), FakeGeocoder()
    return OSRMRouter(), OpenMeteoWeather(), PhotonGeocoder()
