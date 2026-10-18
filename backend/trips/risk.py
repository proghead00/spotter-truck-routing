"""Weather risk classification, straight from the assessment's risk and load tables.

Levels are ordered integers so "worse" is just "greater". Next to the discrete level
we also compute a continuous *risk index* in [0, 4]: the level plus how far the value
sits inside its band. `int(index) == level` always holds, so the index can drive a
smooth heatmap without disagreeing with the table.
"""

from bisect import bisect_right
from dataclasses import dataclass
from enum import IntEnum


class Risk(IntEnum):
    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()


@dataclass(frozen=True)
class Scale:
    """Bands for one weather variable.

    `bounds` are the inclusive lower bounds of Moderate, High and Severe. The table
    writes overlapping edges ("0.10-0.25", "0.25-0.50"); we read each shared edge as
    the start of the worse band. No Travel is `>= no_travel` for wind ("≥55 mph") and
    `> no_travel` for rain and snow (">1.00", ">3.0"), exactly as the table writes it.
    """

    bounds: tuple[float, float, float]
    no_travel: float
    no_travel_inclusive: bool

    def index(self, value: float) -> float:
        value = max(value, 0.0)
        if value > self.no_travel or (self.no_travel_inclusive and value == self.no_travel):
            return float(Risk.NO_TRAVEL)
        level = bisect_right(self.bounds, value)
        edges = (0.0, *self.bounds, self.no_travel)
        lo, hi = edges[level], edges[level + 1]
        return level + min((value - lo) / (hi - lo), 0.999)


WIND_MPH = Scale(bounds=(25, 35, 45), no_travel=55, no_travel_inclusive=True)
RAIN_IN_HR = Scale(bounds=(0.10, 0.25, 0.50), no_travel=1.00, no_travel_inclusive=False)
SNOW_IN_HR = Scale(bounds=(0.5, 1.0, 2.0), no_travel=3.0, no_travel_inclusive=False)

# Load rules. Wind >= 55 mph is No Travel for any load, which WIND_MPH already does.
NO_TRAVEL_LOAD_LB = 30_000  # 45-54 mph + > 30,000 lb -> No Travel
SEVERE_LOAD_LB = 40_000  # 35-44 mph + > 40,000 lb -> Severe


def wind_index(wind_mph: float, load_lb: float) -> float:
    index = WIND_MPH.index(wind_mph)
    level = int(index)
    if level == Risk.SEVERE and load_lb > NO_TRAVEL_LOAD_LB:
        return float(Risk.NO_TRAVEL)
    if level == Risk.HIGH and load_lb > SEVERE_LOAD_LB:
        return index + 1  # same position inside the band, one level up
    return index


@dataclass(frozen=True)
class Assessment:
    level: Risk
    index: float
    reason: str


def assess(wind_mph: float, rain_in_hr: float, snow_in_hr: float, load_lb: float) -> Assessment:
    """Classify one checkpoint: the worst of wind (load-adjusted), rain and snow."""
    factors = {
        "wind": wind_index(wind_mph, load_lb),
        "rain": RAIN_IN_HR.index(rain_in_hr),
        "snow": SNOW_IN_HR.index(snow_in_hr),
    }
    driver = max(factors, key=factors.get)
    index = factors[driver]
    level = Risk(int(index))
    if level == Risk.LOW:
        reason = "Conditions within normal limits"
    elif driver == "wind":
        reason = f"Wind {wind_mph:.0f} mph"
        if int(index) != int(WIND_MPH.index(wind_mph)):
            reason += f" with a {load_lb:,.0f} lb load"
    elif driver == "rain":
        reason = f"Rain {rain_in_hr:.2f} in/hr"
    else:
        reason = f"Snow {snow_in_hr:.1f} in/hr"
    return Assessment(level=level, index=round(index, 3), reason=reason)
