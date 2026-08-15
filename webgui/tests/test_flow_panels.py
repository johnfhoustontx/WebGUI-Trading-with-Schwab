"""Tests for the Options Flow console panels (Premium Divergence + Flow Field).

Everything in ``flow_panels`` is a pure string/coordinate builder, so these are
value tests. The one test that cannot be written any other way is the DOMPurify
guard: a stripped tag or attribute changes NOTHING server-side, so the emitted
string stays correct and the panel still renders — just wrong.
"""
import json
import re

import pytest

from pages.options import flow_panels as fp

_T0 = 1_755_264_600          # a fixed collection boundary


def _flow_rows(n=12, call=2.0, put=1.0):
    """``n`` flow rows with linearly growing (cumulative) premium."""
    return [{"ts": _T0 + i * 60, "spot": 775.0 + (i % 5) * 0.25,
             "call_prem": call * i * 1e6, "put_prem": put * i * 1e6}
            for i in range(1, n + 1)]


def _ladder_rows(times):
    return [{"ts": ts, "spot": 775.0,
             "rows": [[770.0 + k, (k + 1) * 1e6, (11 - k) * 1e6]
                      for k in range(11)]}
            for ts in times]


#############################################
# PRIMITIVES
#############################################

def test_num_rejects_bools_and_non_finite():
    # True is an int in Python; unguarded it would plot as a premium of 1.0.
    assert fp._num(True) is None and fp._num(False) is None
    assert fp._num(float("nan")) is None
    assert fp._num(float("inf")) is None
    assert fp._num("2") is None
    assert fp._num(2) == 2.0


def test_scale_maps_endpoints_and_midpoint():
    assert fp._scale(0, 0, 10, 100, 200) == 100
    assert fp._scale(10, 0, 10, 100, 200) == 200
    assert fp._scale(5, 0, 10, 100, 200) == 150


def test_scale_pins_a_degenerate_range_to_the_midpoint():
    """A flat series is a real reading (nothing traded all session), so it must
    plot as a flat line halfway up rather than divide by zero."""
    assert fp._scale(7, 7, 7, 100, 200) == 150


def test_path_needs_two_points():
    assert fp._path([]) == ""
    assert fp._path([(1.0, 2.0)]) == ""
    assert fp._path([(1.0, 2.0), (3.0, 4.0)]) == "M 1.0 2.0 L 3.0 4.0"


def test_glow_line_emits_a_halo_under_a_hairline():
    """The spec's glow: a wide translucent copy under a 0.75px stroke. NOT an
    SVG filter — DOMPurify strips those, and a blur is expensive to composite."""
    out = fp._glow_line("M 0 0 L 1 1", "#35C8FF")
    assert out.count("<path") == 2
    assert f'stroke-width="{fp.GLOW_W}"' in out
    assert f'stroke-opacity="{fp.GLOW_O}"' in out
    assert f'stroke-width="{fp.HAIRLINE}"' in out
    assert "filter" not in out


def test_glow_line_is_empty_for_an_empty_path():
    assert fp._glow_line("", "#35C8FF") == ""


def test_broken_paths_never_bridge_a_gap():
    """Bridging would draw a straight line across minutes the collector never
    saw, which on a price track reads as calm rather than as missing data."""
    out = fp._broken_paths([1.0, 2.0, 3.0, 4.0], [10.0, 11.0, None, 13.0])
    assert len(out) == 1                       # the trailing single point drops
    assert "3.0" not in out[0]
    two = fp._broken_paths([1.0, 2.0, 3.0, 4.0], [10.0, 11.0, None, 13.0][:2] +
                           [None, 13.0])
    assert len(two) == 1


def test_broken_paths_splits_into_two_runs():
    out = fp._broken_paths([1.0, 2.0, 3.0, 4.0, 5.0],
                           [1.0, 2.0, None, 4.0, 5.0])
    assert len(out) == 2


def test_id_strips_anything_illegal_in_a_dom_id():
    assert fp._id('a"b<c') == "abc"
    assert fp._id("") == "fx"
    assert fp._id(None) == "fx"


def test_esc_escapes_markup():
    assert fp._esc('<b>&"') == "&lt;b&gt;&amp;&quot;"


#############################################
# FORMATTING
#############################################

