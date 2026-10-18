import pytest

from trips.risk import RAIN_IN_HR, SNOW_IN_HR, WIND_MPH, Risk, assess, wind_index

L, M, H, S, N = Risk.LOW, Risk.MODERATE, Risk.HIGH, Risk.SEVERE, Risk.NO_TRAVEL


@pytest.mark.parametrize(
    "mph, level",
    [(0, L), (24.9, L), (25, M), (34.9, M), (35, H), (44.9, H), (45, S), (54.9, S), (55, N), (80, N)],
)
def test_wind_table(mph, level):
    assert int(WIND_MPH.index(mph)) == level


@pytest.mark.parametrize(
    "inches, level",
    [(0, L), (0.099, L), (0.10, M), (0.249, M), (0.25, H), (0.499, H), (0.50, S), (1.00, S), (1.001, N)],
)
def test_rain_table(inches, level):
    assert int(RAIN_IN_HR.index(inches)) == level


@pytest.mark.parametrize(
    "inches, level",
    [(0, L), (0.49, L), (0.5, M), (0.99, M), (1.0, H), (1.99, H), (2.0, S), (3.0, S), (3.01, N)],
)
def test_snow_table(inches, level):
    assert int(SNOW_IN_HR.index(inches)) == level


@pytest.mark.parametrize(
    "mph, load, level",
    [
        (55, 0, N),  # >= 55 mph is No Travel for any load
        (60, 10_000, N),
        (45, 30_000, S),  # 45-54 mph needs MORE than 30,000 lb to become No Travel
        (45, 30_001, N),
        (54, 45_000, N),
        (35, 40_000, H),  # 35-44 mph needs MORE than 40,000 lb to become Severe
        (35, 40_001, S),
        (44, 80_000, S),
        (30, 80_000, M),  # load rules do not touch other bands
        (10, 80_000, L),
    ],
)
def test_load_rules(mph, load, level):
    assert int(wind_index(mph, load)) == level
    assert assess(mph, 0, 0, load).level == level


def test_worst_condition_wins():
    assert assess(10, 0.3, 0.6, 0).level == H  # rain High beats snow Moderate
    assert assess(10, 0.3, 2.5, 0).level == S
    a = assess(40, 0, 0, 42_000)
    assert a.level == S and "42,000 lb" in a.reason


def test_index_is_continuous_and_consistent_with_level():
    previous = -1.0
    for tenth in range(0, 700):
        mph = tenth / 10
        index = WIND_MPH.index(mph)
        assert int(index) == int(assess(mph, 0, 0, 0).level)
        assert index >= previous
        previous = index


def test_labels():
    assert Risk.NO_TRAVEL.label == "No Travel"
    assert Risk.LOW.label == "Low"
