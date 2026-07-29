"""Tests for the Gamma page pure figure/transform builders + Tier-3 wiring.

The engine work (live chain fetch + GammaEngine compute + history grids + the
Explain/Analyze text) moved to ``services/options_svc/compute`` — see that
service's tests. The page now reads a cached snapshot from the Redis bus and
drives refresh/explain/analyze via commands, so it must import NO engine / proxy
code. The pure figure/transform builders below stay unchanged + unit-tested.
"""
import inspect
import json

import bus_client
from pages.options import gamma

GEX = {"spot": 450.0, "gex": {
    448.0: {"call": 100.0, "put": -40.0, "net": 60.0},
    450.0: {"call": 200.0, "put": -250.0, "net": -50.0},
    452.0: {"call": 30.0, "put": -10.0, "net": 20.0},
}, "strike_count": 3}


def test_bars_from_gex_filters_band_and_sorts():
    b = gamma.bars_from_gex(GEX, 450.0)
    assert b["strikes"] == [448.0, 450.0, 452.0]
    assert b["nets"] == [60.0, -50.0, 20.0]
    assert b["colors"][1] != b["colors"][0]


def test_strikes_around_fixed_count_each_side():
    # A FIXED count each side (not a ±% band) → consistent bar/cell size all day.
    strikes = [float(s) for s in range(100, 201, 5)]   # 100,105,...,200 (21 strikes)
    w = gamma.strikes_around(strikes, 150.0, n_side=3)
    assert w == [135.0, 140.0, 145.0, 150.0, 155.0, 160.0, 165.0]  # 3 ≤spot + 3 >spot
    # Lower-priced / sparse names just get what exists (smaller window, no error).
    assert gamma.strikes_around([10.0, 12.5, 15.0], 12.5, n_side=20) == [10.0, 12.5, 15.0]


def test_bars_from_gex_limits_to_n_side_window():
    big = {"spot": 150.0, "gex": {float(s): {"net": 1.0} for s in range(100, 201, 5)}}
    b = gamma.bars_from_gex(big, 150.0, n_side=2)
    assert b["strikes"] == [140.0, 145.0, 150.0, 155.0, 160.0]   # 2 each side + spot


def test_bar_figure_is_highcharts_dict():
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", walls=[450.0], flip=449.5)
    assert "series" in fig and fig["chart"]["type"] == "bar"


def test_bar_figure_strike_axis_hugs_visible_bars_and_is_tall():
    # In a Highcharts bar chart the STRIKE axis is xAxis (vertical).
    # The window spans the bars (448..452), padded — not spot*0.95/1.05.
    fig = gamma.bar_figure(GEX, 450.0, view="GEX")
    lo, hi = fig["xAxis"]["min"], fig["xAxis"]["max"]
    assert lo > 440.0 and hi < 460.0          # tight to the bars, not spot*0.95/1.05
    assert lo <= 448.0 and hi >= 452.0        # outermost bars not clipped
    assert fig["chart"]["height"] >= 600      # fills the lower screen


def test_bar_yrange_empty_falls_back_to_spot_band():
    assert gamma.bar_yrange([], 450.0) == [450.0 * 0.98, 450.0 * 1.02]


# ── spot=None resilience (regression: weekend/off-hours snapshots can have
#    spot=None → the near-spot band math must not raise NoneType*float) ─────────
def test_bars_from_gex_none_spot_returns_empty():
    b = gamma.bars_from_gex(GEX, None)
    assert b == {"strikes": [], "nets": [], "colors": [], "hovers": []}


def test_bar_yrange_none_spot_does_not_raise():
    assert isinstance(gamma.bar_yrange([], None), list)
    assert isinstance(gamma.bar_yrange([450.0], None), list)   # single strike, span 0


def test_bar_figure_none_spot_does_not_raise():
    fig = gamma.bar_figure(GEX, None, view="GEX")
    assert fig["chart"]["type"] == "bar" and "series" in fig


def test_render_no_crash_when_spot_missing():
    """Regression: a cached gamma snapshot with spot=None (e.g. market closed /
    sparse off-hours chain) must not 500 the page — the near-spot band math is
    skipped and a message is shown instead."""
    from nicegui import ui

    bus_client.reset()
    snap = {"symbol": "$SPX", "spot": None, "dte": 0,
            "views": {"GEX": {"data": {"spot": None, "strike_count": 0, "gex": {}},
                              "summary": {}, "walls": [], "flip": None, "history": []}},
            "term": {}}
    bus_client.bus().cache_set("cache:options:gamma", snap)
    with ui.card():
        gamma.render()  # must not raise