def test_fmt_signed_uses_a_true_minus_not_a_hyphen():
    """U+2212 is the same width as '+' in the mono face; a hyphen makes the
    column jitter every time the sign flips."""
    assert fp.fmt_signed(90.25, 2) == "+90.25"
    assert fp.fmt_signed(-90.25, 2) == "−90.25"
    assert fp.fmt_signed(None) == "—"


def test_fmt_helpers_degrade_to_an_em_dash():
    assert fp.fmt_m(None) == "—"
    assert fp.fmt_price(None) == "—"
    assert fp.fmt_time(None) == "—"
    assert fp.fmt_time(float("nan")) == "—"


def test_fmt_time_is_central_not_host_local():
    """Every other time on this page is CT; a cursor reading in another zone
    would be read as the same clock."""
    # 1755264600 == 2025-08-15 08:30 CT.
    assert fp.fmt_time(1_755_264_600) == "08:30"


def test_net_color_never_asserts_a_side_for_a_missing_reading():
    assert fp.net_color(1.0) == fp.C["call"]
    assert fp.net_color(-1.0) == fp.C["put"]
    assert fp.net_color(None) not in (fp.C["call"], fp.C["put"])


def test_dte_label_falls_back_rather_than_inventing_a_number():
    assert fp.dte_label(0) == "0DTE"
    assert fp.dte_label(3) == "3DTE"
    assert fp.dte_label(None) == "NEAREST EXPIRY"


#############################################
# DIVERGENCE — series
#############################################

def test_divergence_series_keeps_only_rows_with_both_premiums():
    """The ribbon is the area BETWEEN call and put, so a one-sided row has no
    ribbon to draw and would leave a wedge anchored to whichever side existed."""
    rows = [{"ts": 1, "spot": 1.0, "call_prem": 2e6, "put_prem": 1e6},
            {"ts": 2, "spot": 1.0, "call_prem": 2e6},
            {"ts": 3, "spot": 1.0, "put_prem": 1e6},
            {"ts": 4, "spot": 1.0, "call_prem": None, "put_prem": 1e6}]
    out = fp.divergence_series(rows)
    assert out["ts"] == [1]


def test_divergence_series_converts_to_millions():
    out = fp.divergence_series(
        [{"ts": 1, "spot": 5.0, "call_prem": 2.5e6, "put_prem": 1e6}])
    assert out["call"] == [2.5] and out["put"] == [1.0]


def test_divergence_series_keeps_a_missing_spot_as_none():
    out = fp.divergence_series(
        [{"ts": 1, "spot": None, "call_prem": 2e6, "put_prem": 1e6}])
    assert out["spot"] == [None]


def test_divergence_series_sorts_by_timestamp():
    rows = [{"ts": 3, "spot": 1.0, "call_prem": 3e6, "put_prem": 1e6},
            {"ts": 1, "spot": 1.0, "call_prem": 1e6, "put_prem": 1e6}]
    assert fp.divergence_series(rows)["ts"] == [1, 3]


def test_divergence_series_is_total_over_junk():
    assert fp.divergence_series(None)["ts"] == []
    assert fp.divergence_series(["x", None, 3])["ts"] == []


def test_divergence_session_reads_the_last_point_not_a_sum():
    """The stored premiums are already daily-cumulative — summing them would
    multiply the session by its own sample count."""
    series = fp.divergence_series(_flow_rows(4))
    out = fp.divergence_session(series)
    assert out["call_total"] == series["call"][-1] == 8.0
    assert out["put_total"] == series["put"][-1] == 4.0


def test_divergence_session_reports_the_spot_range():
    series = fp.divergence_series(_flow_rows(12))
    out = fp.divergence_session(series)
    assert out["high"] == max(series["spot"])
    assert out["low"] == min(series["spot"])


def test_divergence_session_is_empty_without_data():
    out = fp.divergence_session({"call": [], "put": [], "spot": []})
    assert out == {"call_total": None, "put_total": None,
                   "high": None, "low": None}


#############################################
# RIBBON
#############################################

def test_ribbon_is_one_call_led_run_when_call_stays_on_top():
    # Pixel y grows DOWNWARD, so call-led means y_call < y_put.
    pos, neg = fp.ribbon_segments([0.0, 1.0, 2.0], [10.0, 10.0, 10.0],
                                  [20.0, 20.0, 20.0])
    assert len(pos) == 1 and neg == []
    assert pos[0].endswith(" Z")


