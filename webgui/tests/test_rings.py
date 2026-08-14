from pages import rings


def _close(a, b, tol=0.01):
    return abs(a - b) < tol


def test_point_at_zero_degrees_is_top():
    x, y = rings._point(140, 140, 100, 0)
    assert _close(x, 140) and _close(y, 40)


def test_point_at_ninety_degrees_is_right():
    x, y = rings._point(140, 140, 100, 90)
    assert _close(x, 240) and _close(y, 140)


def test_point_at_start_angle_is_lower_left():
    x, y = rings._point(140, 140, 100, rings.START_DEG)
    assert x < 140 and y > 140


def test_value_angle_maps_endpoints_and_midpoint():
    assert _close(rings._value_angle(0), 225.0)
    assert _close(rings._value_angle(50), 360.0)      # top
    assert _close(rings._value_angle(100), 495.0)     # lower-right


def test_arc_path_is_empty_at_zero():
    assert rings._arc_path(140, 140, 100, 225.0, 225.0) == ""


def test_arc_path_sets_large_arc_flag_past_180_degrees():
    short = rings._arc_path(140, 140, 100, 225.0, 315.0)   # 90 deg
    long_ = rings._arc_path(140, 140, 100, 225.0, 495.0)   # 270 deg
    # "M x y A rx ry <x-rot> <large> <sweep> x1 y1" -> large is token 7
    assert short.split()[7] == "0"
    assert long_.split()[7] == "1"


def test_arc_path_always_sweeps_clockwise():
    p = rings._arc_path(140, 140, 100, 225.0, 495.0)
    assert p.split()[8] == "1"