def test_panel_flex_endpoints_and_monotonic():
    bar0, heat0 = gamma.panel_flex(0)
    assert heat0 == 0.28 and round(bar0 + heat0, 4) == 1.0      # session start: bars wide
    barf, heatf = gamma.panel_flex(205)
    assert heatf == 0.70 and round(barf + heatf, 4) == 1.0      # full session: heat wide
    # clamps past a full session
    assert gamma.panel_flex(300) == gamma.panel_flex(205)
    # heat fraction is non-decreasing with more snapshots
    heats = [gamma.panel_flex(n)[1] for n in range(0, 210, 20)]
    assert heats == sorted(heats)
    # midpoint is between the endpoints
    _, heat_mid = gamma.panel_flex(102)
    assert 0.28 < heat_mid < 0.70




def test_heatmap_matrix_from_history():
    rows = [("09:30", 450, None, None, None, 0, {448.0: 5, 450.0: -3}),
            ("09:35", 450, None, None, None, 0, {448.0: 7, 450.0: -1})]
    m = gamma.heatmap_matrix(rows)
    assert m["x"] == ["09:30", "09:35"]
    assert 448.0 in m["y"] and 450.0 in m["y"]
    assert len(m["z"]) == len(m["y"]) and len(m["z"][0]) == 2


def test_heatmap_matrix_empty():
    assert gamma.heatmap_matrix([])["z"] == []


def test_heatmap_matrix_includes_spots():
    rows = [("09:30", 450.0, None, None, None, 0, {449.0: {"net": 5}}),
            ("09:35", 451.5, None, None, None, 0, {449.0: {"net": 7}})]
    assert gamma.heatmap_matrix(rows)["spots"] == [450.0, 451.5]


def test_heatmap_figure_overlays_spot_line():
    rows = [("09:30", 450.0, None, None, None, 0, {449.0: {"net": 5}}),
            ("09:35", 451.5, None, None, None, 0, {449.0: {"net": 7}})]
    fig = gamma.heatmap_figure(rows, "GEX")
    types = [s["type"] for s in fig["series"]]
    assert "heatmap" in types and "line" in types
    spot = next(s for s in fig["series"] if s["type"] == "line")
    assert [p[1] for p in spot["data"]] == [450.0, 451.5]   # y = spot prices
    assert [p[0] for p in spot["data"]] == [0, 1]            # x = time category index
    # Price line is a clean line on its own axis — no marker dots, no colorAxis.
    assert spot["marker"]["enabled"] is False
    assert spot["colorAxis"] is False


def test_heatmap_figure_crops_data_to_yrange():
    # Charm/DEX/Vanna are non-zero across the whole chain (~250 strikes) but the
    # panel only shows the near-spot yrange. Off-window cells must NOT be emitted
    # (perf: ~45k points → ~GEX-sized). Strikes 100/900 are out of [440,460].
    rows = [("09:30", 450.0, None, None, None, 0,
             {100.0: {"net": 7}, 450.0: {"net": 5}, 900.0: {"net": -9}})]
    fig = gamma.heatmap_figure(rows, "Charm", yrange=[440.0, 460.0])
    hm = next(s for s in fig["series"] if s["type"] == "heatmap")
    assert {p[1] for p in hm["data"]} == {450.0}      # only the in-window strike
    # color axis is clamped to the visible cell, not the off-window extreme (9)
    assert fig["colorAxis"]["max"] == 5


def test_heatmap_figure_hides_duplicate_strike_axis_and_flushes_margins():
    # The bar chart owns the Strike axis; the heatmap shares its y-RANGE (for
    # alignment) but hides its own strike labels/title, and runs edge-to-edge
    # (marginLeft/Right 0) so it butts against the bars and reaches the window edge.
    rows = [("09:30", 450.0, None, None, None, 0, {449.0: {"net": 5}})]
    fig = gamma.heatmap_figure(rows, "GEX", yrange=[440.0, 460.0])
    assert fig["yAxis"]["labels"]["enabled"] is False
    assert fig["yAxis"].get("title", {}).get("text") is None
    assert fig["yAxis"]["min"] == 440.0 and fig["yAxis"]["max"] == 460.0   # range kept
    assert fig["chart"]["marginLeft"] == 0 and fig["chart"]["marginRight"] == 0


def test_strike_step_uses_median_gap_not_min():
    # Mixed spacing (fine strikes near money among coarser ones): the row height
    # must be the MEDIAN gap (2.5) so cells fill the panel, NOT the min (1.0)
    # which leaves thin rows + dead space (the QCOM/SPCX bug).
    strikes = [100.0, 101.0, 102.0, 104.5, 107.0, 109.5, 112.0]  # gaps: 1,1,2.5,2.5,2.5,2.5
    assert gamma._strike_step(strikes) == 2.5
    assert gamma._strike_step([100.0, 105.0, 110.0]) == 5.0       # uniform → that gap
    assert gamma._strike_step([100.0]) == 1.0                     # no gaps → fallback