def test_ribbon_is_one_put_led_run_when_put_stays_on_top():
    pos, neg = fp.ribbon_segments([0.0, 1.0], [20.0, 20.0], [10.0, 10.0])
    assert pos == [] and len(neg) == 1


def test_ribbon_splits_at_the_crossing():
    """The crossover IS the read this panel exists for, so the two fills must
    meet in a point rather than overlapping a sample either side of it."""
    pos, neg = fp.ribbon_segments([0.0, 10.0], [0.0, 20.0], [20.0, 0.0])
    assert len(pos) == 1 and len(neg) == 1
    # Both runs terminate on the same interpolated midpoint (x=5, y=10).
    assert "5.0 10.0" in pos[0] and "5.0 10.0" in neg[0]


def test_ribbon_crossing_is_interpolated_not_snapped_to_a_sample():
    pos, neg = fp.ribbon_segments([0.0, 10.0], [0.0, 12.0], [9.0, 0.0])
    joint = re.findall(r"(\d+\.\d) (\d+\.\d)", pos[0] + neg[0])
    xs = {float(x) for x, _ in joint}
    assert xs - {0.0, 10.0}, "expected an interpolated crossing x"


def test_ribbon_is_empty_below_two_points():
    assert fp.ribbon_segments([], [], []) == ([], [])
    assert fp.ribbon_segments([1.0], [1.0], [2.0]) == ([], [])


#############################################
# GEOMETRY
#############################################

def test_divergence_geometry_is_none_without_data():
    assert fp.divergence_geometry(fp.divergence_series([])) is None


def test_divergence_premium_axis_starts_at_zero():
    """Premium is cumulative from zero; a zoomed baseline would exaggerate a
    quiet session into a dramatic one."""
    geom = fp.divergence_geometry(fp.divergence_series(_flow_rows()))
    assert geom["prem"][0] == 0.0


def test_divergence_geometry_spans_the_plot_box():
    geom = fp.divergence_geometry(fp.divergence_series(_flow_rows()))
    x0, _y0, x1, _y1 = fp.DIV_PLOT
    assert geom["xs"][0] == x0 and geom["xs"][-1] == x1


def test_divergence_spot_scale_is_independent_of_the_premium_scale():
    """Spot is on its own scale: at ~775 against premium of ~20 it would sit off
    the top of a shared axis and the panel would show one line, not three."""
    geom = fp.divergence_geometry(fp.divergence_series(_flow_rows()))
    _x0, y0, _x1, y1 = fp.DIV_PLOT
    assert all(y0 <= y <= y1 for y in geom["y_spot"] if y is not None)


def test_divergence_geometry_survives_an_all_missing_spot():
    rows = [{"ts": _T0 + i, "spot": None, "call_prem": i * 1e6,
             "put_prem": i * 1e6} for i in range(1, 5)]
    geom = fp.divergence_geometry(fp.divergence_series(rows))
    assert geom is not None and geom["y_spot"] == [None] * 4


#############################################
# LADDER ALIGNMENT
#############################################

def test_align_ladder_matches_on_timestamp():
    times = [1, 2, 3]
    lad = [{"ts": 2, "rows": [[100.0, 1.0, 2.0]]}]
    assert fp.align_ladder(times, lad) == [None, [[100.0, 1.0, 2.0]], None]


def test_align_ladder_does_not_carry_forward():
    """A carried ladder would render as a live reading at a time it was never
    taken."""
    assert fp.align_ladder([1, 2], [{"ts": 1, "rows": [[1.0, 1.0, 1.0]]}]) \
        == [[[1.0, 1.0, 1.0]], None]


def test_align_ladder_is_total_over_junk():
    assert fp.align_ladder([1], None) == [None]
    assert fp.align_ladder([1], ["x", {"ts": None}, {}]) == [None]


#############################################
# DECLUTTER
#############################################

def test_declutter_leaves_well_separated_labels_alone():
    assert fp.declutter([10.0, 100.0, 200.0], min_gap=15.0) == [10.0, 100.0, 200.0]


