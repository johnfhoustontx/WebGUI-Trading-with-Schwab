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


# ---------------------------------------------------------------- ring_svg
# Path emission order is TRACKS first (one per arc, so every value arc paints
# over its own track), then halo+value per filled arc. With three arcs the
# ``split("<path ")`` indices are therefore:
#   1,2,3 = tracks (outer, mid, inner)   4,5 = arc0 halo, arc0 value   6,7 = arc1 ...
_VALUE_ARC_0 = 5        # first arc's value path, in a 3-arc ring


def _arcs(a=72.0, b=61.0, c=52.0):
    return [{"value": a, "caption": "DAY"},
            {"value": b, "caption": "WEEK"},
            {"value": c, "caption": "MONTH"}]


def test_ring_svg_is_a_single_svg_element_with_fixed_viewbox():
    out = rings.ring_svg(_arcs(), uid="sent")
    assert out.startswith("<svg ") and out.rstrip().endswith("</svg>")
    assert 'viewBox="0 0 280 280"' in out


def test_ring_svg_size_sets_only_width_and_height():
    out = rings.ring_svg(_arcs(), uid="sent", size=340)
    assert 'width="340"' in out and 'height="340"' in out
    assert 'viewBox="0 0 280 280"' in out


def test_ring_svg_emits_no_style_or_filter_elements():
    """ui.html sanitizes via the browser's setHTML; the halo is layered strokes."""
    out = rings.ring_svg(_arcs(), uid="sent")
    assert "<style" not in out
    assert "<filter" not in out


def test_ring_svg_draws_a_track_halo_and_value_arc_per_arc():
    # 3 tracks + 3 halos + 3 value arcs
    assert rings.ring_svg(_arcs(), uid="sent").count("<path ") == 9


def test_ring_svg_colors_each_arc_by_its_own_value():
    from pages import gauge
    out = rings.ring_svg(_arcs(a=95.0, b=50.0, c=5.0), uid="sent")
    assert gauge._ramp_color(0.95) in out
    assert gauge._ramp_color(0.05) in out


def test_ring_svg_ids_are_namespaced_by_uid():
    """Two rings share the /sentiment page — their ids must not collide."""
    a = rings.ring_svg(_arcs(), uid="sent")
    b = rings.ring_svg(_arcs(), uid="trend")
    assert 'id="ring-sent"' in a and 'id="ring-trend"' in b
    assert 'id="ring-trend"' not in a and 'id="ring-sent"' not in b


def test_ring_svg_escapes_the_uid_and_captions():
    out = rings.ring_svg([{"value": 50.0, "caption": '<b>x</b>'}], uid='a"><b>')
    assert "<b>" not in out


def test_ring_svg_puts_the_outermost_value_in_the_center():
    out = rings.ring_svg(_arcs(a=72.0), uid="sent")
    assert ">72<" in out and ">DAY<" in out


def test_ring_svg_puts_week_and_month_in_the_bottom_gap():
    out = rings.ring_svg(_arcs(b=61.0, c=52.0), uid="sent")
    assert ">61<" in out and ">WEEK<" in out
    assert ">52<" in out and ">MONTH<" in out


def test_ring_svg_renders_dash_for_a_missing_value():
    arcs = _arcs()
    arcs[1]["value"] = None
    assert ">—<" in rings.ring_svg(arcs, uid="sent")


def test_ring_svg_missing_value_text_is_muted_not_ramp_red():
    """A None must not borrow the ramp's zero colour — a red em-dash reads as a
    bearish reading the data never supplied."""
    from pages import gauge
    arcs = _arcs()
    arcs[1]["value"] = None
    out = rings.ring_svg(arcs, uid="sent")
    dash_text = [chunk for chunk in out.split("<text ") if ">—<" in chunk][0]
    assert f'fill="{rings.TICK_FILL}"' in dash_text
    assert gauge._ramp_color(0.0) not in dash_text


def test_ring_svg_missing_value_draws_track_only():
    arcs = _arcs()
    arcs[1]["value"] = None
    # 3 tracks + 2 halos + 2 value arcs
    assert rings.ring_svg(arcs, uid="sent").count("<path ") == 7


def test_ring_svg_clamps_out_of_range_and_junk_values():
    for bad in (-40.0, 140.0, "abc", float("nan"), None):
        arcs = _arcs()
        arcs[0]["value"] = bad
        out = rings.ring_svg(arcs, uid="sent")   # must not raise
        assert out.startswith("<svg ")


def test_ring_svg_survives_a_non_dict_arc():
    out = rings.ring_svg([{"value": 50.0, "caption": "DAY"}, None], uid="sent")
    assert out.startswith("<svg ")


def test_ring_svg_never_wraps_past_the_full_sweep():
    """A value above 100 must clamp to the 100 arc, NOT wrap into a short arc
    that reads LOW (the documented _value_angle precondition)."""
    over = rings.ring_svg(_arcs(a=140.0), uid="sent")
    full = rings.ring_svg(_arcs(a=100.0), uid="sent")
    assert over.split("<path ")[_VALUE_ARC_0] == full.split("<path ")[_VALUE_ARC_0]
    # ...and it really is a near-full sweep, not an accidental match on ""
    assert "A 112.00 112.00 0 1 1" in over.split("<path ")[_VALUE_ARC_0]