def test_heatmap_figure_rowsize_from_visible_window_median():
    # Off-window coarse strikes (10-apart) must not inflate rowsize; within the
    # window strikes are 2.5 apart → rowsize 2.5 (dense, $SPX-like).
    grid = {200.0: {"net": 5}, 202.5: {"net": -3}, 205.0: {"net": 4},
            207.5: {"net": -2}, 260.0: {"net": 9}, 320.0: {"net": -9}}
    rows = [("09:30", 203.0, None, None, None, 0, grid)]
    fig = gamma.heatmap_figure(rows, "GEX", yrange=[198.0, 210.0])
    hm = next(s for s in fig["series"] if s["type"] == "heatmap")
    assert hm["rowsize"] == 2.5


def test_heatmap_figure_no_yrange_keeps_all_strikes():
    rows = [("09:30", 450.0, None, None, None, 0, {100.0: {"net": 7}, 900.0: {"net": -9}})]
    fig = gamma.heatmap_figure(rows, "Charm")
    hm = next(s for s in fig["series"] if s["type"] == "heatmap")
    assert {p[1] for p in hm["data"]} == {100.0, 900.0}


def test_heatmap_figure_time_labels_not_clipped():
    # Time labels are dense; rotating them + a taller bottom margin keep them from
    # being cut off at the bottom edge.
    rows = [("09:30", 450.0, None, None, None, 0, {449.0: {"net": 5}})]
    fig = gamma.heatmap_figure(rows, "GEX")
    assert fig["xAxis"]["labels"]["rotation"] == -45
    assert fig["chart"]["marginBottom"] >= 60


def test_union_range_includes_values_with_padding():
    # Spot path 470..510 falls outside a [498, 512] strike window; the unioned
    # range must cover the full path so the heatmap price line isn't clipped.
    lo, hi = gamma.union_range([498.0, 512.0], [505.0, 470.0, 509.0])
    assert lo <= 470.0 and hi >= 512.0


def test_union_range_noop_without_values():
    assert gamma.union_range([498.0, 512.0], []) == [498.0, 512.0]
    assert gamma.union_range([498.0, 512.0], [None, "x"]) == [498.0, 512.0]


def test_heatmap_matrix_extracts_net_from_cell_dicts():
    # gex_history grids map strike -> {call, put, net}; z must be the net number.
    rows = [("09:30", 450, None, None, None, 0,
             {740.0: {"call": 1, "put": -3, "net": -2},
              9999.0: {"call": 0, "put": 0, "net": 0}})]
    m = gamma.heatmap_matrix(rows)
    assert 740.0 in m["y"]
    assert 9999.0 not in m["y"]          # all-zero-net strike filtered out
    assert m["z"][0][0] == -2            # net extracted, not the dict


def test_heatmap_matrix_formats_epoch_ts():
    rows = [(1718370600, 450, None, None, None, 0, {448.0: 5})]
    x = gamma.heatmap_matrix(rows)["x"][0]
    assert isinstance(x, str) and ":" in x


def test_term_heatmap_axes_and_zero_filter():
    tg = {"underlying_price": 450.0, "expirations": ["2026-06-18", "2026-06-19"],
          "cells": {"2026-06-18": {450.0: {"net_gex_usd": 5}},
                    "2026-06-19": {450.0: {"net_gex_usd": -3}, 451.0: {"net_gex_usd": 0}}}}
    fig = gamma.term_heatmap(tg)
    assert fig["xAxis"]["categories"] == ["2026-06-18", "2026-06-19"]
    assert "450" in fig["yAxis"]["categories"]
    assert "451" not in fig["yAxis"]["categories"]   # all-zero strike filtered out


def test_term_heatmap_empty():
    assert gamma.term_heatmap({})["series"][0]["data"] == []


def test_term_heatmap_handles_json_string_strike_keys():
    # Strike keys round-trip to STRINGS through Redis JSON. term_heatmap must
    # numeric-sort + label them (not crash on f"{s:g}", not sort lexically).
    tg = {"expirations": ["2026-06-18", "2026-06-19"],
          "cells": {"2026-06-18": {"450.0": {"net_gex_usd": 5}, "1000.0": {"net_gex_usd": 9}},
                    "2026-06-19": {"450.0": {"net_gex_usd": -3}, "451.0": {"net_gex_usd": 0}}}}
    fig = gamma.term_heatmap(tg)
    cats = fig["yAxis"]["categories"]
    assert cats == ["450", "1000"]          # numeric order, not lexical "1000" < "450"
    assert "451" not in cats                 # all-zero strike still filtered
    assert any(p[2] == 9 for p in fig["series"][0]["data"])   # net value looked up by re-floated key


def test_wrap_explain_fragment_and_document():
    body = "<h2>GAMMA EXPOSURE (GEX)</h2><p>hi</p>"
    frag = gamma.wrap_explain("$SPX", body, full=False)
    assert "gx-explain" in frag and body in frag and "$SPX" in frag
    assert not frag.lstrip().startswith("<!DOCTYPE")
    doc = gamma.wrap_explain("$SPX", body, full=True)
    assert doc.lstrip().startswith("<!DOCTYPE") and body in doc


