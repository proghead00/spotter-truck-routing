import pytest

from trips.geo import Line, decode_polyline, destination, encode_polyline, haversine_mi, interpolate

GOOGLE_EXAMPLE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
GOOGLE_POINTS = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_polyline_matches_reference_example():
    assert decode_polyline(GOOGLE_EXAMPLE) == pytest.approx(GOOGLE_POINTS)
    assert encode_polyline(GOOGLE_POINTS) == GOOGLE_EXAMPLE


def test_polyline_round_trip_with_negative_deltas():
    pts = [(41.87811, -87.62979), (41.0, -88.5), (38.62701, -90.19940), (38.62701, -90.19940)]
    assert decode_polyline(encode_polyline(pts)) == pytest.approx(pts, abs=1e-5)


def test_haversine_chicago_st_louis():
    assert haversine_mi((41.8781, -87.6298), (38.6270, -90.1994)) == pytest.approx(262, abs=2)


def test_destination_inverts_haversine():
    start = (39.0, -95.0)
    end = destination(start, 45, 100)
    assert haversine_mi(start, end) == pytest.approx(100, rel=1e-6)


def test_interpolate_clamps_and_interpolates():
    assert interpolate([0, 10], [0, 100], 5) == 50
    assert interpolate([0, 10], [0, 100], -1) == 0
    assert interpolate([0, 10], [0, 100], 11) == 100
    assert interpolate([0, 5, 5, 10], [0, 1, 2, 3], 7.5) == 2.5


def test_line_point_at_and_slice():
    line = Line([(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)])
    assert line.length_mi == pytest.approx(2 * 69.09, rel=1e-3)
    lat, lon = line.point_at(line.length_mi / 4)
    assert (lat, lon) == pytest.approx((0.0, 0.5))
    part = line.slice(line.length_mi * 0.25, line.length_mi * 0.75)
    assert part[0] == pytest.approx((0.0, 0.5))
    assert part[1] == (0.0, 1.0)
    assert part[-1] == pytest.approx((0.0, 1.5))