def test_declutter_pushes_overlapping_labels_apart():
    out = fp.declutter([100.0, 102.0, 104.0], min_gap=15.0)
    gaps = [b - a for a, b in zip(sorted(out), sorted(out)[1:])]
    assert all(g >= 15.0 - 1e-9 for g in gaps)


def test_declutter_preserves_order():
    """A label that swapped past its neighbour would point at the wrong line —
    worse than one sitting slightly off its own."""
    ys = [100.0, 101.0, 102.0, 103.0]
    out = fp.declutter(ys, min_gap=20.0)
    assert sorted(range(len(ys)), key=lambda i: ys[i]) == \
        sorted(range(len(out)), key=lambda i: out[i])


def test_declutter_pulls_the_block_up_when_it_overflows_the_bottom():
    out = fp.declutter([480.0, 482.0, 484.0], min_gap=20.0, lo=48.0, hi=500.0)
    assert max(out) <= 500.0 + 1e-9


def test_declutter_is_empty_for_no_labels():
    assert fp.declutter([]) == []


#############################################
# FLOW FIELD — series
#############################################

def test_field_series_indexes_every_symbol_into_the_shared_timeline():
    """The symbols do not share a clock; plotting each against its own row index
    would put one name's 09:15 above another's 08:30."""
    model = fp.field_series({"SPY": [(1, 1.0), (2, 2.0)], "QQQ": [(2, 5.0)]},
                            ["SPY", "QQQ"])
    assert model["times"] == [1, 2]
    qqq = [line for line in model["lines"] if line["k"] == "QQQ"][0]
    assert qqq["v"] == [None, 5.0]          # starts further along the axis


def test_field_series_drops_a_symbol_with_nothing_to_plot():
    model = fp.field_series({"SPY": [(1, 1.0)], "QQQ": []}, ["SPY", "QQQ"])
    assert [line["k"] for line in model["lines"]] == ["SPY"]


def test_field_series_follows_the_caller_order():
    model = fp.field_series({"SPY": [(1, 1.0)], "QQQ": [(1, 2.0)]},
                            ["QQQ", "SPY"])
    assert [line["k"] for line in model["lines"]] == ["QQQ", "SPY"]


def test_field_geometry_always_straddles_zero():
    """The panel's claim is "call-led above the flat line", which is meaningless
    if zero is off-canvas."""
    geom = fp.field_geometry(fp.field_series({"SPY": [(1, 5.0), (2, 9.0)]},
                                             ["SPY"]))
    _x0, y0, _x1, y1 = fp.FLD_PLOT
    assert y0 <= geom["zero_y"] <= y1


def test_field_geometry_is_none_without_plottable_values():
    assert fp.field_geometry({"times": [], "lines": []}) is None
    assert fp.field_geometry({"times": [1], "lines": [{"k": "SPY", "v": [None]}]}) \
        is None


#############################################
# PANELS
#############################################

def test_divergence_panel_carries_every_id_the_scrub_writes_to():
    html, payload = fp.divergence_panel(_flow_rows(), _ladder_rows(
        [_T0 + i * 60 for i in range(1, 13)]), "SPY", "0DTE", "fxd")
    for node in ("root", "hit", "cur", "dspot", "dcall", "dput", "cspot",
                 "ccall", "cput", "cnet", "time", "rspot", "rcall", "rput",
                 "rnet", "bar", "lad", "ladtime"):
        assert f'id="fxd-{node}"' in html, node
    assert payload is not None


def test_divergence_payload_coordinates_match_the_drawn_geometry():
    """The cursor dots are placed from the payload; deriving the scales again in
    JS would be two implementations of one mapping, and the first symptom of
    their drifting is a dot sitting off its own line."""
    rows = _flow_rows()
    _html, payload = fp.divergence_panel(rows, [], "SPY", "0DTE", "fxd")
    geom = fp.divergence_geometry(fp.divergence_series(rows))
    assert payload["xs"] == [round(x, 1) for x in geom["xs"]]
    assert payload["yCall"] == [round(y, 1) for y in geom["y_call"]]
    assert payload["yPut"] == [round(y, 1) for y in geom["y_put"]]


def test_divergence_payload_defaults_to_the_latest_reading():
    _html, payload = fp.divergence_panel(_flow_rows(), [], "SPY", "0DTE", "fxd")
    assert payload["def"] == payload["n"] - 1


