"""Pure-transform tests for the RRG plot (/sentiment/rrg).

The plot is hand-drawn (absolutely-positioned markers over an SVG tail layer)
rather than a chart library, so its geometry is ordinary arithmetic and belongs
here rather than in a browser.
"""
import re

import pytest
from pages import rrg_view as R


def _sec(etf, ratio, mom, quad, tail=None, direction="INTO"):
    return {"name": etf, "etf": etf, "rs_ratio": ratio, "rs_momentum": mom,
            "quadrant": quad, "direction": direction,
            "tail": [{"rs_ratio": r, "rs_momentum": m} for r, m in (tail or [])]}


def _sectors():
    # XLU's real 2026-08-17 tail, trimmed to the last five readings.
    return [
        _sec("XLU", 100.14, 102.98, "Leading",
             [(97.28, 96.93), (97.82, 97.68), (98.41, 99.69),
              (99.62, 101.39), (100.14, 102.98)]),
        _sec("XLK", 100.39, 99.71, "Weakening",
             [(100.9, 101.2), (100.8, 100.9), (100.6, 100.4),
              (100.5, 100.0), (100.39, 99.71)], direction="FROM"),
        _sec("XLY", 99.42, 97.27, "Lagging", direction="FROM"),
    ]


# ── the tail ─────────────────────────────────────────────────────────────────
def test_tail_takes_only_the_latest_five_readings():
    long_tail = _sec("X", 100.0, 100.0, "Leading",
                     [(90.0 + i, 90.0 + i) for i in range(12)])
    pts = R.tail_points(long_tail)
    assert len(pts) == R.TAIL_POINTS == 5
    assert pts[-1] == (101.0, 101.0)          # the newest reading
    assert pts[0] == (97.0, 97.0)             # five back, not twelve


def test_the_tails_last_point_is_the_sectors_current_position():
    # The engine writes the head as the final tail sample; the plot relies on
    # that so the trail actually ends at the marker instead of near it.
    s = _sectors()[0]
    assert R.tail_points(s)[-1] == (s["rs_ratio"], s["rs_momentum"])


def test_a_sector_with_no_tail_falls_back_to_its_head_alone():
    pts = R.tail_points(_sec("XLY", 99.42, 97.27, "Lagging"))
    assert pts == [(99.42, 97.27)]


def test_a_short_tail_is_used_whole_rather_than_padded():
    s = _sec("X", 100.0, 100.0, "Leading", [(99.0, 99.0), (100.0, 100.0)])
    assert R.tail_points(s) == [(99.0, 99.0), (100.0, 100.0)]


def test_tail_readings_that_are_not_numbers_are_dropped():
    s = _sec("X", 100.0, 100.0, "Leading")
    s["tail"] = [{"rs_ratio": None, "rs_momentum": 99.0},
                 {"rs_ratio": 99.5, "rs_momentum": 99.5},
                 {"rs_ratio": 100.0, "rs_momentum": 100.0}]
    assert R.tail_points(s) == [(99.5, 99.5), (100.0, 100.0)]


# ── the domain ───────────────────────────────────────────────────────────────
def test_domain_stays_symmetric_about_one_hundred():
    # The quadrant crosshair is drawn at 50%/50%, so an asymmetric domain would
    # put the axes somewhere other than RS-Ratio 100 / RS-Mom 100 and silently
    # reassign every sector's quadrant on screen.
    d = R.domain(_sectors())
    assert d["x_lo"] + d["x_hi"] == pytest.approx(200.0)
    assert d["y_lo"] + d["y_hi"] == pytest.approx(200.0)


def test_domain_contains_every_tail_point_not_just_the_heads():
    # The reference design hard-codes 98.9..101.1 on x; the real five-reading
    # tails reach 97.28, which that window would clip clean off the plot.
    d = R.domain(_sectors())
    for s in _sectors():
        for x, y in R.tail_points(s):
            assert d["x_lo"] <= x <= d["x_hi"], (s["etf"], x)
            assert d["y_lo"] <= y <= d["y_hi"], (s["etf"], y)


