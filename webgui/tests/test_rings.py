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


# ------------------------------------------------ DOMPurify allow-list helper
# Shared by every hand-drawn SVG builder's allow-list test (console, regime mix,
# momentum, finder, persistence) — imported from here.


def _dompurify_allowlist():
    """The tag/attribute names DOMPurify will keep, read out of the copy NiceGUI
    actually ships.

    NiceGUI's templates/index.html REPLACES ``Element.prototype.setHTML`` with
    ``DOMPurify.sanitize(html)``, so ``ui.html``'s default sanitizing path is
    DOMPurify's default allowlist — not the browser's native sanitizer, which is
    a laxer thing entirely.

    The bundle is minified, so the arrays are recovered as the long contiguous
    runs of quoted lowercase tokens. Matching on run length rather than plain
    substring keeps an unrelated identifier elsewhere in the file from reading
    as an allowed name.

    But not every such array is an ALLOW list — DOMPurify also ships deny lists
    (the SVG disallowed-elements array and FORBID_CONTENTS), and unioning those
    in would bless the very names it strips. ``<use>`` is a plausible reach for
    symbol reuse and was being waved through. Runs containing ``script`` are
    therefore dropped: no allowlist in a sanitizer contains ``script``, and both
    deny lists do, which makes it a reliable discriminator rather than an
    index-based guess that would rot when the bundle is rebuilt.

    Measured at DOMPurify 3.4.0: 8 runs, 2 dropped, 507 names over the six real
    allowlists (html/svg/mathml tags + html/svg/mathml attributes)."""
    import pathlib
    import re

    from nicegui import ui
    src = (pathlib.Path(ui.__file__).parent / "static" / "dompurify.mjs") \
        .read_text(encoding="utf-8", errors="replace")
    names = set()
    for run in re.findall(r'(?:"[a-z][a-z0-9-]*",){19,}"[a-z][a-z0-9-]*"', src):
        tokens = set(re.findall(r'"([a-z][a-z0-9-]*)"', run))
        if "script" in tokens:          # a deny list, not an allowlist
            continue
        names |= tokens
    assert len(names) > 300, "allowlist extraction found too little — bundle changed?"
    # The exclusion really fired: these are stripped, so they must NOT be here.
    assert not ({"script", "foreignobject", "use", "animate"} & names)
    return names