def test_divergence_panel_says_so_when_no_ladder_was_collected():
    html, payload = fp.divergence_panel(_flow_rows(), [], "SPY", "0DTE", "fxd")
    assert fp.LADDER_EMPTY in html
    assert payload["lad"] is None
    assert 'id="fxd-lad"' not in html      # no empty frame pretending to be one


def test_divergence_panel_empty_state_keeps_the_frame():
    """An empty view must read as "nothing to show yet", not as a page that
    failed to render."""
    html, payload = fp.divergence_panel([], [], "SPY", "0DTE", "fxd")
    assert payload is None
    assert "PREMIUM DIVERGENCE" in html and "fx-panel" in html


def test_divergence_panel_shows_the_symbol_and_tenor():
    html, _ = fp.divergence_panel(_flow_rows(), [], "$NDX", "0DTE", "fxd")
    assert "$NDX · 0DTE · SESSION" in html


def test_field_panel_carries_a_row_and_chip_per_symbol():
    rows = {"SPY": [(_T0 + i, float(i)) for i in range(1, 6)],
            "QQQ": [(_T0 + i, -float(i)) for i in range(1, 6)]}
    html, payload = fp.field_panel(rows, ["SPY", "QQQ"],
                                   {"SPY": "#4dd0e1", "QQQ": "#b388ff"},
                                   "Dollars ($M)", "fxf")
    for i in range(2):
        for node in (f"c{i}", f"v{i}", f"b{i}", f"row{i}", f"d{i}"):
            assert f'id="fxf-{node}"' in html, node
    assert 'id="fxf-board"' in html
    assert [line["k"] for line in payload["lines"]] == ["SPY", "QQQ"]


def test_field_panel_uses_the_per_symbol_colour_map():
    rows = {"SPY": [(_T0, 1.0), (_T0 + 60, 2.0)]}
    html, payload = fp.field_panel(rows, ["SPY"], {"SPY": "#4dd0e1"},
                                   "Dollars ($M)", "fxf")
    assert "#4dd0e1" in html
    assert payload["lines"][0]["c"] == "#4dd0e1"


def test_field_panel_falls_back_for_an_unmapped_symbol():
    rows = {"ZZZ": [(_T0, 1.0), (_T0 + 60, 2.0)]}
    _html, payload = fp.field_panel(rows, ["ZZZ"], {}, "Dollars ($M)", "fxf")
    assert payload["lines"][0]["c"] == fp.C["label"]


def test_field_panel_pill_agrees_with_the_plotted_count():
    rows = {"SPY": [(_T0, 1.0), (_T0 + 60, 2.0)]}
    html, _ = fp.field_panel(rows, ["SPY"], {}, "Dollars ($M)", "fxf")
    assert "LIVE · 1 SYMBOL<" in html          # singular, no stray "S"


def test_field_panel_scale_is_a_badge_not_a_toggle():
    """The real control is the NiceGUI select above the panel; a second,
    non-functional pair of buttons in here would read as clickable."""
    rows = {"SPY": [(_T0, 1.0), (_T0 + 60, 2.0)]}
    html, _ = fp.field_panel(rows, ["SPY"], {}, "Skew %", "fxf")
    assert "SKEW %" in html
    assert "PERCENTILE" not in html and "DOLLARS" not in html


def test_field_panel_empty_state():
    html, payload = fp.field_panel({}, [], {}, "Dollars ($M)", "fxf")
    assert payload is None and "FLOW FIELD" in html


#############################################
# SCRUB SCRIPT
#############################################

def test_scrub_js_inlines_the_payload_and_leaves_no_placeholders():
    _html, payload = fp.divergence_panel(_flow_rows(), [], "SPY", "0DTE", "fxd")
    js = fp.scrub_js("fxd", "div", payload)
    assert not [t for t in ("__ID__", "__KIND__", "__DATA__") if t in js]
    assert '"fxd"' in js and '"div"' in js


def test_scrub_js_is_empty_without_a_payload():
    """So the caller can hand the result straight to ui.run_javascript."""
    assert fp.scrub_js("fxd", "div", None) == ""