def test_domain_never_shrinks_below_the_designs_window():
    quiet = [_sec("A", 100.01, 100.01, "Leading")]
    d = R.domain(quiet)
    assert d["x_hi"] - 100 == pytest.approx(R.MIN_HALF_X)
    assert d["y_hi"] - 100 == pytest.approx(R.MIN_HALF_Y)


def test_domain_pads_beyond_the_extremes_so_markers_are_not_cut_by_the_edge():
    d = R.domain(_sectors())
    assert d["x_lo"] < 97.28 and d["y_lo"] < 96.93


def test_domain_of_nothing_is_the_default_window():
    d = R.domain([])
    assert d["x_lo"] == pytest.approx(100 - R.MIN_HALF_X)
    assert d["y_hi"] == pytest.approx(100 + R.MIN_HALF_Y)


# ── projection ───────────────────────────────────────────────────────────────
def test_x_projects_low_to_left_and_high_to_right():
    assert R.px(99.0, 99.0, 101.0) == pytest.approx(0.0)
    assert R.px(100.0, 99.0, 101.0) == pytest.approx(50.0)
    assert R.px(101.0, 99.0, 101.0) == pytest.approx(100.0)


def test_y_is_inverted_so_higher_momentum_sits_higher_on_screen():
    assert R.py(103.0, 97.0, 103.0) == pytest.approx(0.0)
    assert R.py(100.0, 97.0, 103.0) == pytest.approx(50.0)
    assert R.py(97.0, 97.0, 103.0) == pytest.approx(100.0)


def test_one_hundred_lands_on_the_crosshair_for_any_symmetric_domain():
    d = R.domain(_sectors())
    assert R.px(100.0, d["x_lo"], d["x_hi"]) == pytest.approx(50.0)
    assert R.py(100.0, d["y_lo"], d["y_hi"]) == pytest.approx(50.0)


def test_projection_of_a_degenerate_domain_does_not_divide_by_zero():
    assert R.px(100.0, 100.0, 100.0) == pytest.approx(50.0)
    assert R.py(100.0, 100.0, 100.0) == pytest.approx(50.0)


# ── ticks ────────────────────────────────────────────────────────────────────
def test_ticks_land_inside_the_domain_and_include_the_centre():
    d = R.domain(_sectors())
    xs = R.ticks(d["x_lo"], d["x_hi"])
    assert xs and all(d["x_lo"] <= t <= d["x_hi"] for t in xs)
    assert any(abs(t - 100.0) < 1e-9 for t in xs)


def test_ticks_are_evenly_spaced_and_ascending():
    xs = R.ticks(97.0, 103.0)
    gaps = {round(b - a, 6) for a, b in zip(xs, xs[1:])}
    assert len(gaps) == 1
    assert xs == sorted(xs)


def test_tick_count_stays_readable_across_wildly_different_spans():
    for lo, hi in ((99.0, 101.0), (97.0, 103.0), (80.0, 120.0), (99.9, 100.1)):
        n = len(R.ticks(lo, hi))
        assert 3 <= n <= 12, (lo, hi, n)


def test_a_degenerate_span_still_yields_the_centre_tick():
    assert R.ticks(100.0, 100.0) == [100.0]


# ── markers ──────────────────────────────────────────────────────────────────
def test_marker_area_tracks_index_weight():
    # Diameter goes as sqrt(weight), so AREA is proportional to weight — the
    # only encoding that reads honestly as "share of the index".
    small, big = R.marker_px(2.1), R.marker_px(32.5)
    assert big > small
    assert (big - R.MARKER_BASE) / (small - R.MARKER_BASE) == \
        pytest.approx((32.5 ** 0.5) / (2.1 ** 0.5), rel=1e-6)


def test_marker_has_a_floor_so_a_weightless_sector_is_still_visible():
    assert R.marker_px(0) == R.MARKER_BASE
    assert R.marker_px(None) == R.MARKER_BASE


def test_plot_points_carry_position_size_and_a_quadrant_palette():
    pts = R.plot_points(_sectors(), {"XLU": 2.1, "XLK": 32.5, "XLY": 9.9},
                        R.domain(_sectors()))
    by = {p["etf"]: p for p in pts}
    assert set(by) == {"XLU", "XLK", "XLY"}
    xlk = by["XLK"]
    assert 0 <= xlk["x_pct"] <= 100 and 0 <= xlk["y_pct"] <= 100
    assert xlk["size_px"] > by["XLU"]["size_px"]        # 32.5% vs 2.1%
    for role in ("dot", "halo", "label"):
        assert role in xlk["classes"]