def _text_node(out, size):
    """The first <text> chunk rendered at ``size``. Selecting by the size
    CONSTANT (not a literal) keeps these tests alive when Task 10 nudges the
    text layout — the size is how we find the node, never what we assert."""
    return [c for c in out.split("<text ") if f'font-size="{size}"' in c][0]


def test_ring_svg_junk_and_nan_read_as_missing_not_zero():
    """The clamp test only proves ring_svg does not raise. A junk/NaN value must
    become the em-dash, NOT a fabricated hard 0 reading — Task 3's windowed mean
    makes an empty-slice NaN a live possibility, not a hypothetical."""
    for bad in ("abc", float("nan"), float("inf"), float("-inf")):
        arcs = _arcs()
        arcs[1]["value"] = bad
        out = rings.ring_svg(arcs, uid="sent")
        assert out.count("<path ") == 7, bad          # week arc drops out
        assert ">—<" in _text_node(out, rings.LEGEND_VALUE_SIZE), bad


def test_ring_svg_clamps_a_negative_to_the_zero_arc():
    under = rings.ring_svg(_arcs(a=-40.0), uid="sent")
    # 0 sweeps nothing, so arc 0 contributes no halo/value pair
    assert under.count("<path ") == 7
    # the CENTRE text is the only place a 0 reading is distinguishable from the
    # 0 scale tick, which renders ">0<" in every ring regardless.
    assert ">0<" in _text_node(under, rings.CENTER_VALUE_SIZE)
    assert "-40" not in under


def test_ring_svg_draws_a_small_value_rather_than_swallowing_it():
    """Guards _MIN_SWEEP_DEG against being widened into a threshold that
    silently drops real low readings."""
    assert rings.ring_svg(_arcs(a=5.0), uid="sent").count("<path ") == 9


def test_ring_svg_draws_the_five_scale_ticks():
    out = rings.ring_svg(_arcs(), uid="sent")
    for tick in ("0", "25", "50", "75", "100"):
        assert f">{tick}<" in out


def test_ring_svg_accepts_fewer_than_three_arcs():
    assert rings.ring_svg(_arcs()[:2], uid="sent").count("<path ") == 6


def test_ring_svg_ignores_arcs_beyond_the_radii_it_has():
    over = _arcs() + [{"value": 30.0, "caption": "YEAR"}]
    assert rings.ring_svg(over, uid="sent").count("<path ") == 9
    assert ">YEAR<" not in rings.ring_svg(over, uid="sent")


def test_ring_svg_each_arc_uses_its_own_radius_outermost_first():
    """Guards arc ORDER and per-arc radius together. Task 1's reviewer noted
    that count/text assertions alone leave every coordinate unchecked.

    The expected radii are LITERAL on purpose: zipping against ``rings.RADII``
    made the test tautological — reversing the constant reversed both sides and
    the assertion still passed (caught by mutation testing)."""
    out = rings.ring_svg(_arcs(), uid="sent")
    tracks = out.split("<path ")[1:4]          # the three track paths, in order
    for path, r in zip(tracks, ("112.00", "90.00", "68.00")):
        assert f"A {r} {r}" in path
    assert rings.RADII == (112.0, 90.0, 68.0)
    assert list(rings.RADII) == sorted(rings.RADII, reverse=True)


def test_ring_svg_value_arcs_use_their_own_radius_not_the_outer_one():
    """The track test above only pins the TRACK radii. Without this, drawing all
    three VALUE arcs at r=112 (concentric arcs collapsed onto one ring) is green."""
    out = rings.ring_svg(_arcs(), uid="sent")
    value_arcs = out.split("<path ")[5::2]      # 5, 7, 9 -> the three value paths
    for path, r in zip(value_arcs, ("112.00", "90.00", "68.00")):
        assert f"A {r} {r}" in path


def test_ring_svg_value_arc_stops_at_the_value_not_the_full_sweep():
    """A 50 arc must end at the top of the dial (140, 28 at r=112)."""
    out = rings.ring_svg([{"value": 50.0, "caption": "DAY"}], uid="sent")
    value_arc = out.split("<path ")[3]         # track, halo, then value arc
    assert "140.00 28.00" in value_arc


def test_ring_svg_halo_is_wider_and_translucent_under_the_value_arc():
    out = rings.ring_svg([{"value": 50.0, "caption": "DAY"}], uid="sent")
    halo, value = out.split("<path ")[2], out.split("<path ")[3]
    assert f'stroke-width="{rings.STROKE + rings.HALO_EXTRA}"' in halo
    assert f'opacity="{rings.HALO_OPACITY}"' in halo
    assert f'stroke-width="{rings.STROKE}"' in value
    assert "opacity=" not in value
