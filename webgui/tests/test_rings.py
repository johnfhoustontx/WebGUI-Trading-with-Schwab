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


def test_arc_path_endpoints_sit_on_the_circle_at_the_requested_angles():
    """Locks the start/end coords + radii — the tokens that decide where a value
    arc visually stops. Without this, swapping end_deg for start_deg (or dropping
    the value scaling) leaves the whole suite green."""
    d = rings._arc_path(140, 140, 100, rings.START_DEG, rings._value_angle(50))
    t = d.split()
    assert (t[0], t[3]) == ("M", "A")
    assert _close(float(t[1]), 69.29) and _close(float(t[2]), 210.71, 0.02)  # 0 -> lower-left
    assert _close(float(t[4]), 100.0) and _close(float(t[5]), 100.0)         # radii
    assert _close(float(t[9]), 140.0) and _close(float(t[10]), 40.0)         # 50 -> top


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