def test_summary_text_keeps_net_and_flip_drops_spot_and_strikes():
    txt = gamma.summary_text({"spot": 450.0, "flip": 449.5, "net_total": 1234.0,
                              "strike_count": 3}, "GEX")
    assert "GEX" in txt and "449.5" in txt and "1,234" in txt   # flip + net kept
    assert "spot" not in txt and "strikes" not in txt           # low-value, dropped
    assert "450.00" not in txt                                  # spot value gone


def test_dex_hedge_suffix_variants():
    s = gamma.dex_hedge_suffix({"net_delta_0dte": 10, "projected_net_delta_close": 5,
                                "hedge_pressure": -5})
    assert "Net Δ 10" in s and "hedge -5" in s
    assert gamma.dex_hedge_suffix({"hedge_pressure": None}) == "hedge n/a (nearest expiry not 0-DTE)"
    assert gamma.dex_hedge_suffix(None).startswith("hedge n/a")


# ── Tier-3 wiring (Task 2.6d) ────────────────────────────────────────────────
def test_refloat_keys_casts_string_keys_to_float():
    out = gamma._refloat_keys({"448.0": {"net": 1}, "450.0": {"net": -2}})
    assert set(out) == {448.0, 450.0}
    assert all(isinstance(k, float) for k in out)
    assert out[448.0] == {"net": 1}


def test_refloat_keys_idempotent_and_tolerant():
    # Already-float keys pass through; non-castable keys are kept as-is.
    assert gamma._refloat_keys({450.0: 1}) == {450.0: 1}
    assert gamma._refloat_keys({"x": 1}) == {"x": 1}
    assert gamma._refloat_keys(None) == {}


def test_json_roundtrip_then_refloat_reproduces_bars():
    """A JSON round-trip stringifies float keys; re-floating restores correct bars.

    Proves the page's normalization keeps the pure ``bars_from_gex`` builder
    working over a Redis-stored (JSON) snapshot."""
    data = {"spot": 450.0, "gex": {
        448.0: {"call": 100.0, "put": -40.0, "net": 60.0},
        450.0: {"call": 200.0, "put": -250.0, "net": -50.0},
        452.0: {"call": 30.0, "put": -10.0, "net": 20.0},
    }}
    reloaded = json.loads(json.dumps(data))           # float keys -> strings
    assert all(isinstance(k, str) for k in reloaded["gex"])  # confirm stringified
    fixed = {"spot": reloaded["spot"], "gex": gamma._refloat_keys(reloaded["gex"])}
    b = gamma.bars_from_gex(fixed, 450.0)
    assert b["strikes"] == [448.0, 450.0, 452.0]      # numeric, sorted, in range
    assert b["nets"] == [60.0, -50.0, 20.0]


# ── Charts refresh (2026-06-16): dark theme, beveled bars, line labels,
#    relabeled views, heatmap separators/contrast ──────────────────────────────
def test_view_label_renames_gex_and_dex():
    assert gamma._view_label("GEX") == "GAMMA"
    assert gamma._view_label("DEX") == "DELTA"
    assert gamma._view_label("Charm") == "Charm"
    assert gamma._view_label("Term") == "Term"


def test_base_chart_sets_dark_background():
    fig = gamma._base_chart("bar", 680)
    assert fig["chart"]["backgroundColor"] == gamma.DARK_BG
    assert fig["accessibility"]["enabled"] is False
    assert gamma._dark_axis("x")["title"]["text"] == "x"
    assert gamma._dark_axis()["gridLineColor"] == gamma.GRID


def test_bar_figure_is_transparent_and_beveled_with_friendly_title():
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", walls=[452.0], flip=449.5)
    # Transparent so the page background shows through (matches the heatmap panel).
    assert fig["chart"]["backgroundColor"] == "transparent"
    pt = fig["series"][0]["data"][0]
    assert pt["borderWidth"] >= 1 and pt["borderColor"]        # beveled per-bar border
    assert pt["borderColor"] != pt["color"]                    # darker shade
    assert "GAMMA" in fig["title"]["text"]                     # friendly label, not "GEX"


def test_line_annotations_label_spot_flip_and_call_put_walls():
    anns = gamma.line_annotations(450.0, 449.0, [455.0, 445.0])
    texts = [a["text"] for a in anns]
    assert any(t.startswith("Spot") for t in texts)
    assert any("Gamma flip" in t for t in texts)
    assert any("Call wall" in t for t in texts)   # 455 >= spot
    assert any("Put wall" in t for t in texts)    # 445 < spot


def test_bar_figure_includes_reference_line_plotlines():
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", walls=[452.0], flip=449.5)
    texts = [pl["label"]["text"] for pl in fig["xAxis"]["plotLines"]]
    assert any(t.startswith("Spot") for t in texts)
    assert any("Gamma flip" in t for t in texts)


def test_bar_figure_accepts_explicit_yrange():
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", yrange=[400.0, 500.0])
    assert [fig["xAxis"]["min"], fig["xAxis"]["max"]] == [400.0, 500.0]