def test_marker_halo_is_one_shadow_in_rgba_with_no_spaces():
    # A `shadow-[…]` arbitrary does not generate from a hex, and a Tailwind
    # arbitrary value cannot contain a space — both documented JIT gotchas.
    halo = R.QUAD_MARKER["Leading"]["halo"]
    assert halo.startswith("shadow-[") and halo.endswith("]")
    assert " " not in halo and "#" not in halo
    assert halo.count("rgba(") == 2                    # panel cut, then the ring


def test_a_sector_missing_coordinates_is_dropped_rather_than_drawn_at_zero():
    bad = [_sec("XLU", 100.1, 103.0, "Leading"), _sec("NA", None, None, "Leading")]
    assert [p["etf"] for p in R.plot_points(bad, {}, R.domain(bad))] == ["XLU"]


# ── label placement ──────────────────────────────────────────────────────────
def test_labels_carry_the_sector_name_not_the_ticker():
    secs = [{"name": "Real Estate", "etf": "XLRE", "rs_ratio": 100.0,
             "rs_momentum": 100.0, "quadrant": "Leading"}]
    p = R.plot_points(secs, {}, R.domain(secs))[0]
    assert p["label"] == "Real Estate"
    assert p["etf"] == "XLRE"          # kept, for the palette and weights lookup


def test_a_sector_with_no_name_falls_back_to_its_ticker():
    secs = [{"etf": "XLRE", "rs_ratio": 100.0, "rs_momentum": 100.0,
             "quadrant": "Leading"}]
    assert R.plot_points(secs, {}, R.domain(secs))[0]["label"] == "XLRE"


def test_labels_flip_to_the_left_when_they_would_hang_off_the_plot():
    # The side decision MEASURES the label, because a sector name is roughly
    # four times a ticker: "Communication" to the right of a marker at 70%
    # would run off the edge where "XLC" would have fitted.
    d = {"x_lo": 99.0, "x_hi": 101.0, "y_lo": 99.0, "y_hi": 101.0}
    pts = R.plot_points([
        {"name": "Communication", "etf": "XLC", "rs_ratio": 100.9,
         "rs_momentum": 100.0, "quadrant": "Leading"},
        {"name": "Energy", "etf": "XLE", "rs_ratio": 99.1,
         "rs_momentum": 100.0, "quadrant": "Leading"}], {}, d)
    by = {p["etf"]: p for p in pts}
    assert by["XLC"]["anchor"] == "right" and by["XLC"]["dx"] < 0
    assert by["XLE"]["anchor"] == "left" and by["XLE"]["dx"] > 0


def test_a_long_name_flips_where_a_short_one_at_the_same_spot_would_not():
    d = {"x_lo": 99.0, "x_hi": 101.0, "y_lo": 99.0, "y_hi": 101.0}
    spot = {"rs_ratio": 100.5, "rs_momentum": 100.0, "quadrant": "Leading"}
    long_ = R.plot_points([{**spot, "name": "Consumer Discretionary", "etf": "A"}],
                          {}, d)[0]
    short = R.plot_points([{**spot, "name": "Energy", "etf": "B"}], {}, d)[0]
    assert long_["anchor"] == "right"
    assert short["anchor"] == "left"


def test_label_width_grows_with_the_name():
    assert R.label_width_px("Communication") > R.label_width_px("Energy")
    assert R.label_width_px("") == 0
    assert R.label_width_px(None) == 0


def test_labels_stacked_on_top_of_each_other_are_pushed_apart():
    # Eleven sectors cluster hard around 100; without decluttering their ETF
    # codes overprint into an unreadable smear.
    same = [_sec(f"S{i}", 99.5, 100.0, "Leading") for i in range(4)]
    d = R.domain(same)
    dys = sorted(p["dy"] for p in R.plot_points(same, {}, d))
    assert len(set(dys)) == 4
    assert all(b - a >= R.LABEL_MIN_GAP_PX - 1e-6 for a, b in zip(dys, dys[1:]))