def test_scrub_js_kind_is_one_of_two_known_values():
    _html, payload = fp.field_panel({"SPY": [(_T0, 1.0), (_T0 + 60, 2.0)]},
                                    ["SPY"], {}, "Dollars ($M)", "fxf")
    assert '"field"' in fp.scrub_js("fxf", "field", payload)
    assert '"div"' in fp.scrub_js("fxf", "anythingelse", payload)


def test_scrub_payload_is_json_serialisable():
    _html, payload = fp.divergence_panel(
        _flow_rows(), _ladder_rows([_T0 + i * 60 for i in range(1, 13)]),
        "SPY", "0DTE", "fxd")
    assert json.loads(json.dumps(payload)) == payload


#############################################
# SANITIZER GUARD
#############################################

def _dompurify_allowlist():
    """The tag/attribute names DOMPurify will keep, read out of the copy NiceGUI
    actually ships. Mirrors test_rings.py — see that module for the full
    reasoning, including why runs containing ``script`` are dropped (they are
    DENY lists, and unioning them in would bless the names it strips)."""
    import pathlib

    from nicegui import ui
    src = (pathlib.Path(ui.__file__).parent / "static" / "dompurify.mjs") \
        .read_text(encoding="utf-8", errors="replace")
    names = set()
    for run in re.findall(r'(?:"[a-z][a-z0-9-]*",){19,}"[a-z][a-z0-9-]*"', src):
        tokens = set(re.findall(r'"([a-z][a-z0-9-]*)"', run))
        if "script" in tokens:
            continue
        names |= tokens
    assert len(names) > 300, "allowlist extraction found too little — bundle changed?"
    assert not ({"script", "foreignobject", "use", "animate"} & names)
    return names


@pytest.mark.parametrize("build", [
    lambda: fp.divergence_panel(
        _flow_rows(), _ladder_rows([_T0 + i * 60 for i in range(1, 13)]),
        "SPY", "0DTE", "fxd")[0],
    lambda: fp.divergence_panel([], [], "SPY", "0DTE", "fxd")[0],
    lambda: fp.field_panel({"SPY": [(_T0, 1.0), (_T0 + 60, -2.0)],
                            "QQQ": [(_T0, -1.0), (_T0 + 60, 3.0)]},
                           ["SPY", "QQQ"], {"SPY": "#4dd0e1"},
                           "Dollars ($M)", "fxf")[0],
    lambda: fp.field_panel({}, [], {}, "Dollars ($M)", "fxf")[0],
])
def test_panels_emit_nothing_dompurify_would_strip(build):
    """A stripped attribute changes NOTHING server-side, so the string stays
    correct and the page still renders — just wrong. It cost a real defect on
    the sentiment rings (``dominant-baseline``), which is why this guards the
    whole surface rather than that one name."""
    allow = _dompurify_allowlist()
    out = build()
    tags = set(re.findall(r"<([a-zA-Z][\w-]*)", out))
    attrs = set(re.findall(r'([a-zA-Z][\w-]*)="', out))
    stripped = sorted(n for n in tags | attrs if n.lower() not in allow)
    assert not stripped, f"DOMPurify would strip: {stripped}"
    # Non-vacuity: we really did parse a panel, and really did check attributes.
    assert {"div", "span", "style"} <= tags | attrs


def test_svg_text_is_centred_by_a_dy_shift_not_dominant_baseline():
    """Pins the FIX, not just the absence of the bug: dominant-baseline is
    stripped by DOMPurify, and dy is em-relative so one constant serves every
    font size on the panel."""
    html, _ = fp.divergence_panel(_flow_rows(), [], "SPY", "0DTE", "fxd")
    assert "dominant-baseline" not in html
    assert html.count(f'dy="{fp._BASELINE_DY}"') == html.count("<text ")


def test_panels_use_no_data_attributes():
    """DOMPurify's ALLOW_DATA_ATTR default cannot be read off the name lists, so
    the guard above cannot vouch for a data-* attribute. Avoid them entirely."""
    for build in (lambda: fp.divergence_panel(_flow_rows(), [], "S", "0DTE", "d")[0],
                  lambda: fp.field_panel({"SPY": [(_T0, 1.0), (_T0 + 60, 2.0)]},
                                         ["SPY"], {}, "Dollars ($M)", "f")[0]):
        assert not re.search(r'\bdata-[\w-]+=', build())