def test_darker_returns_a_darker_hex():
    assert gamma._darker("#66bb6a") != "#66bb6a"
    assert gamma._darker("#66bb6a").startswith("#") and len(gamma._darker("#66bb6a")) == 7


def test_heatmap_figure_blended_no_lines_no_fade():
    rows = [("09:30", 450, None, None, None, 0, {448.0: 5, 450.0: -3})]
    fig = gamma.heatmap_figure(rows, "GEX", yrange=[440.0, 460.0])
    hm = next(s for s in fig["series"] if s["type"] == "heatmap")
    # Blended: interpolated image, no cell borders, no separator mesh.
    assert hm["interpolation"] is True and hm["borderWidth"] == 0
    assert "plotBackgroundColor" not in fig["chart"]
    # No fade on hover/click.
    assert hm["states"]["inactive"]["enabled"] is False
    assert "pointFormat" in hm["tooltip"]                # value text (shown on click)
    # Transparent background (same as the candlestick graph); zero fades out.
    assert fig["chart"]["backgroundColor"] == "transparent"
    assert [fig["yAxis"]["min"], fig["yAxis"]["max"]] == [440.0, 460.0]   # aligned to bars


def test_heat_colorscale_is_dark_with_transparent_zero():
    # Diverging dark scale: zero (stop 0.50) fades to transparent so the dark page
    # shows through; the extremes are red (neg) and green (pos).
    mid = next(s for s in gamma.HEAT_STOPS if s[0] == 0.50)
    assert mid[1] in ("rgba(0,0,0,0.0)", "rgba(0,0,0,0)")
    assert "239,83,80" in gamma.HEAT_STOPS[0][1]          # most-negative → red
    assert "102,187,106" in gamma.HEAT_STOPS[-1][1]       # most-positive → green


def test_spot_line_is_off_white():
    assert gamma.PRICE_LINE.lower() in ("#f5f5f5", "#ffffff", "#fafafa")


def test_heatmap_figure_spot_line_not_faded():
    rows = [("09:30", 450.0, None, None, None, 0, {449.0: {"net": 5}}),
            ("09:35", 451.5, None, None, None, 0, {449.0: {"net": 7}})]
    fig = gamma.heatmap_figure(rows, "GEX")
    line = next(s for s in fig["series"] if s["type"] == "line")
    assert line["states"]["inactive"]["enabled"] is False


def test_heat_init_fig_has_press_and_hold_tooltip_hook():
    fig = gamma._heat_init_fig()
    assert fig["tooltip"]["enabled"] is True
    # Press-and-hold: shown on mousedown, hidden on mouseup; gated via runPointActions.
    load = fig["chart"]["events"][":load"]
    assert "runPointActions" in load
    assert "'mousedown'" in load and "'mouseup'" in load


def test_heatmap_figure_carries_press_and_hold_hook():
    # The hook must be on the figure the element actually MOUNTS with (render
    # overwrites the init fig before mount), so heatmap_figure carries it too.
    rows = [("09:30", 450, None, None, None, 0, {448.0: 5, 450.0: -3})]
    fig = gamma.heatmap_figure(rows, "GEX", yrange=[440.0, 460.0])
    assert ":load" in fig["chart"]["events"]
    load = fig["chart"]["events"][":load"]
    assert "'mousedown'" in load and "'mouseup'" in load


def test_term_heatmap_blended_and_dark_contrast():
    tg = {"underlying_price": 450.0, "expirations": ["2026-06-18"],
          "cells": {"2026-06-18": {450.0: {"net_gex_usd": 5},
                                   460.0: {"net_gex_usd": -200}}}}
    fig = gamma.term_heatmap(tg)
    hm = fig["series"][0]
    # Blended like the intraday heatmap: interpolated, no borders, no fade.
    assert hm["interpolation"] is True and hm["borderWidth"] == 0
    assert hm["states"]["inactive"]["enabled"] is False
    assert fig["chart"]["backgroundColor"] == "transparent"
    assert "plotBackgroundColor" not in fig["chart"]
    # press-and-hold tooltip hook present (Term paints on the recreated chart_el)
    assert "'mousedown'" in fig["chart"]["events"][":load"]
    # symmetric contrast clamp present on the color axis
    ca = fig["colorAxis"]
    assert ca.get("max") is not None and ca.get("min") == -ca["max"]


def test_robust_zmax_ignores_none_and_returns_positive():
    z = [[None, 5], [-3, None], [200, 1]]
    zmax = gamma._robust_zmax(z)
    assert zmax is not None and zmax > 0
    assert gamma._robust_zmax([[None], []]) is None


def test_render_callable():
    assert callable(gamma.render)