def test_labels_that_are_already_clear_are_left_alone():
    spread = [_sec("A", 99.5, 103.0, "Leading"), _sec("B", 99.5, 97.0, "Lagging")]
    assert all(p["dy"] == 0 for p in R.plot_points(spread, {}, R.domain(spread)))


def test_decluttering_is_per_side_so_a_left_label_cannot_push_a_right_one():
    d = {"x_lo": 99.0, "x_hi": 101.0, "y_lo": 99.0, "y_hi": 101.0}
    pts = R.plot_points([
        {"name": "Consumer Discretionary", "etf": "RGT", "rs_ratio": 100.9,
         "rs_momentum": 100.0, "quadrant": "Leading"},
        {"name": "Energy", "etf": "LFT", "rs_ratio": 99.01,
         "rs_momentum": 100.0, "quadrant": "Leading"}], {}, d)
    by = {p["etf"]: p for p in pts}
    assert by["RGT"]["anchor"] != by["LFT"]["anchor"]     # opposite sides …
    assert all(p["dy"] == 0 for p in pts)                # … so neither moves


# ── the tail layer ───────────────────────────────────────────────────────────
def test_tail_segments_form_one_unbroken_polyline():
    segs = R.tail_segments(_sectors()[:1], R.domain(_sectors()))
    # 4 spans, each resampled — many more segments than readings, all joined.
    assert len(segs) == (R.TAIL_POINTS - 1) * R.SMOOTH_SAMPLES
    for a, b in zip(segs, segs[1:]):
        assert a["x2"] == b["x1"] and a["y2"] == b["y1"]


def test_a_tail_fades_and_thins_toward_its_oldest_reading():
    segs = R.tail_segments(_sectors()[:1], R.domain(_sectors()))
    assert [s["opacity"] for s in segs] == sorted(s["opacity"] for s in segs)
    assert [s["width"] for s in segs] == sorted(s["width"] for s in segs)


def test_smoothing_passes_through_every_real_reading():
    """The curve may choose the route between readings; it may not move them."""
    pts = [(99.0, 99.0), (99.5, 100.5), (100.0, 100.0), (100.5, 101.5)]
    out = R.smooth_tail(pts)
    for p in pts:
        assert any(abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9
                   for q in out), p
    assert out[0] == pts[0] and out[-1] == pts[-1]


def test_smoothing_adds_intermediate_points_between_every_reading():
    pts = [(99.0, 99.0), (99.5, 100.5), (100.0, 100.0)]
    out = R.smooth_tail(pts)
    assert len(out) == 1 + (len(pts) - 1) * R.SMOOTH_SAMPLES


def test_smoothing_actually_bends_the_path():
    # A corner in the raw readings must come out rounded, i.e. the resampled
    # point nearest the corner should sit off the original polyline.
    pts = [(99.0, 100.0), (100.0, 100.0), (100.0, 101.0)]
    out = R.smooth_tail(pts)
    assert any(abs(y - 100.0) > 1e-6 and 99.0 < x < 100.0 for x, y in out)


def test_smoothing_stays_within_the_readings_bounding_box():
    # A spline that overshoots is claiming the sector visited a position it
    # never held, so the tension is set to keep the curve tight.
    pts = [(99.0, 99.0), (99.4, 100.6), (100.1, 99.4), (100.6, 101.0)]
    out = R.smooth_tail(pts)
    pad = 0.25
    assert min(x for x, _ in out) >= min(p[0] for p in pts) - pad
    assert max(x for x, _ in out) <= max(p[0] for p in pts) + pad
    assert min(y for _, y in out) >= min(p[1] for p in pts) - pad
    assert max(y for _, y in out) <= max(p[1] for p in pts) + pad


def test_smoothing_a_two_point_or_shorter_tail_is_a_no_op():
    assert R.smooth_tail([(99.0, 99.0), (100.0, 100.0)]) ==         [(99.0, 99.0), (100.0, 100.0)]
    assert R.smooth_tail([(99.0, 99.0)]) == [(99.0, 99.0)]
    assert R.smooth_tail([]) == []


