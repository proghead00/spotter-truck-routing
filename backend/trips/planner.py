"""Trip planning: three routes -> timed checkpoints -> weather risk -> recommendation."""

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from django.conf import settings

from .geo import LatLon, Line, bearing, cumulative, destination, encode_polyline, haversine_mi, interpolate
from .heatmap import HOURS as HEATMAP_HOURS
from .heatmap import build_heatmap, corridor_grid
from .providers import Forecast, RawRoute, get_providers
from .risk import Risk, assess

ROUTE_IDS = "ABC"
TRACK_STEP_MI = 2.0


class NoRouteError(Exception):
    pass


# --------------------------------------------------------------------------- routes


def _overlap(a: RawRoute, b: RawRoute) -> float:
    """Share of `a`'s sampled points that lie on (within ~0.7 mi of) `b`."""

    def cells(route: RawRoute, step: int) -> list[tuple[int, int]]:
        return [(round(lat * 100), round(lon * 100)) for lat, lon in route.coords[::step]]

    b_cells = set(cells(b, 1))
    sample = cells(a, max(1, len(a.coords) // 400))
    near = sum(any((x + dx, y + dy) in b_cells for dx in (-1, 0, 1) for dy in (-1, 0, 1)) for x, y in sample)
    return near / len(sample)


def find_routes(router, origin: LatLon, dest: LatLon, wanted: int = 3) -> list[RawRoute]:
    """Ask the router for alternatives; if it returns fewer than `wanted`, force detours
    through via-points offset to either side of the trip and keep the distinct ones."""
    routes = router.route([origin, dest], alternatives=wanted)
    if not routes:
        raise NoRouteError("No drivable route between these points.")
    routes = routes[:wanted]
    if len(routes) >= wanted:
        return routes

    fastest = min(r.duration_s for r in routes)
    span, brg = haversine_mi(origin, dest), bearing(origin, dest)
    vias = [
        destination(destination(origin, brg, span * along), brg + side, span * offset)
        for offset in (0.12, 0.25)
        for along in (0.5, 0.35, 0.65)
        for side in (90, -90)
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        candidates = pool.map(lambda v: router.route([origin, v, dest]), vias)
    for found in candidates:
        for cand in found[:1]:
            if cand.duration_s > fastest * settings.MAX_DETOUR_RATIO:
                continue
            if all(_overlap(cand, r) < 0.85 and _overlap(r, cand) < 0.85 for r in routes):
                routes.append(cand)
        if len(routes) == wanted:
            break
    return routes


def truck_seconds(route: RawRoute, line: Line) -> list[float]:
    """Cumulative truck travel time at each coordinate.

    The public router is a car profile, so each segment is slowed to at most the truck
    speed cap. Without per-segment durations we spread the total time by distance.
    """
    seg_mi = [b - a for a, b in zip(line.miles, line.miles[1:])]
    car = route.seg_seconds or [route.duration_s * m / line.length_mi if line.length_mi else 0 for m in seg_mi]
    cap = settings.TRUCK_MAX_MPH
    return cumulative([max(c, m / cap * 3600) for c, m in zip(car, seg_mi)])


# --------------------------------------------------------------------------- hours of service

# FMCSA property-carrying limits: 11 h driving per shift, a 30 min break once 8 h have been
# driven, then 10 h off duty. Assumes a fresh driver who only drives while on duty.
DRIVE_LIMIT_S = 11 * 3600
BREAK_AFTER_S = 8 * 3600
BREAK_S = 30 * 60
REST_S = 10 * 3600


def hos_elapsed(drive_s: float) -> float:
    """Wall-clock time needed to accumulate `drive_s` of driving under HOS."""
    if drive_s <= 0:
        return 0.0
    shifts = math.ceil(drive_s / DRIVE_LIMIT_S) - 1
    rem = drive_s - shifts * DRIVE_LIMIT_S
    return shifts * (DRIVE_LIMIT_S + BREAK_S + REST_S) + rem + (BREAK_S if rem > BREAK_AFTER_S else 0)


def hos_stops(total_drive_s: float) -> list[tuple[float, str, int]]:
    """(driving seconds when the stop starts, kind, stop length) for a trip."""
    stops, base = [], 0
    while base + BREAK_AFTER_S < total_drive_s:
        stops.append((base + BREAK_AFTER_S, "break", BREAK_S))
        if base + DRIVE_LIMIT_S >= total_drive_s:
            break
        stops.append((base + DRIVE_LIMIT_S, "rest", REST_S))
        base += DRIVE_LIMIT_S
    return stops


# --------------------------------------------------------------------------- per-route evaluation


@dataclass
class Evaluated:
    raw: RawRoute
    line: Line
    drive_seconds: list[float]  # cumulative driving time at each coordinate
    hos: bool
    checkpoints: list[dict] = field(default_factory=list)
    spans: list[dict] = field(default_factory=list)
    stops: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def drive_at(self, mile: float) -> float:
        return interpolate(self.line.miles, self.drive_seconds, mile)

    def elapsed_at(self, mile: float) -> float:
        drive = self.drive_at(mile)
        return hos_elapsed(drive) if self.hos else drive

    @property
    def duration_s(self) -> float:
        return self.elapsed_at(self.line.length_mi)


def checkpoint_miles(length: float, interval: float) -> list[float]:
    """0, interval, 2*interval, ... and always the destination."""
    miles = [i * interval for i in range(int(length // interval) + 1)]
    if length - miles[-1] > 0.5:
        miles.append(length)
    elif len(miles) > 1:
        miles[-1] = length  # fold a sub-half-mile remainder into the last checkpoint
    return miles


def owned_spans(miles: list[float]) -> list[tuple[float, float]]:
    """Each checkpoint stands for the road halfway to its neighbours."""
    mids = [(a + b) / 2 for a, b in zip(miles, miles[1:])]
    edges = [miles[0], *mids, miles[-1]]
    return list(zip(edges, edges[1:]))


def summarize(checkpoints: list[dict], duration_s: float, drive_s: float, length: float) -> dict:
    by_level = {r: 0.0 for r in Risk}
    weighted = 0.0
    for cp in checkpoints:
        span = cp["span_end"] - cp["span_start"]
        by_level[Risk(cp["level"])] += span
        weighted += cp["level"] * span
    return {
        "miles_by_level": {r.name.lower(): round(m, 1) for r, m in by_level.items()},
        "avg_risk": round(weighted / length, 3) if length else 0.0,
        "max_level": max(cp["level"] for cp in checkpoints),
        "duration_s": round(duration_s),
        "drive_s": round(drive_s),
    }


def evaluate(ev: Evaluated, forecasts: list[Forecast], miles: list[float], departure: datetime, load_lb: float):
    t0 = departure.timestamp()
    spans = owned_spans(miles)
    for mile, forecast, (start, end) in zip(miles, forecasts, spans):
        lat, lon = ev.line.point_at(mile)
        elapsed = ev.elapsed_at(mile)
        wind, gust, rain, snow = forecast.at(t0 + elapsed)
        verdict = assess(wind, rain, snow, load_lb)
        ev.checkpoints.append(
            {
                "mile": round(mile, 1),
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "eta": (departure + timedelta(seconds=elapsed)).isoformat(),
                "elapsed_s": round(elapsed),
                "wind_mph": round(wind, 1),
                "gust_mph": round(gust, 1),
                "rain_in_hr": round(rain, 3),
                "snow_in_hr": round(snow, 2),
                "level": int(verdict.level),
                "risk_index": verdict.index,
                "reason": verdict.reason,
                "span_start": round(start, 2),
                "span_end": round(end, 2),
            }
        )
    ev.summary = summarize(ev.checkpoints, ev.duration_s, ev.drive_seconds[-1], ev.line.length_mi)
    if ev.hos:
        for drive_s, kind, length in hos_stops(ev.drive_seconds[-1]):
            mile = interpolate(ev.drive_seconds, ev.line.miles, drive_s)
            lat, lon = ev.line.point_at(mile)
            start = hos_elapsed(drive_s)
            ev.stops.append(
                {
                    "kind": kind,
                    "mile": round(mile, 1),
                    "lat": round(lat, 5),
                    "lon": round(lon, 5),
                    "start": (departure + timedelta(seconds=start)).isoformat(),
                    "elapsed_s": round(start),
                    "duration_s": length,
                }
            )
    # Merge neighbouring checkpoints of equal level into coloured map segments.
    for cp in ev.checkpoints:
        if ev.spans and ev.spans[-1]["level"] == cp["level"]:
            ev.spans[-1]["end_mi"] = cp["span_end"]
        else:
            ev.spans.append({"level": cp["level"], "start_mi": cp["span_start"], "end_mi": cp["span_end"]})
    for span in ev.spans:
        span["polyline"] = encode_polyline(ev.line.slice(span["start_mi"], span["end_mi"]))


# --------------------------------------------------------------------------- recommendation

CRITERIA = (
    ("no_travel_mi", "Fewest No Travel miles"),
    ("severe_mi", "Fewest Severe miles"),
    ("high_mi", "Fewest High miles"),
    ("avg_risk", "Lowest average risk"),
    ("duration_min", "Shortest travel time"),
)


def rank_key(summary: dict) -> tuple:
    """Lexicographic preference from the brief. No Travel miles come first: a route that
    crosses a No Travel zone is worse than any Severe-only route. Values are rounded so
    noise (0.04 mi, 20 s) never decides between otherwise equal routes."""
    m = summary["miles_by_level"]
    return (
        round(m["no_travel"]),
        round(m["severe"]),
        round(m["high"]),
        round(summary["avg_risk"], 2),
        round(summary["duration_s"] / 60),
    )


def _mi(value: float) -> str:
    return f"{value:.0f} mi"


def _hm(minutes: float) -> str:
    return f"{int(minutes // 60)}h {int(minutes % 60):02d}m"


def recommend(routes: list[dict]) -> dict:
    ordered = sorted(routes, key=lambda r: rank_key(r["summary"]))
    best = ordered[0]
    for rank, r in enumerate(ordered, 1):
        r["rank"] = rank
        r["recommended"] = r is best
    reason = "Only route available."
    if len(ordered) > 1:
        a, b = rank_key(best["summary"]), rank_key(ordered[1]["summary"])
        i = next((k for k in range(len(a)) if a[k] != b[k]), len(a) - 1)
        fmt = (_mi, _mi, _mi, "{:.2f}".format, _hm)[i]
        reason = f"{CRITERIA[i][1]}: {fmt(a[i])} vs {fmt(b[i])} on Route {ordered[1]['id']}."
        if a == b:
            reason = f"Ties Route {ordered[1]['id']} on every criterion; listed first by the router."
    warning = None
    worst = best["summary"]["miles_by_level"]
    if worst["no_travel"] > 0:
        warning = "Even the recommended route crosses No Travel conditions. Delay departure until the weather clears."
    elif worst["severe"] > 0:
        warning = "Even the recommended route has Severe weather. Consider a later departure."
    return {"route_id": best["id"], "reason": reason, "warning": warning, "criteria": [c[1] for c in CRITERIA]}


# --------------------------------------------------------------------------- entry point


def track(ev: Evaluated) -> list[list[float]]:
    """[lat, lon, seconds after departure] every couple of miles, holding still during stops."""
    rows = [(m, ev.elapsed_at(m)) for m in [*_steps(ev.line.length_mi, TRACK_STEP_MI), ev.line.length_mi]]
    for stop in ev.stops:
        rows += [(stop["mile"], stop["elapsed_s"]), (stop["mile"], stop["elapsed_s"] + stop["duration_s"])]
    rows.sort(key=lambda r: r[1])
    return [[*(round(v, 5) for v in ev.line.point_at(m)), round(t)] for m, t in rows]


def _steps(length: float, step: float) -> list[float]:
    return [i * step for i in range(int(length // step) + 1)]


def plan_trip(
    origin: dict, dest: dict, departure: datetime, load_lb: float, interval_mi: int, hos: bool = True
) -> dict:
    router, weather, _ = get_providers()
    departure = departure.astimezone(UTC)
    raw = find_routes(router, (origin["lat"], origin["lon"]), (dest["lat"], dest["lon"]))

    evaluated, miles_per_route, points = [], [], []
    for r in raw:
        line = Line(r.coords)
        ev = Evaluated(raw=r, line=line, drive_seconds=truck_seconds(r, line), hos=hos)
        miles = checkpoint_miles(line.length_mi, interval_mi)
        evaluated.append(ev)
        miles_per_route.append(miles)
        points.extend(line.point_at(m) for m in miles)

    # One weather request covers both the checkpoints (until arrival) and the heatmap (0-48 h).
    grid = corridor_grid([ev.line for ev in evaluated], settings.HEATMAP_CORRIDOR_MI, settings.HEATMAP_MAX_CELLS)
    start = departure.replace(minute=0, second=0, microsecond=0)
    longest = max(ev.duration_s for ev in evaluated)
    end = max(start + timedelta(hours=HEATMAP_HOURS + 1), departure + timedelta(seconds=longest + 2 * 3600))
    forecasts = weather.hourly(points + grid["centers"], start, end)
    heatmap = build_heatmap(grid, forecasts[len(points) :], start, load_lb)

    routes, k = [], 0
    for route_id, ev, miles in zip(ROUTE_IDS, evaluated, miles_per_route):
        evaluate(ev, forecasts[k : k + len(miles)], miles, departure, load_lb)
        k += len(miles)
        routes.append(
            {
                "id": route_id,
                "name": ev.raw.name,
                "distance_mi": round(ev.line.length_mi, 1),
                "duration_s": round(ev.duration_s),
                "arrival": (departure + timedelta(seconds=ev.duration_s)).isoformat(),
                "polyline": encode_polyline(ev.line.coords),
                "spans": ev.spans,
                "checkpoints": ev.checkpoints,
                "summary": ev.summary,
                "stops": ev.stops,
                "track": track(ev),
            }
        )

    return {
        "origin": origin,
        "destination": dest,
        "departure": departure.isoformat(),
        "load_lb": load_lb,
        "interval_mi": interval_mi,
        "hos": hos,
        "routes": routes,
        "recommendation": recommend(routes),
        "heatmap": heatmap,
    }