def test_render_view_updates_in_place_not_clear():
    """Regression: the flicker fix means _render_view must NOT tear down the
    Plotly elements every repaint. It should update figures in place and must not
    call chart_box.clear()/heatmap_box.clear() (which rebuilt the canvas)."""
    src = inspect.getsource(gamma.render)
    assert "_set_figure" in src           # in-place Highcharts update (no teardown)
    assert "_set_chart" in src            # kind-aware recreate for the bar<->Term switch
    assert "chart_box.clear()" not in src   # never tear down the whole panel/messages
    assert "heatmap_box.clear()" not in src
    assert "_STRIKE_HEAT_SPLIT" in src   # fixed 40/60 strike/heatmap split is wired
    assert "reflow" in src               # panels reflow to fill their flex containers
    assert "bar_yrange" in src           # fixed ±N_SIDE window y-range is wired


def test_big_gamma_snapshot_read_is_off_loop():
    """Perf regression (P5): the ~14 MB cache:options:gamma snapshot must be read
    OFF the event loop via run.io_bound — in the version-gated repaint AND the
    initial page-build read — while the cheap :ver probes stay ON the loop.

    Guards: the version compare is still done from read_versions/read_version (the
    tiny :ver counters, never wrapped), the big payload GET+parse goes through
    run.io_bound, and an in-flight ("fetching") guard prevents a slow read from
    stacking across the 2 s poll ticks."""
    src = inspect.getsource(gamma.render)
    # The big-payload read is moved off-loop.
    assert 'run.io_bound(bus_client.read, "options:gamma")' in src
    # The cheap version probes are NOT wrapped (still a plain synchronous call).
    assert "read_versions([" in src
    # The repaint/poll became async + are guarded against a dead client.
    assert "async def _maybe_repaint" in src
    assert "async def _poll" in src
    assert "@guard_async" in src
    # Re-entrancy: a poll must not fire a second big read while one is in flight.
    assert 'state.get("fetching")' in src
    # Version-gating preserved: only fetch when the version actually changed.
    assert 'version == seen["gamma"]' in src


def test_render_syncs_symbol_and_guards_foreign_snapshots():
    """Regression: the dropdown must sync to the cached snapshot's symbol on build,
    and a repaint must ignore a snapshot whose symbol != the selected one — so a
    refresh (or the service's $SPX startup publish) can't revert the displayed
    symbol."""
    src = inspect.getsource(gamma.render)
    assert "_set_symbol" in src                       # dropdown synced to cache symbol
    assert "on_value_change(lambda e: _on_symbol_change" in src   # select → refresh
    # repaint guard: snapshot symbol must match the current dropdown
    assert 'snap.get("symbol")' in src and "_current_symbol()" in src