def test_taper_is_continuous_rather_than_stepping_once_per_reading():
    segs = R.tail_segments(_sectors()[:1], R.domain(_sectors()))
    widths = [s["width"] for s in segs]
    # Every sub-segment differs from its neighbour: a stepped taper would show
    # only as many distinct widths as there are readings.
    assert len(set(round(w, 6) for w in widths)) == len(widths)
    assert widths[0] == pytest.approx(R.TAIL_W[0], abs=0.05)
    assert widths[-1] == pytest.approx(R.TAIL_W[1])


def test_a_sector_with_no_tail_contributes_no_segments():
    only_head = [_sec("XLY", 99.42, 97.27, "Lagging")]
    assert R.tail_segments(only_head, R.domain(only_head)) == []


def test_tail_svg_uses_percentage_coordinates_not_a_scaled_viewbox():
    """The reference scales a 0-100 viewBox with preserveAspectRatio="none" and
    leans on ``vector-effect:non-scaling-stroke`` to keep the stroke even. That
    attribute is NOT in DOMPurify's allowlist, so ``ui.html`` would strip it and
    every tail would render stretched — thick horizontally, hairline
    vertically. Percentage coordinates need no viewBox and no rescue."""
    svg = R.tail_svg(_sectors(), R.domain(_sectors()))
    assert "viewBox" not in svg and "preserveAspectRatio" not in svg
    assert "vector-effect" not in svg
    assert re.search(r'x1="[\d.]+%"', svg)


def test_tail_svg_emits_nothing_dompurify_would_strip():
    """Same invariant the ring dial carries, and for the same reason: a stripped
    attribute changes nothing server-side, so the string stays correct and the
    page renders — just wrong."""
    from test_rings import _dompurify_allowlist
    allow = _dompurify_allowlist()
    svg = R.tail_svg(_sectors(), R.domain(_sectors()))
    tags = set(re.findall(r"<([a-zA-Z][\w-]*)", svg))
    attrs = set(re.findall(r'([a-zA-Z][\w-]*)="', svg))
    stripped = sorted(n for n in tags | attrs if n.lower() not in allow)
    assert not stripped, f"DOMPurify would strip: {stripped}"
    assert {"svg", "line"} <= tags
    assert {"stroke", "stroke-width", "opacity"} <= attrs


def test_tail_svg_with_no_sectors_is_still_a_valid_empty_layer():
    svg = R.tail_svg([], R.domain([]))
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<line" not in svg


# ── the alert strip ──────────────────────────────────────────────────────────
def test_alert_bar_states_regime_sentence_and_the_arithmetic():
    bar = R.alert_bar({"regime": "Risk-OFF", "cyclical_mom_mean": 100.49,
                       "defensive_mom_mean": 102.00, "spread": -1.51}, 1.50)
    assert bar["word"] == "Risk-off rotation"
    assert bar["sentence"] == "Money is moving into defensives and out of cyclicals."
    assert bar["stats"] == "cyclical 100.49 · defensive 102.00 · spread −1.51 vs ±1.50"
    assert bar["tone"] == "down"


def test_alert_bar_flips_for_risk_on():
    bar = R.alert_bar({"regime": "Risk-ON", "spread": 1.8}, 1.50)
    assert bar["word"] == "Risk-on rotation" and bar["tone"] == "up"
    assert "into cyclicals" in bar["sentence"]


def test_alert_bar_on_a_cold_cache_admits_it():
    bar = R.alert_bar({}, 1.50)
    assert bar["tone"] == "flat"
    assert "—" in bar["stats"]


# ── quadrant styling ─────────────────────────────────────────────────────────
def test_every_quadrant_has_a_wash_and_a_corner_label_class():
    for q in R.QUADRANT_CORNERS:
        assert q in R.QUAD_WASH and q in R.QUAD_CORNER_TXT
        assert R.QUAD_WASH[q].startswith("bg-[#")


def test_corners_are_placed_the_way_an_rrg_is_read():
    assert R.QUADRANT_CORNERS == {
        "Improving": "top-left", "Leading": "top-right",
        "Lagging": "bottom-left", "Weakening": "bottom-right"}
