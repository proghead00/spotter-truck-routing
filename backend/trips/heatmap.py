"""Weather heatmap along the trip corridor, hourly for 0-48 h.

Cells are laid out on a regular Web Mercator grid so the frontend can paint them into a
tiny image and stretch it over the map (the map is Mercator too, so cells line up).
Only cells within the corridor around the routes are kept, and they are filled from the route forecasts.
"""

import math
from datetime import datetime

from django.conf import settings

from .geo import EARTH_RADIUS_MI, LatLon, Line, haversine_mi, inverse_mercator_y, mercator_y
from .providers import Forecast
from .risk import assess

HOURS = 48
SAMPLE_STEP_MI = 3.0


def corridor_grid(lines: list[Line], corridor_mi: float, max_cells: int) -> dict:
    samples = [
        line.point_at(i * SAMPLE_STEP_MI) for line in lines for i in range(int(line.length_mi // SAMPLE_STEP_MI) + 1)
    ]
    samples += [line.coords[-1] for line in lines]
    xs = [math.radians(lon) for _, lon in samples]
    ys = [mercator_y(lat) for lat, _ in samples]
    mid_lat = math.radians(sum(lat for lat, _ in samples) / len(samples))
    mi_to_merc = 1 / (EARTH_RADIUS_MI * math.cos(mid_lat))

    cell_mi = max(settings.HEATMAP_MIN_CELL_MI, corridor_mi / 3)
    while True:
        size = cell_mi * mi_to_merc
        pad = corridor_mi * mi_to_merc + size
        x0, y_top = min(xs) - pad, max(ys) + pad
        cols = math.ceil((max(xs) + pad - x0) / size)
        rows = math.ceil((y_top - (min(ys) - pad)) / size)
        reach = corridor_mi / cell_mi
        r = math.ceil(reach)
        active = set()
        for x, y in zip(xs, ys):
            c, row = int((x - x0) / size), int((y_top - y) / size)
            for dc in range(-r, r + 1):
                for dr in range(-r, r + 1):
                    if dc * dc + dr * dr <= reach * reach and 0 <= c + dc < cols and 0 <= row + dr < rows:
                        active.add((row + dr) * cols + c + dc)
        if len(active) <= max_cells:
            break
        cell_mi *= 1.1  # ponytail: coarsen until under the API budget; adaptive (quadtree) cells if detail matters

    cells = sorted(active)
    centers = [
        (inverse_mercator_y(y_top - (i // cols + 0.5) * size), math.degrees(x0 + (i % cols + 0.5) * size))
        for i in cells
    ]
    bounds = [
        [inverse_mercator_y(y_top - rows * size), math.degrees(x0)],
        [inverse_mercator_y(y_top), math.degrees(x0 + cols * size)],
    ]
    return {"cells": cells, "centers": centers, "cols": cols, "rows": rows, "bounds": bounds, "cell_mi": cell_mi}


NEIGHBOURS = 4


def build_heatmap(grid: dict, points: list[LatLon], forecasts: list[Forecast], start: datetime, load_lb: float) -> dict:
    """Hourly frames for hours 0..48 after `start`, one value per corridor cell.

    Cells are filled from the forecasts already fetched along the routes (inverse-distance blend of the
    nearest few), so the heatmap costs no extra weather calls and cannot be rate-limited on its own.
    Risk is computed from the blended wind, rain and snow, with the same rules as the checkpoints.
    """
    times = [int(start.timestamp()) + h * 3600 for h in range(HOURS + 1)]
    sources = {id(f): (p, f) for p, f in zip(points, forecasts)}  # snapped points share one Forecast
    src_points = [p for p, _ in sources.values()]
    src_frames = [[f.at(t) for t in times] for _, f in sources.values()]

    blends = []  # per cell: [(source index, weight)]
    for center in grid["centers"]:
        nearest = sorted((haversine_mi(center, p), i) for i, p in enumerate(src_points))[:NEIGHBOURS]
        weights = [(i, 1 / max(d, 1.0) ** 2) for d, i in nearest]
        total = sum(w for _, w in weights)
        blends.append([(i, w / total) for i, w in weights])

    risk, wind, rain, snow = [], [], [], []
    for h in range(len(times)):
        frame = [
            [sum(w * src_frames[i][h][k] for i, w in blend) for k in (0, 2, 3)]  # wind, rain, snow
            for blend in blends
        ]
        risk.append([round(assess(w, r, s, load_lb).index, 2) for w, r, s in frame])
        wind.append([round(w) for w, _, _ in frame])
        rain.append([round(r, 2) for _, r, _ in frame])
        snow.append([round(s, 1) for _, _, s in frame])
    return {
        "bounds": [[round(v, 5) for v in corner] for corner in grid["bounds"]],
        "cols": grid["cols"],
        "rows": grid["rows"],
        "cell_mi": round(grid["cell_mi"], 1),
        "cells": grid["cells"],
        "times": times,
        "risk": risk,
        "wind": wind,
        "rain": rain,
        "snow": snow,
    }
