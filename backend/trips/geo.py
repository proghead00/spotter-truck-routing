"""Small geodesy helpers: distances, polyline codec, walking along a line."""

import math
from bisect import bisect_right

EARTH_RADIUS_MI = 3958.7613
METERS_PER_MILE = 1609.344

LatLon = tuple[float, float]


def haversine_mi(a: LatLon, b: LatLon) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(min(1.0, math.sqrt(h)))


def destination(origin: LatLon, bearing_deg: float, distance_mi: float) -> LatLon:
    """Point reached from `origin` travelling `distance_mi` on an initial bearing."""
    lat1, lon1 = map(math.radians, origin)
    brg, d = math.radians(bearing_deg), distance_mi / EARTH_RADIUS_MI
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180


def bearing(a: LatLon, b: LatLon) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return math.degrees(math.atan2(y, x)) % 360


def decode_polyline(encoded: str, precision: int = 5) -> list[LatLon]:
    """Google encoded polyline -> [(lat, lon)]. OSRM uses precision 5 by default."""
    coords, index, lat, lon, factor = [], 0, 0, 0, 10**precision
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        coords.append((lat / factor, lon / factor))
    return coords


def encode_polyline(coords: list[LatLon], precision: int = 5) -> str:
    out, prev, factor = [], (0, 0), 10**precision
    for lat, lon in coords:
        cur = (round(lat * factor), round(lon * factor))
        for delta in (cur[0] - prev[0], cur[1] - prev[1]):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev = cur
    return "".join(out)


def cumulative(values: list[float]) -> list[float]:
    """[a, b, c] -> [0, a, a+b, a+b+c]."""
    out = [0.0]
    for v in values:
        out.append(out[-1] + v)
    return out


def interpolate(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation of y at x on a non-decreasing xs, clamped to the ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect_right(xs, x) - 1
    span = xs[i + 1] - xs[i]
    t = (x - xs[i]) / span if span else 0.0
    return ys[i] + (ys[i + 1] - ys[i]) * t


class Line:
    """A polyline addressed by milepost."""

    def __init__(self, coords: list[LatLon]):
        self.coords = coords
        self.lats = [c[0] for c in coords]
        self.lons = [c[1] for c in coords]
        self.miles = cumulative([haversine_mi(a, b) for a, b in zip(coords, coords[1:])])

    @property
    def length_mi(self) -> float:
        return self.miles[-1]

    def point_at(self, mile: float) -> LatLon:
        return interpolate(self.miles, self.lats, mile), interpolate(self.miles, self.lons, mile)

    def slice(self, start: float, end: float) -> list[LatLon]:
        """The part of the line between two mileposts, with interpolated end points."""
        lo, hi = bisect_right(self.miles, start), bisect_right(self.miles, end)
        inner = [c for c, m in zip(self.coords[lo:hi], self.miles[lo:hi]) if start < m < end]
        return [self.point_at(start), *inner, self.point_at(end)]


def mercator_y(lat: float) -> float:
    lat = max(min(lat, 85.0), -85.0)
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def inverse_mercator_y(y: float) -> float:
    return math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2)