def test_page_imports_no_engine_or_proxy():
    """Regression: the Tier-3 page must not pull in engine / proxy code."""
    for attr in ("proxy", "gamma_tool", "gex_history_db", "html_render",
                 "regime_filter", "OPTIONS_SCANNER", "sys"):
        assert not hasattr(gamma, attr), f"gamma.py still references {attr}"
    src = inspect.getsource(gamma)
    for forbidden in ("gamma_tool", "gex_history_db", "html_render",
                      "regime_filter", "import proxy", "OPTIONS_SCANNER"):
        assert forbidden not in src, f"gamma.py must not reference {forbidden!r}"


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty (service
    cold) — the Tier-3 graceful-empty path."""
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:gamma") is None  # confirm empty
    with ui.card():
        gamma.render()  # must not raise


def test_symbol_options_default_present_and_first():
    out = gamma.symbol_options({"symbols": ["SPY", "$SPX", "NVDA"]})
    assert out[0] == "$SPX"          # $SPX always first
    assert out == ["$SPX", "SPY", "NVDA"]


def test_symbol_options_cold_fallback():
    assert gamma.symbol_options(None) == ["$SPX", "SPY", "QQQ"]
    assert gamma.symbol_options({}) == ["$SPX", "SPY", "QQQ"]


def test_symbol_options_injects_missing_default():
    out = gamma.symbol_options({"symbols": ["SPY", "QQQ"]})
    assert out[0] == "$SPX"
    assert "SPY" in out and "QQQ" in out


# ── Phase 3c: dynamic colors palette-mapped + panel flex as runtime arbitrary class ──
def test_status_color_class_maps_collector_states():
    from pages.options import gamma as g
    assert g.status_color_class("green") == "text-[green]"
    assert g.status_color_class("red") == "text-[red]"
    assert g.status_color_class("gray") == "text-[gray]"
    assert g.status_color_class("#c48b00") == "text-[#c48b00]"
    assert g.status_color_class("") == "text-[#666666]"     # fallback (also the compute default)


def test_flex_class_builds_arbitrary_value():
    from pages.options import gamma as g
    assert g.flex_class(0.5) == "flex-[0.5_1_0%]"
    assert g.flex_class(1) == "flex-[1_1_0%]"
    assert g.flex_class(0, grow2=0, basis="0px") == "flex-[0_0_0px]"


def test_history_dates_distinct_newest_first():
    from pages.options import gamma as g
    payload = {"briefings": [
        {"date": "2026-07-08", "slot": "close"},
        {"date": "2026-07-08", "slot": "open"},
        {"date": "2026-07-02", "slot": "adhoc-1842"},
    ]}
    assert g.history_dates(payload) == ["2026-07-08", "2026-07-02"]  # distinct, order kept
    assert g.history_dates(None) == []
    assert g.history_dates({"briefings": []}) == []


def test_flow_figure_builds_stacked_panels():
    from pages.options import gamma as g
    rows = [
        {"ts": 1783000000, "spot": 500.0, "call_vol": 100, "put_vol": 80,
         "call_prem": 2.0e6, "put_prem": 1.5e6},
        {"ts": 1783000120, "spot": 501.0, "call_vol": 140, "put_vol": 130,
         "call_prem": 3.0e6, "put_prem": 2.8e6},
    ]
    fig = g.flow_figure(rows)
    names = [s["name"] for s in fig["series"]]
    assert names == ["Price", "Call premium", "Put premium", "Net premium (call − put)"]
    assert len(fig["yAxis"]) == 3                       # price / premium / net panels
    call = next(s for s in fig["series"] if s["name"] == "Call premium")
    assert call["data"][0] == [0, 2.0]                  # 2.0e6 -> 2.0 $M
    net = next(s for s in fig["series"] if s["name"].startswith("Net"))
    assert net["data"][0] == [0, 0.5] and net["type"] == "area"   # (2.0-1.5)/1e6


def test_flow_figure_skips_missing_premium():
    from pages.options import gamma as g
    rows = [
        {"ts": 1, "spot": 500.0, "call_prem": None, "put_prem": None},   # pre-Phase-1
        {"ts": 2, "spot": 501.0, "call_prem": 1.0e6, "put_prem": 0.4e6},
    ]
    fig = g.flow_figure(rows)
    call = next(s for s in fig["series"] if s["name"] == "Call premium")
    assert call["data"] == [[1, 1.0]]                   # only the row that has premium
    price = next(s for s in fig["series"] if s["name"] == "Price")
    assert len(price["data"]) == 2                      # price present for both


def test_flow_summary_text_variants():
    from pages.options import gamma as g
    assert "No flow data" in g.flow_summary_text([])
    assert "not collected yet" in g.flow_summary_text(
        [{"ts": 1, "spot": 1, "call_prem": None, "put_prem": None}])
    s = g.flow_summary_text(
        [{"ts": 1, "spot": 1, "call_vol": 10, "put_vol": 5, "call_prem": 3.0e6, "put_prem": 1.0e6}])
    assert "3.0M" in s and "1.0M" in s and "+2.0M" in s


def _proj_rows():
    return [("09:30", 100.0, None, None, None, 0,
             {99.0: {"net": 5.0}, 100.0: {"net": -3.0}, 101.0: {"net": 4.0}}),
            ("09:31", 100.2, None, None, None, 0,
             {99.0: {"net": 6.0}, 100.0: {"net": -2.0}, 101.0: {"net": 5.0}})]


def test_heatmap_appends_projection_columns():
    proj = {"times": ["13:15", "13:30"], "spot": 100.0,
            "grid": {99.0: [5.0, 6.0], 100.0: [-8.0, -12.0], 101.0: [4.0, 3.0]},
            "cone": {"mid": [100.0, 100.0], "up": [100.5, 100.8], "down": [99.5, 99.2]}}
    fig = gamma.heatmap_figure(_proj_rows(), "GEX", yrange=[95.0, 105.0], projection=proj)
    cats = fig["xAxis"]["categories"]
    assert cats[-2:] == ["13:15", "13:30"]                         # future cols appended
    pls = fig["xAxis"].get("plotLines", [])
    assert any(pl.get("className") == "gamma-now-divider" for pl in pls)   # 'now' seam
    hm = next(s for s in fig["series"] if s["type"] == "heatmap")
    assert any(p[0] >= 2 for p in hm["data"])                      # future cells at idx>=2
    names = [s.get("name") for s in fig["series"]]
    assert "EM up" in names and "EM down" in names                # cone overlays
    spot = next(s for s in fig["series"] if s.get("name") == "Spot")
    assert len(spot["data"]) == 4                                  # 2 collected + 2 cone.mid


def test_heatmap_no_projection_no_divider_empty_cone():
    fig = gamma.heatmap_figure(_proj_rows(), "GEX", yrange=[95.0, 105.0], projection=None)
    assert not any(pl.get("className") == "gamma-now-divider"
                   for pl in fig["xAxis"].get("plotLines", []))
    # The EM cone series are ALWAYS present (fixed 4-series structure so the in-place
    # chart.update() maps 1:1 across views) but carry EMPTY data with no projection.
    em = {s["name"]: s for s in fig["series"] if s.get("name") in ("EM up", "EM down")}
    assert set(em) == {"EM up", "EM down"}
    assert em["EM up"]["data"] == [] and em["EM down"]["data"] == []


def test_heatmap_series_count_constant_across_projection():
    # A CONSTANT series count (heatmap + Spot + EM up + EM down = 4) is required so the
    # in-place chart.update() maps series 1:1 when toggling GEX<->Charm/DELTA/Vanna. A
    # varying count made Highcharts replace series (shifting colorIndex + leaving stray
    # line paths) → the heatmap rendered as a mess of thin lines. Regression guard.
    proj = {"times": ["13:15"], "spot": 100.0, "grid": {100.0: [5.0]},
            "cone": {"mid": [100.0], "up": [100.5], "down": [99.5]}}
    with_proj = gamma.heatmap_figure(_proj_rows(), "GEX", yrange=[95.0, 105.0], projection=proj)
    no_proj = gamma.heatmap_figure(_proj_rows(), "Charm", yrange=[95.0, 105.0], projection=None)
    assert len(with_proj["series"]) == len(no_proj["series"]) == 4
    assert [s["type"] for s in no_proj["series"]] == ["heatmap", "line", "line", "line"]


def test_strike_heat_split_constant():
    assert gamma._STRIKE_HEAT_SPLIT == (0.40, 0.60)   # flip to (0.70, 0.30) if hard to read


def test_status_strip_text_combines_sources():
    s = gamma.status_strip_text({"last_scan": "1:00 PM", "next_scan": "1:01 PM"},
                                "spot 5,400.00", 75)
    assert "Last scan 1:00 PM" in s
    assert "Next scan 1:01 PM" in s
    assert "Next refresh 1:15" in s
    assert "spot 5,400.00" in s


def test_status_strip_text_defensive():
    s = gamma.status_strip_text(None, "", 0)
    assert "Last scan —" in s and "Next scan —" in s and "Next refresh 0:00" in s


# --- Call/Put wall lines extended across the heatmap ---

_WALL_ROWS = [("09:30", 450.0, None, None, None, 0, {449.0: {"net": 5}}),
              ("09:35", 451.5, None, None, None, 0, {449.0: {"net": 7}})]


def test_heatmap_figure_draws_wall_lines_across_the_plot():
    # Walls are horizontal yAxis plotLines, so they span the FULL time axis rather
    # than being a per-column series — "across the heatmap".
    fig = gamma.heatmap_figure(_WALL_ROWS, "GEX", spot=450.0, walls=[455.0, 445.0])
    lines = fig["yAxis"]["plotLines"]
    by_value = {pl["value"]: pl for pl in lines}
    assert set(by_value) == {455.0, 445.0}
    # Labeled by side relative to spot, matching the bar chart's vocabulary.
    assert "Call wall" in by_value[455.0]["label"]["text"]
    assert "Put wall" in by_value[445.0]["label"]["text"]


def test_heatmap_figure_always_emits_plotlines_key():
    # In-place chart.update() MERGES options: a figure that omits plotLines would
    # leave the PREVIOUS view's wall lines painted on the new view. Always emit the
    # key (empty when there are no walls) so an update replaces them deterministically.
    fig = gamma.heatmap_figure(_WALL_ROWS, "Charm")
    assert fig["yAxis"]["plotLines"] == []


def test_heatmap_wall_lines_are_defensive():
    # A None/garbage wall must not raise or emit a bogus line.
    fig = gamma.heatmap_figure(_WALL_ROWS, "GEX", spot=450.0,
                               walls=[None, "x", 455.0])
    assert [pl["value"] for pl in fig["yAxis"]["plotLines"]] == [455.0]
    # No spot → still drawn (side falls back to Call wall), never dropped.
    fig2 = gamma.heatmap_figure(_WALL_ROWS, "GEX", spot=None, walls=[455.0])
    assert len(fig2["yAxis"]["plotLines"]) == 1


def test_heatmap_figure_draws_gamma_flip_line():
    # The flip is the regime boundary — seeing where price sat relative to it all
    # session is the point, so it spans the heatmap like the walls do.
    fig = gamma.heatmap_figure(_WALL_ROWS, "GEX", spot=450.0, walls=[455.0],
                               flip=449.5)
    by_value = {pl["value"]: pl for pl in fig["yAxis"]["plotLines"]}
    assert set(by_value) == {455.0, 449.5}
    assert "Gamma flip" in by_value[449.5]["label"]["text"]
    # Distinguishable from the walls at a glance.
    assert by_value[449.5]["color"] == gamma.FLIP_COLOR
    assert by_value[455.0]["color"] == gamma.WALL_COLOR


def test_heatmap_flip_line_defensive_and_independent():
    # Flip alone (no walls) still draws; a garbage flip is skipped, not raised on.
    assert len(gamma.heatmap_figure(_WALL_ROWS, "GEX", flip=449.5)["yAxis"]["plotLines"]) == 1
    assert gamma.heatmap_figure(_WALL_ROWS, "GEX", flip="x")["yAxis"]["plotLines"] == []
    assert gamma.heatmap_figure(_WALL_ROWS, "GEX", flip=None)["yAxis"]["plotLines"] == []
