"""Tests for the Gamma page pure figure/transform builders + Tier-3 wiring.

The engine work (live chain fetch + GammaEngine compute + history grids + the
Explain/Analyze text) moved to ``services/options_svc/compute`` — see that
service's tests. The page now reads a cached snapshot from the Redis bus and
drives refresh/explain/analyze via commands, so it must import NO engine / proxy
code. The pure figure/transform builders below stay unchanged + unit-tested.
"""
import inspect
import json

import pytest

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
    assert b == {"strikes": [], "nets": [], "colors": [], "hovers": [],
                 "projected": []}


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
    # A CONSTANT series count (heatmap + the 3 spot-overlay series + EM up + EM down
    # + the 3 level tracks = 9) is required so the in-place chart.update() maps
    # series 1:1 when toggling
    # GEX<->Charm/DELTA/Vanna. A varying count made Highcharts replace series
    # (shifting colorIndex + leaving stray line paths) → the heatmap rendered as a
    # mess of thin lines. Regression guard: what matters is that the count is the
    # SAME everywhere, whatever the optional blocks are doing.
    proj = {"times": ["13:15"], "spot": 100.0, "grid": {100.0: [5.0]},
            "cone": {"mid": [100.0], "up": [100.5], "down": [99.5]}}
    with_proj = gamma.heatmap_figure(_proj_rows(), "GEX", yrange=[95.0, 105.0], projection=proj)
    no_proj = gamma.heatmap_figure(_proj_rows(), "Charm", yrange=[95.0, 105.0], projection=None)
    assert len(with_proj["series"]) == len(no_proj["series"]) == 9
    assert [s["type"] for s in no_proj["series"]] == [
        "heatmap", "line", "columnrange", "errorbar"] + ["line"] * 5


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


# --- Intraday level-movement tracks on the heatmap (toggleable) ---

_LVL = {"flip": [449.5, 450.0], "call_wall": [455.0, 456.0],
        "put_wall": [445.0, None]}


def _series_by_name(fig):
    return {s["name"]: s for s in fig["series"]}


def test_heatmap_track_series_plot_level_movement_over_time():
    fig = gamma.heatmap_figure(_WALL_ROWS, "GEX", levels=_LVL, show_tracks=True)
    s = _series_by_name(fig)
    # x is the time-category index, y the level at that snapshot.
    assert s["Call wall track"]["data"] == [[0, 455.0], [1, 456.0]]
    assert s["Flip track"]["data"] == [[0, 449.5], [1, 450.0]]
    # A None level is a GAP, not a dropped point — the line must not slide left.
    assert s["Put wall track"]["data"] == [[0, 445.0], [1, None]]
    # Walls jump between discrete strikes; a smooth line would imply levels that
    # never existed, so the tracks are drawn as steps.
    assert s["Call wall track"]["step"] == "left"


def test_heatmap_tracks_hidden_when_toggled_off_but_series_still_emitted():
    # The series COUNT must not vary: Highcharts' in-place update replaces (not
    # updates) series when the count changes, shifting colorIndex and leaving stray
    # paths. So the tracks are always present — empty when off.
    off = gamma.heatmap_figure(_WALL_ROWS, "GEX", levels=_LVL, show_tracks=False)
    on = gamma.heatmap_figure(_WALL_ROWS, "GEX", levels=_LVL, show_tracks=True)
    assert len(off["series"]) == len(on["series"])
    s = _series_by_name(off)
    assert s["Call wall track"]["data"] == []
    assert s["Flip track"]["data"] == []


def test_heatmap_series_count_is_constant_across_views_and_toggle():
    variants = [
        gamma.heatmap_figure(_WALL_ROWS, "GEX", levels=_LVL, show_tracks=True),
        gamma.heatmap_figure(_WALL_ROWS, "Charm"),                    # no levels at all
        gamma.heatmap_figure(_WALL_ROWS, "Vanna", levels=None, show_tracks=True),
        gamma.heatmap_figure(_WALL_ROWS, "GEX", walls=[455.0], flip=449.5),
    ]
    assert len({len(f["series"]) for f in variants}) == 1


def test_heatmap_tracks_keep_static_lines_visible():
    # The user asked for BOTH: dashed static line = the level now, solid track =
    # how it got there.
    fig = gamma.heatmap_figure(_WALL_ROWS, "GEX", spot=450.0, walls=[455.0],
                               flip=449.5, levels=_LVL, show_tracks=True)
    assert len(fig["yAxis"]["plotLines"]) == 2          # static flip + call wall
    assert _series_by_name(fig)["Call wall track"]["data"]


# --- Spot overlay style: line / candles / OHLC ---

def test_ohlc_bars_buckets_samples_with_carried_open():
    # Spot is a 1-min POINT SAMPLE, not a bar. Bars are built the standard way for a
    # sampled series: open = the PREVIOUS bar's close, so bars are contiguous and a
    # 1-min bar still has a body instead of a degenerate O==H==L==C dash.
    bars = gamma.ohlc_bars([10.0, 12.0, 11.0, 9.0, 13.0, 14.0], 3)
    assert bars[0] == [1, 10.0, 12.0, 10.0, 11.0]      # x = bucket centre column
    assert bars[1] == [4, 11.0, 14.0, 9.0, 14.0]       # open carried; low spans it


def test_ohlc_bars_one_minute_interval_still_has_a_body():
    bars = gamma.ohlc_bars([10.0, 11.0, 10.5], 1)
    assert [b[0] for b in bars] == [0, 1, 2]
    assert bars[0] == [0, 10.0, 10.0, 10.0, 10.0]      # first bar has no prior close
    assert bars[1] == [1, 10.0, 11.0, 10.0, 11.0]      # carried open -> real body


def test_ohlc_bars_skips_gaps_and_is_defensive():
    assert gamma.ohlc_bars([], 5) == []
    assert gamma.ohlc_bars(None, 5) == []
    assert gamma.ohlc_bars([1.0, 2.0], 0) == []        # no div-by-zero
    # A None sample is skipped, not read as 0 (which would spike the low).
    assert gamma.ohlc_bars([10.0, None, 12.0], 3) == [[1, 10.0, 12.0, 10.0, 12.0]]
    assert gamma.ohlc_bars([None, None], 2) == []      # no usable sample -> no bar


def test_candle_points_color_each_bar_by_direction():
    body, wick = gamma.candle_points([[0, 10.0, 12.0, 9.0, 11.0],    # up   (c>o)
                                      [1, 11.0, 11.5, 8.0, 9.0]])    # down (c<o)
    # Body spans open->close (order-independent); wick spans low->high.
    assert body[0] == {"x": 0, "low": 10.0, "high": 11.0, "color": gamma.UP_COLOR}
    assert body[1] == {"x": 1, "low": 9.0, "high": 11.0, "color": gamma.DOWN_COLOR}
    assert (wick[0]["low"], wick[0]["high"]) == (9.0, 12.0)
    # Per-POINT color, so one series carries both up and down bars.
    assert wick[0]["color"] == gamma.UP_COLOR and wick[1]["color"] == gamma.DOWN_COLOR
    assert gamma.candle_points([]) == ([], [])


def test_heatmap_spot_style_populates_only_the_selected_overlay():
    for style, want, empty in (("line", "Spot", ("Spot candles", "Spot wicks")),
                               ("candle", "Spot candles", ("Spot",)),
                               ("ohlc", "Spot candles", ("Spot",))):
        fig = gamma.heatmap_figure(_WALL_ROWS, "GEX", spot_style=style,
                                   spot_interval=1)
        s = {x["name"]: x for x in fig["series"]}
        assert s[want]["data"], f"{style}: {want} should carry data"
        for name in empty:
            assert s[name]["data"] == [], f"{style}: {name} must be empty"


def test_heatmap_bar_styles_avoid_the_stock_module():
    # Candlestick/ohlc are STOCK series; loading that module breaks this chart's
    # in-place update (live-verified). The bars are core columnrange + errorbar.
    fig = gamma.heatmap_figure(_WALL_ROWS, "GEX", spot_style="candle")
    types = {x["name"]: x["type"] for x in fig["series"]}
    assert types["Spot candles"] == "columnrange"
    assert types["Spot wicks"] == "errorbar"
    assert "candlestick" not in types.values() and "ohlc" not in types.values()


def test_heatmap_ohlc_draws_thinner_than_candles():
    candle = {x["name"]: x for x in
              gamma.heatmap_figure(_WALL_ROWS, "GEX", spot_style="candle")["series"]}
    ohlc = {x["name"]: x for x in
            gamma.heatmap_figure(_WALL_ROWS, "GEX", spot_style="ohlc")["series"]}
    assert "pointWidth" not in candle["Spot candles"]      # full-width filled body
    assert ohlc["Spot candles"]["pointWidth"] == 2         # thin -> reads as a bar


# --- Net Prem view ---------------------------------------------------------
# The payload is DELIBERATELY loose (NetPremiumSnapshot.series is typed `dict`),
# so these builders must be TOTAL: a malformed row skips that point, a malformed
# symbol skips that series, and everything else still renders. A bare row[1]
# would raise IndexError and 500 the whole Dealer Positioning page.

_T1, _T2, _T3 = 1_700_000_000, 1_700_000_060, 1_700_000_120


def _np_series():
    """Two symbols on a STAGGERED clock -- SPY starts a minute late, so the union
    x-axis is the only thing that keeps them aligned."""
    return {
        "QQQ": [[_T1, 3.0e6, 1.0e6], [_T2, 4.0e6, 1.0e6], [_T3, 5.0e6, 1.0e6]],
        "SPY": [[_T2, 1.0e6, 5.0e6], [_T3, 1.0e6, 9.0e6]],
    }


def test_net_prem_symbols_is_every_group_member_in_group_order():
    syms = gamma.net_prem_symbols()
    assert syms[:3] == ["$SPX", "$NDX", "BIG10"]
    assert syms[-1] == "AMD"
    assert len(syms) == len(set(syms)) == 28
    # Flat list == the concatenation of the group tuples.
    flat = [s for g in gamma.NET_PREM_GROUPS for s in g["symbols"]]
    assert syms == flat


def _service_consts(module, *names):
    """Module-level constants read out of a Tier-2 file WITHOUT importing it.

    The Net Prem view duplicates two things across the tier boundary because the
    webgui may not import ``services.*``. The tier rule forbids IMPORTING that
    code, not READING the file — so parse it and compare, giving each duplicated
    table a real pin instead of a reviewer's one-off check. Zero runtime coupling;
    the tests skip cleanly if the service layout ever moves.
    """
    import ast
    import pathlib
    src = pathlib.Path(__file__).parents[2] / "services" / "options_svc" / module
    if not src.exists():
        pytest.skip(f"service module not present: {module}")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if getattr(target, "id", None) in names:
                found[target.id] = ast.literal_eval(node.value)
    missing = [n for n in names if n not in found]
    if missing:
        pytest.skip(f"{module} no longer defines {', '.join(missing)}")
    return found


def test_net_prem_groups_match_the_service():
    groups = _service_consts("net_premium.py", "GROUPS")["GROUPS"]
    assert gamma.NET_PREM_GROUPS == groups


def test_net_prem_collection_window_matches_the_scheduler():
    """The staleness gate's window duplicates the scheduler's GEX collection
    window, and only agreement makes the gate honest.

    If the service window ever NARROWS, the page raises a daily false STALE —
    exactly the cry-wolf outcome net_prem_status_text exists to avoid, which
    would poison the one diagnostic this view adds. If it WIDENS, the page goes
    blind during the extra minutes instead. The GEX start has already moved once
    (08:30 -> 08:00), so this is a live risk, not a hypothetical.
    """
    svc = _service_consts("scheduler.py", "_GEX_START", "_GEX_STOP")
    assert gamma._NP_WINDOW_OPEN == svc["_GEX_START"]
    assert gamma._NP_WINDOW_CLOSE == svc["_GEX_STOP"]


def test_net_prem_every_group_symbol_has_a_distinct_color():
    syms = gamma.net_prem_symbols()
    colors = [gamma.net_prem_color(s) for s in syms]
    # Coverage: nothing falls through to the grey fallback.
    assert gamma.NET_PREM_FALLBACK not in colors
    for s in syms:
        assert s in gamma.NET_PREM_COLORS, f"{s} has no colour"
    # Distinctness: two lines on one chart must never share a colour.
    assert len(set(colors)) == len(colors)


def test_net_prem_color_is_stable_across_selections():
    # A symbol's colour is a property of the SYMBOL, never of the selection --
    # SPY is the same line colour whether you plot 2 names or 20.
    small = gamma.net_prem_figure(_np_series(), ["SPY"])
    big = gamma.net_prem_figure(_np_series(), ["QQQ", "SPY"])
    spy_small = [s for s in small["series"] if s["name"] == "SPY"][0]
    spy_big = [s for s in big["series"] if s["name"] == "SPY"][0]
    assert spy_small["color"] == spy_big["color"] == gamma.NET_PREM_COLORS["SPY"]


def test_net_prem_color_falls_back_for_an_unknown_symbol():
    assert gamma.net_prem_color("ZZZZ") == gamma.NET_PREM_FALLBACK


def test_net_prem_value_dollars_is_millions_of_net_premium():
    assert gamma.net_prem_value([_T1, 3.0e6, 1.0e6], "dollars") == 2.0
    assert gamma.net_prem_value([_T1, 1.0e6, 5.0e6], "dollars") == -4.0
    assert gamma.net_prem_value([_T1, 0.0, 0.0], "dollars") == 0.0   # real zero


def test_net_prem_value_skew_is_a_signed_percent_of_total():
    assert gamma.net_prem_value([_T1, 3.0e6, 1.0e6], "skew") == 50.0
    assert gamma.net_prem_value([_T1, 1.0e6, 3.0e6], "skew") == -50.0
    # Nothing traded either side -> nothing to report (and no div-by-zero).
    assert gamma.net_prem_value([_T1, 0.0, 0.0], "skew") is None
    assert gamma.net_prem_value([_T1, -1.0, 0.0], "skew") is None


def test_net_prem_value_is_total_over_malformed_rows():
    for bad in (None, [], [1], [1, 2], "notalist", "x", {"call": 1},
                [1, "x", "y"], [1, None, 2.0], [1, True, 2.0],
                [1, float("nan"), 2.0], [1, float("inf"), 2.0], 42):
        assert gamma.net_prem_value(bad, "dollars") is None, bad
        assert gamma.net_prem_value(bad, "skew") is None, bad


def test_net_prem_figure_x_axis_is_the_union_of_selected_timestamps():
    fig = gamma.net_prem_figure(_np_series(), ["QQQ", "SPY"])
    assert fig["xAxis"]["categories"] == [gamma._fmt_ts(t)
                                          for t in (_T1, _T2, _T3)]
    by = {s["name"]: s for s in fig["series"]}
    # QQQ spans the whole union; SPY started late, so it begins at index 1 --
    # the union is what keeps the two lines on the same clock.
    assert [p[0] for p in by["QQQ"]["data"]] == [0, 1, 2]
    assert [p[0] for p in by["SPY"]["data"]] == [1, 2]
    assert by["SPY"]["data"][0][1] == -4.0


def test_net_prem_figure_tooltip_is_not_shared_and_legend_is_on():
    fig = gamma.net_prem_figure(_np_series(), ["QQQ", "SPY"])
    # 20+ series under one shared tooltip is an unreadable wall.
    assert fig["tooltip"]["shared"] is False
    assert fig["legend"]["enabled"] is True
    assert fig["yAxis"]["plotLines"][0]["value"] == 0


def test_net_prem_group_symbols_returns_one_group():
    assert gamma.net_prem_group_symbols("megacaps") == [
        "NVDA", "AVGO", "AAPL", "META", "MSFT",
        "TSLA", "PLTR", "AMZN", "GOOGL", "AMD"]
    assert "$SPX" in gamma.net_prem_group_symbols("indices")
    # Disjoint groups, so a mega-cap request can never carry an index back.
    assert "$SPX" not in gamma.net_prem_group_symbols("megacaps")


def test_net_prem_group_symbols_is_total_over_an_unknown_key():
    """A persisted group key that no longer exists must degrade, not raise —
    gamma_netprem_group lives in the hand-editable settings.json."""
    for junk in ("gone", "", None, 7):
        assert gamma.net_prem_group_symbols(junk) == []


def test_only_this_group_drops_out_of_group_symbols():
    """The one case where the group tab touches the CHART rather than visibility.

    The tab filters which tick-boxes you see — that is what lets $SPX plot beside
    XLK — so switching to Mega-caps leaves ticked indices on the chart. This
    reduces the selection to the active group in one click.
    """
    out = gamma.net_prem_only_group(
        ["$SPX", "$NDX", "SPY", "NVDA", "AAPL"], "megacaps")
    assert out == ["NVDA", "AAPL"]


def test_only_this_group_keeps_ticks_rather_than_selecting_the_whole_group():
    """A narrowing, not a set-to-all: pressing it can only REMOVE lines. If it
    selected the whole group it would silently add nine symbols you never asked
    for whenever one mega-cap happened to be ticked."""
    out = gamma.net_prem_only_group(["$SPX", "NVDA"], "megacaps")
    assert out == ["NVDA"]


def test_only_this_group_is_total_over_junk():
    assert gamma.net_prem_only_group([], "megacaps") == []
    assert gamma.net_prem_only_group(["NVDA"], "gone") == []
    # Hostile persisted selections must not raise (settings.json is editable).
    assert gamma.net_prem_only_group([123, None, "NVDA"], "megacaps") == ["NVDA"]


def test_select_all_adds_the_active_group():
    out = gamma.net_prem_with_group(["$SPX"], "megacaps")
    assert out[0] == "$SPX"                      # other groups survive
    assert set(gamma.net_prem_group_symbols("megacaps")) <= set(out)


def test_select_all_is_group_scoped_not_all_28():
    """The tick-boxes on screen ARE the active group, so ticking a hidden 28
    would plot lines whose source the reader cannot see."""
    out = gamma.net_prem_with_group([], "sectors")
    assert set(out) == set(gamma.net_prem_group_symbols("sectors"))
    assert "NVDA" not in out and "$SPX" not in out


def test_select_all_returns_group_order_and_dedupes():
    out = gamma.net_prem_with_group(["AMD", "AMD", "NVDA"], "megacaps")
    assert out == gamma.net_prem_group_symbols("megacaps")   # order, no dupes


def test_select_all_is_total_over_junk():
    assert gamma.net_prem_with_group([123, None], "gone") == []
    assert gamma.net_prem_with_group([123, None, "NVDA"], "gone") == ["NVDA"]


def test_select_all_is_wired_to_the_button():
    import inspect
    src = inspect.getsource(gamma.render)
    assert "np_all_btn.on_click(_np_select_all)" in src
    sel = src[src.index("def _np_select_all("):]
    sel = sel[:sel.index("\n    @guard\n    def _np_only_group(")]
    assert "net_prem_with_group(_np_current(), np_group_tabs.value)" in sel, sel


def test_symbol_scoped_controls_hide_on_net_prem():
    """Symbol / Refresh now / Level movement / Spot / Bar drive the
    symbol-scoped views. Net Prem plots a fixed universe from its own cache key
    and has no spot overlay, so leaving them visible there is five dead knobs."""
    import inspect
    src = inspect.getsource(gamma.render)
    sync = src[src.index("def _sync_spot_controls("):]
    sync = sync[:sync.index("\n    spot_style_sel.on_value_change")]
    assert 'view_toggle.value != "Net Prem"' in sync, sync
    for name in ("symbol_in", "fetch_btn", "tracks_sw", "spot_style_sel",
                 # These three report on the symbol in the now-hidden dropdown,
                 # so on Net Prem they would act on a symbol the reader can
                 # neither see nor change.
                 "explain_btn", "analyze_btn", "briefings_btn"):
        assert name in sync, f"{name} not hidden on Net Prem"
    # Bar stays subject to its own line-style rule as well as the view.
    assert "spot_style_sel.value != \"line\"" in sync, sync
    # ...and the view switch must actually call it.
    assert "_sync_spot_controls()" in src[src.index("def _on_view_change("):]


def test_only_this_group_is_wired_to_the_button():
    import inspect
    src = inspect.getsource(gamma.render)
    assert "np_only_btn.on_click(_np_only_group)" in src
    only = src[src.index("def _np_only_group("):]
    only = only[:only.index("\n    np_group_tabs.on_value_change")]
    # It must read the ACTIVE tab, not a hardcoded group.
    assert "net_prem_only_group(_np_current(), np_group_tabs.value)" in only, only


def test_net_prem_figure_legend_sits_above_the_plot():
    """The legend must not share the bottom with the rotated time labels.

    Series count here is user-driven (up to 28), so the legend wraps to several
    rows. Highcharts reserves bottom space for the legend OR the axis labels, not
    both — so a bottom legend plus a pinned marginBottom overruns the -45° time
    labels and the axis title, which is what happened live at 15 symbols.
    """
    fig = gamma.net_prem_figure(_np_series(), ["QQQ", "SPY"])
    assert fig["legend"]["verticalAlign"] == "top"
    # A pinned bottom margin would re-break it however the legend is aligned.
    assert "marginBottom" not in fig["chart"]


def test_net_prem_figure_y_axis_title_follows_the_mode():
    assert "$M" in gamma.net_prem_figure(
        _np_series(), ["QQQ"], "dollars")["yAxis"]["title"]["text"]
    assert "%" in gamma.net_prem_figure(
        _np_series(), ["QQQ"], "skew")["yAxis"]["title"]["text"]


def test_net_prem_figure_skips_a_selected_symbol_with_no_data():
    fig = gamma.net_prem_figure(_np_series(), ["QQQ", "XLE"])
    assert [s["name"] for s in fig["series"]] == ["QQQ"]


def test_net_prem_figure_empty_selection_renders_an_empty_chart():
    for sel in ([], None):
        fig = gamma.net_prem_figure(_np_series(), sel)
        assert fig["series"] == [] and fig["xAxis"]["categories"] == []


def test_net_prem_figure_dedupes_the_selection():
    fig = gamma.net_prem_figure(_np_series(), ["SPY", "SPY", "QQQ"])
    assert [s["name"] for s in fig["series"]] == ["SPY", "QQQ"]


def test_net_prem_figure_survives_every_malformed_series_shape():
    # Each of these PASSES NetPremiumSnapshot validation (verified), so the page
    # really can receive them. The good symbol must still plot in every case.
    for bad in ("notalist", [[1]], [None], [[1, "x", "y"]], 42, {}, None,
                [[1, 2, 3], "junk"]):
        series = {"QQQ": _np_series()["QQQ"], "SPY": bad}
        fig = gamma.net_prem_figure(series, ["QQQ", "SPY"])
        names = [s["name"] for s in fig["series"]]
        assert "QQQ" in names, bad
        assert gamma.net_prem_summary_text(series, ["QQQ", "SPY"])


def test_net_prem_figure_skips_unreportable_skew_points():
    # (0, 0) is a REAL observation in dollars (plots as 0) but has no skew.
    series = {"SPY": [[_T1, 0.0, 0.0], [_T2, 3.0e6, 1.0e6]]}
    assert len(gamma.net_prem_figure(
        series, ["SPY"], "dollars")["series"][0]["data"]) == 2
    skew = gamma.net_prem_figure(series, ["SPY"], "skew")["series"][0]["data"]
    assert skew == [[1, 50.0]]


def test_net_prem_missing_names_selected_symbols_without_rows():
    series = _np_series()
    assert gamma.net_prem_missing(series, ["QQQ", "XLE", "SPY", "XLB"]) == ["XLE", "XLB"]
    assert gamma.net_prem_missing(series, ["QQQ", "SPY"]) == []
    # Present-but-unusable counts as missing, exactly like absent.
    assert gamma.net_prem_missing({"SPY": []}, ["SPY"]) == ["SPY"]
    assert gamma.net_prem_missing({"SPY": "notalist"}, ["SPY"]) == ["SPY"]
    assert gamma.net_prem_missing({"SPY": [None]}, ["SPY"]) == ["SPY"]
    assert gamma.net_prem_missing(None, ["SPY"]) == ["SPY"]


def test_net_prem_summary_text_names_the_extremes():
    txt = gamma.net_prem_summary_text(_np_series(), ["QQQ", "SPY"])
    assert "2 symbols" in txt
    assert "QQQ" in txt and "+$4.0M" in txt      # most call-led (last point)
    assert "SPY" in txt and "-$8.0M" in txt      # most put-led
    skew = gamma.net_prem_summary_text(_np_series(), ["QQQ", "SPY"], "skew")
    assert "+67%" in skew and "-80%" in skew


def test_net_prem_summary_text_reports_missing_names():
    txt = gamma.net_prem_summary_text(_np_series(), ["QQQ", "XLE", "XLB"])
    assert "XLE" in txt and "XLB" in txt and "no data yet" in txt


def test_net_prem_summary_text_handles_nothing_selected_and_nothing_plotted():
    assert "Select" in gamma.net_prem_summary_text(_np_series(), [])
    only_missing = gamma.net_prem_summary_text({}, ["XLE", "XLB"])
    assert "no data yet" in only_missing and "XLE" in only_missing
    # A single plotted symbol has no two extremes -- one reading, not a repeat.
    one = gamma.net_prem_summary_text(_np_series(), ["QQQ"])
    assert one.count("QQQ") == 1


def _ct(y, m, d, hh, mm):
    import datetime as _dt
    from zoneinfo import ZoneInfo
    return _dt.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/Chicago"))


def _iso(when):
    import datetime as _dt
    return when.astimezone(_dt.timezone.utc).isoformat()


def test_net_prem_status_text_never_published():
    now = _ct(2026, 8, 5, 10, 0)          # Wednesday, mid-window
    for payload in (None, {}):
        assert "never been published" in gamma.net_prem_status_text(payload, now)


def test_net_prem_status_text_fresh_but_empty_is_not_collected_yet():
    now = _ct(2026, 8, 5, 8, 5)
    payload = {"session_date": "2026-08-05", "ts": _iso(_ct(2026, 8, 5, 8, 4)),
               "series": {}, "error": None}
    txt = gamma.net_prem_status_text(payload, now)
    assert "not collected yet" in txt
    assert "stale" not in txt.lower()


def test_net_prem_status_text_stale_inside_the_collection_window_is_an_error():
    now = _ct(2026, 8, 5, 11, 0)          # Wednesday, inside 08:00-15:20 CT
    payload = {"session_date": "2026-08-05", "ts": _iso(_ct(2026, 8, 5, 10, 50)),
               "series": _np_series(), "error": None}
    txt = gamma.net_prem_status_text(payload, now)
    assert "stale" in txt.lower()


def test_net_prem_status_text_stale_outside_the_window_is_not_an_error():
    # Off-hours the key legitimately holds the last tick -- correct persistence,
    # matching the heatmap/Flow views. Never flag it.
    payload = {"session_date": "2026-08-05", "ts": _iso(_ct(2026, 8, 5, 15, 19)),
               "series": _np_series(), "error": None}
    for now in (_ct(2026, 8, 5, 19, 0),        # same evening
                _ct(2026, 8, 8, 11, 0),        # Saturday, inside the clock window
                _ct(2026, 8, 5, 7, 30)):       # weekday, before the window opens
        txt = gamma.net_prem_status_text(payload, now)
        assert "stale" not in txt.lower(), now


def test_net_prem_status_text_not_stale_on_a_market_holiday():
    # Jan 1 2027 is an NYSE full closure that falls on a Friday inside the clock
    # window -- nothing collects, so a day-old payload is correct, not broken.
    payload = {"session_date": "2026-12-31", "ts": _iso(_ct(2026, 12, 31, 15, 19)),
               "series": _np_series(), "error": None}
    txt = gamma.net_prem_status_text(payload, _ct(2027, 1, 1, 11, 0))
    assert "stale" not in txt.lower()


def test_net_prem_status_text_normal_publish_reports_the_symbol_count():
    now = _ct(2026, 8, 5, 11, 0)
    payload = {"session_date": "2026-08-05", "ts": _iso(_ct(2026, 8, 5, 10, 59)),
               "series": _np_series(), "error": None}
    txt = gamma.net_prem_status_text(payload, now)
    # "collected", not "symbols": the summary line beside this one counts the
    # SELECTION ("N symbols plotted"), so the two must not look like one number.
    assert "2 collected" in txt
    assert "2026-08-05" in txt and "stale" not in txt.lower()


def test_net_prem_status_text_renders_the_service_error_verbatim():
    # House pattern (matrix.status_text): the error string is user-facing UI copy
    # rendered as-is -- never matched on.
    now = _ct(2026, 8, 5, 11, 0)
    payload = {"session_date": "2026-08-05", "ts": _iso(_ct(2026, 8, 5, 10, 59)),
               "series": {}, "error": "net premium unavailable"}
    assert "net premium unavailable" in gamma.net_prem_status_text(payload, now)


def test_net_prem_status_text_tolerates_a_junk_timestamp():
    now = _ct(2026, 8, 5, 11, 0)
    for ts in (None, "", "not-a-date", 12345):
        assert gamma.net_prem_status_text({"ts": ts, "series": {}}, now)


def test_net_prem_modes_label_both_axes():
    assert set(gamma.NET_PREM_MODES) == {"dollars", "skew"}
    # An unknown mode degrades to dollars rather than raising.
    assert gamma.net_prem_value([_T1, 3.0e6, 1.0e6], "bogus") == 2.0


def test_net_prem_rows_are_sorted_by_timestamp():
    # Highcharts line data MUST be x-ascending. Losing the sort corrupts the
    # chart SILENTLY rather than raising, and every other fixture here is already
    # in order -- so this is the only thing holding it.
    desc = {"SPY": [[_T3, 5.0e6, 1.0e6], [_T1, 1.0e6, 3.0e6], [_T2, 2.0e6, 2.0e6]]}
    fig = gamma.net_prem_figure(desc, ["SPY"])
    assert fig["xAxis"]["categories"] == [gamma._fmt_ts(t) for t in (_T1, _T2, _T3)]
    data = fig["series"][0]["data"]
    assert [p[0] for p in data] == [0, 1, 2]
    assert [p[1] for p in data] == [-2.0, 0.0, 4.0]
    # ...and "latest" must be the NEWEST point, not the last one listed.
    assert "+$4.0M" in gamma.net_prem_summary_text(desc, ["SPY"])


def test_net_prem_missing_is_mode_aware_and_agrees_with_the_summary():
    # Parseable rows that draw NO skew line: (0, 0) has no ratio to report. A
    # mode-blind "missing" would call SPY present while the chart showed nothing.
    series = {"SPY": [[_T1, 0.0, 0.0]]}
    assert gamma.net_prem_missing(series, ["SPY"], "dollars") == []
    assert gamma.net_prem_missing(series, ["SPY"], "skew") == ["SPY"]
    assert gamma.net_prem_figure(series, ["SPY"], "skew")["series"] == []
    # ONE definition of missing -- the header cannot contradict the chart.
    assert "no data yet: SPY" in gamma.net_prem_summary_text(series, ["SPY"], "skew")
    assert "no data yet" not in gamma.net_prem_summary_text(series, ["SPY"], "dollars")


def test_net_prem_summary_adjectives_never_contradict_the_sign():
    # Whole selection put-led: the top of the range is the LEAST put-led one --
    # "most call-led SPY -$4.0M" would read as a self-contradiction.
    down = {"SPY": [[_T1, 1.0e6, 5.0e6]], "QQQ": [[_T1, 1.0e6, 9.0e6]]}
    txt = gamma.net_prem_summary_text(down, ["SPY", "QQQ"])
    assert "least put-led SPY -$4.0M" in txt and "most put-led QQQ -$8.0M" in txt
    assert "most call-led" not in txt

    up = {"SPY": [[_T1, 5.0e6, 1.0e6]], "QQQ": [[_T1, 3.0e6, 1.0e6]]}
    txt = gamma.net_prem_summary_text(up, ["SPY", "QQQ"])
    assert "most call-led SPY +$4.0M" in txt and "least call-led QQQ +$2.0M" in txt
    assert "most put-led" not in txt

    # Straddling zero keeps both plain superlatives.
    mixed = gamma.net_prem_summary_text(_np_series(), ["QQQ", "SPY"])
    assert "most call-led QQQ" in mixed and "most put-led SPY" in mixed


def test_net_prem_selection_drops_junk_and_unknown_entries():
    # The selection is PERSISTED to webgui/data/settings.json -- tracked,
    # hand-editable, no type validation on read -- so it is untrusted input.
    series = _np_series()
    for junk in ([123, "SPY"], [None, "SPY"], [{"a": 1}, "SPY"], ["FB", "SPY"]):
        assert gamma.net_prem_missing(series, junk) == [], junk
        assert [s["name"] for s in
                gamma.net_prem_figure(series, junk)["series"]] == ["SPY"], junk
        # A non-str would raise TypeError at ", ".join(missing) and 500 the page.
        assert gamma.net_prem_summary_text(series, junk), junk
    # A stale saved ticker no longer in the groups is dropped too, so a caller's
    # `key=order.index` sort cannot raise ValueError on it.
    assert gamma.net_prem_missing(series, ["FB"]) == []
    assert gamma.net_prem_summary_text(series, ["FB"]).startswith("Select")


def test_net_prem_status_text_staleness_boundary_is_two_minutes():
    import datetime as _dt
    now = _ct(2026, 8, 5, 11, 0)

    def at(age_sec):
        payload = {"session_date": "2026-08-05", "series": _np_series(),
                   "ts": _iso(now - _dt.timedelta(seconds=age_sec)), "error": None}
        return gamma.net_prem_status_text(payload, now).lower()

    assert "stale" not in at(119)      # inside 2x the 1-min publish cadence
    assert "stale" in at(121)


def test_net_prem_status_text_window_boundaries():
    payload = {"session_date": "2026-08-05", "series": _np_series(),
               "ts": _iso(_ct(2026, 8, 5, 5, 0)), "error": None}   # hours stale

    def stale_at(hh, mm):
        return "stale" in gamma.net_prem_status_text(
            payload, _ct(2026, 8, 5, hh, mm)).lower()

    assert not stale_at(7, 59)      # before the window opens
    assert stale_at(8, 0)           # open is INCLUSIVE
    assert stale_at(15, 19)         # last minute inside
    assert not stale_at(15, 20)     # close is EXCLUSIVE


def test_net_prem_status_text_tolerates_a_naive_now_and_a_non_dict_payload():
    import datetime as _dt
    payload = {"session_date": "2026-08-05", "series": _np_series(),
               "ts": _iso(_ct(2026, 8, 5, 10, 59)), "error": None}
    assert gamma.net_prem_status_text(payload, _dt.datetime(2026, 8, 5, 16, 0))
    for junk in ("notadict", [1, 2], 42):
        assert "never been published" in gamma.net_prem_status_text(
            junk, _ct(2026, 8, 5, 11, 0)), junk


# --- view identity for the single full-width chart element -------------------

def test_view_order_puts_net_prem_between_flow_and_term():
    """Flow and Net Prem are the two options-FLOW views, so they sit together."""
    order = gamma._VIEW_ORDER
    assert order[-3:] == ["Flow", "Net Prem", "Term"]
    for v in gamma._VIEWS:
        assert v in order


def test_chart_kind_separates_flow_from_net_prem():
    """THE trap: both are ``chart.type == "line"``, so keying the recreate-vs-
    update decision on the type alone would take the in-place path and merge
    Net Prem's single yAxis dict onto Flow's 3 banded axes — leaving two
    orphaned axes painted and the plot squeezed into the top 62%."""
    flow = gamma.flow_figure([{"ts": 1, "spot": 1.0, "call_prem": 2.0, "put_prem": 1.0}])
    netp = gamma.net_prem_figure({"SPY": [[1, 2.0, 1.0]]}, ["SPY"])
    assert flow["chart"]["type"] == netp["chart"]["type"] == "line"   # the trap
    assert isinstance(flow["yAxis"], list) and isinstance(netp["yAxis"], dict)
    assert gamma.chart_kind(flow) != gamma.chart_kind(netp)


def test_chart_kind_is_stable_across_same_view_repaints():
    """A Net Prem repaint with a different SELECTION (so a different series
    count) must stay the same kind — else every checkbox tick would tear the
    element down and flash."""
    series = {"SPY": [[1, 2.0, 1.0]], "QQQ": [[1, 3.0, 1.0]], "$SPX": [[1, 1.0, 4.0]]}
    one = gamma.net_prem_figure(series, ["SPY"])
    three = gamma.net_prem_figure(series, ["SPY", "QQQ", "$SPX"])
    assert len(one["series"]) != len(three["series"])
    assert gamma.chart_kind(one) == gamma.chart_kind(three)
    # ...and Net Prem in the two modes is one kind too (only the axis title moves).
    assert gamma.chart_kind(gamma.net_prem_figure(series, ["SPY"], "skew")) == \
        gamma.chart_kind(one)


# One representative figure builder per VIEW. Registered here rather than
# hand-listed inside a single assertion so that adding a view to _VIEW_ORDER
# without registering it FAILS — chart_kind is a structural proxy (a future
# single-axis "line" view would compute ('line', 0) and silently collide with
# Net Prem, reintroducing the merge-leak bug class), so the distinctness guard
# has to be forget-proof rather than depend on someone remembering to extend it.
_VIEW_FIGS = {
    "GEX": lambda: gamma.bar_figure({"spot": 100.0, "gex": {100.0: {"net": 1.0}}}, 100.0,
                                    view="GEX"),
    "Charm": lambda: gamma.bar_figure({"spot": 100.0, "gex": {100.0: {"net": 1.0}}}, 100.0,
                                      view="Charm"),
    "DEX": lambda: gamma.bar_figure({"spot": 100.0, "gex": {100.0: {"net": 1.0}}}, 100.0,
                                    view="DEX"),
    "Vanna": lambda: gamma.bar_figure({"spot": 100.0, "gex": {100.0: {"net": 1.0}}}, 100.0,
                                      view="Vanna"),
    "Flow": lambda: gamma.flow_figure([]),
    "Net Prem": lambda: gamma.net_prem_figure({}, []),
    "Term": lambda: gamma.term_heatmap({"expirations": ["2026-08-07"],
                                        "cells": {"100.0": {"2026-08-07": 1.0}}}),
}

# Views that SHARE a figure builder must share a kind (no needless recreate);
# views with DIFFERENT builders must not collide (no merge leak).
_VIEW_BUILDER = {"GEX": "bars", "Charm": "bars", "DEX": "bars", "Vanna": "bars",
                 "Flow": "flow", "Net Prem": "netprem", "Term": "term"}


def test_every_view_is_registered_for_the_chart_kind_guard():
    """A new subtab must be registered here, so the guards below cover it."""
    assert set(_VIEW_FIGS) == set(gamma._VIEW_ORDER)
    assert set(_VIEW_BUILDER) == set(gamma._VIEW_ORDER)


def test_chart_kind_is_distinct_across_every_pair_of_builders():
    """Any two views drawn by DIFFERENT builders must be different kinds — else
    _set_chart takes the in-place path and Highcharts merges one figure's config
    onto the other's (the Flow/Net Prem orphaned-axis bug)."""
    by_builder = {}
    for view, make in _VIEW_FIGS.items():
        by_builder.setdefault(_VIEW_BUILDER[view], gamma.chart_kind(make()))
    assert len(set(by_builder.values())) == len(by_builder), by_builder


def test_views_sharing_a_builder_share_a_kind():
    """GEX/Charm/DEX/Vanna are all bar figures: switching between them must NOT
    recreate the element (that would reintroduce flicker where there is none)."""
    bars = {v: gamma.chart_kind(_VIEW_FIGS[v]())
            for v, b in _VIEW_BUILDER.items() if b == "bars"}
    assert len(set(bars.values())) == 1, bars
    # ...and the first-paint empty figure matches them, so the very first real
    # render updates in place instead of tearing the element down.
    assert gamma.chart_kind(gamma._empty_fig()) == next(iter(bars.values()))


def test_net_prem_groups_are_disjoint():
    """np_boxes is keyed by SYMBOL, so a symbol in two groups would overwrite its
    own checkbox — orphaning the first widget, which is then never wired to
    on_value_change nor visibility-toggled and would sit visible on every view.
    net_prem_symbols() already dedupes, so the two paths must not disagree."""
    seen = {}
    for group in gamma.NET_PREM_GROUPS:
        for sym in group["symbols"]:
            assert sym not in seen, f"{sym} in both {seen[sym]} and {group['key']}"
            seen[sym] = group["key"]
    assert len(seen) == len(gamma.net_prem_symbols())


def test_chart_kind_is_total_over_junk():
    """It runs on every repaint; a malformed figure must not 500 the page."""
    for junk in (None, "nope", [], {}, {"chart": None}, {"chart": {}, "yAxis": "x"}):
        gamma.chart_kind(junk)


# --- closure-bound wiring (guarded by source inspection, as test_driver_monitor
# --- does: these live inside render()'s closure and cannot be called directly).

def test_tick_refreshes_the_net_prem_status_line():
    """Staleness is a CLOCK function, but np_status_lbl was only recomputed on a
    repaint — whose drivers are all cache-version bumps. So the one failure the
    line exists to report (the whole options service down, no key bumping at all)
    would freeze it at its last-good "updated HH:MM" forever. The 1 s _tick, which
    is already running, must recompute it."""
    import inspect
    src = inspect.getsource(gamma.render)
    tick = src[src.index("def _tick("):]
    tick = tick[:tick.index("\n    @guard_async")]
    assert "_paint_np_status()" in tick, tick
    # ...and it must be a pure recompute, not a bus read on a 1 s timer.
    paint = src[src.index("def _paint_np_status("):]
    paint = paint[:paint.index("\n    def _render_net_prem(")]
    assert "bus_client" not in paint and "io_bound" not in paint, paint


def test_auto_refresh_skips_the_chain_fetch_on_net_prem():
    """Net Prem never reads the gamma snapshot, so the 120 s gamma_refresh would
    cost the options service a full option-chain fetch + GammaEngine compute for
    a result this view discards."""
    import inspect
    src = inspect.getsource(gamma.render)
    fn = src[src.index("def _auto_refresh("):]
    fn = fn[:fn.index("\n    @guard\n    def _tick(")]
    assert 'view_toggle.value == "Net Prem"' in fn, fn
    # The guard must precede the enqueue, or it does nothing.
    assert fn.index('"Net Prem"') < fn.index("gamma_refresh"), fn


# --- Projected EOD delta-flip line (0-DTE charm drift), on every view ---

def test_wall_plot_lines_adds_projected_flip():
    lines = gamma.wall_plot_lines(450.0, [455.0], flip=449.5, projected_flip=452.25)
    by_val = {pl["value"]: pl for pl in lines}
    assert set(by_val) == {455.0, 449.5, 452.25}
    pf = by_val[452.25]
    # Labeled so it can't be mistaken for the view's own (actual) flip.
    assert "Proj" in pf["label"]["text"] and "452.25" in pf["label"]["text"]
    # Its own color, distinct from the actual flip and the walls.
    assert pf["color"] == gamma.PROJ_FLIP_COLOR
    assert pf["color"] not in (gamma.FLIP_COLOR, gamma.WALL_COLOR)


def test_projected_flip_is_optional_and_defensive():
    # Absent (most symbols never have a 0-DTE expiry) -> no line, no raise.
    base = gamma.wall_plot_lines(450.0, [], flip=449.5)
    assert len(base) == 1
    assert gamma.wall_plot_lines(450.0, [], flip=449.5, projected_flip=None) == base
    assert gamma.wall_plot_lines(450.0, [], flip=449.5, projected_flip="x") == base


def test_heatmap_figure_draws_projected_flip_on_any_view():
    # The projected flip is a DEX 0-DTE concept but is drawn on EVERY view as a
    # shared reference level, alongside that view's own flip.
    for view in ("GEX", "Charm", "DEX", "Vanna"):
        fig = gamma.heatmap_figure(_WALL_ROWS, view, spot=450.0, flip=449.5,
                                   projected_flip=452.25)
        vals = [pl["value"] for pl in fig["yAxis"]["plotLines"]]
        assert 452.25 in vals, view


# --- Hedge-pressure history panel (0-DTE charm drift over the session) ---

_HEDGE = [{"ts": 1, "hedge_pressure": 1.5e9, "net_delta_0dte": 5e10, "projected_flip": 99.0},
          {"ts": 2, "hedge_pressure": -2.5e9, "net_delta_0dte": 5e10, "projected_flip": 98.0},
          {"ts": 3, "hedge_pressure": 3.0e9, "net_delta_0dte": 5e10, "projected_flip": 99.5}]


def test_hedge_figure_plots_pressure_in_billions_signed():
    pts = gamma.hedge_figure(_HEDGE, ["09:30", "09:31", "09:32"])["series"][0]["data"]
    # Dollars are unreadable raw; the axis is $B and the sign is the whole point
    # (positive = dealers must BUY into the close, negative = sell).
    assert [p["y"] for p in pts] == [1.5, -2.5, 3.0]
    assert [p["x"] for p in pts] == [0, 1, 2]          # shares the heatmap's x index
    # Colored PER POINT by sign, so ONE series carries both and the flip from
    # buy- to sell-pressure is visible at a glance.
    assert pts[0]["color"] == gamma.UP_COLOR
    assert pts[1]["color"] == gamma.DOWN_COLOR


def test_hedge_figure_empty_is_safe():
    for arg in ([], None):
        fig = gamma.hedge_figure(arg, [])
        assert fig["series"][0]["data"] == []


def test_hedge_summary_text_reads_direction_and_size():
    txt = gamma.hedge_summary_text(_HEDGE)
    assert "+$3.00B" in txt and "buy" in txt.lower()      # last value drives the read
    assert gamma.hedge_summary_text([]) == ""
    down = gamma.hedge_summary_text([{"ts": 1, "hedge_pressure": -1.2e9}])
    assert "-$1.20B" in down and "sell" in down.lower()


# --- Projected DEX bars (each strike's own 0-DTE charm drift) ---

_DRIFT_DATA = {
    "spot": 450.0,
    "gex": {448.0: {"call": 100.0, "put": -40.0, "net": 60.0},
            450.0: {"call": 200.0, "put": -250.0, "net": -50.0},
            452.0: {"call": 30.0, "put": -10.0, "net": 20.0}},
    # Only the 0-DTE strikes carry drift; 452 has none.
    "hedge_drift_by_strike": {448.0: 15.0, 450.0: -30.0},
}


def test_bars_from_gex_adds_projected_where_drift_exists():
    b = gamma.bars_from_gex(_DRIFT_DATA, 450.0)
    assert b["nets"] == [60.0, -50.0, 20.0]
    # projected = net + that strike's OWN drift; None where the strike has no
    # 0-DTE interest, so the chart can skip drawing a coincident outline.
    assert b["projected"] == [75.0, -80.0, None]


def test_bars_from_gex_projected_absent_without_drift_map():
    # Most symbols never have a 0-DTE book -> no drift map -> all None, no raise.
    plain = {k: v for k, v in _DRIFT_DATA.items() if k != "hedge_drift_by_strike"}
    assert gamma.bars_from_gex(plain, 450.0)["projected"] == [None, None, None]
    assert gamma.bars_from_gex({}, None)["projected"] == []


def test_bar_figure_overlays_a_projected_outline_series():
    fig = gamma.bar_figure(_DRIFT_DATA, 450.0, view="DEX")
    names = [s["name"] for s in fig["series"]]
    assert "Projected close" in names
    proj = next(s for s in fig["series"] if s["name"] == "Projected close")
    # Only the two drifting strikes are drawn.
    assert [p["x"] for p in proj["data"]] == [448.0, 450.0]
    assert [p["y"] for p in proj["data"]] == [75.0, -80.0]
    # Outline only (transparent fill) so it reads over the solid bar whether the
    # projection EXTENDS past it or pulls back inside it.
    assert proj["color"] == "transparent"
    assert proj["borderColor"] == gamma.PROJ_FLIP_COLOR
    # Drawn on TOP of the solid bars, and overlaid (not grouped beside them).
    assert names.index("Projected close") > names.index(gamma._view_label("DEX"))
    assert fig["plotOptions"]["bar"]["grouping"] is False


def test_bar_figure_omits_projected_series_when_no_drift():
    plain = {k: v for k, v in _DRIFT_DATA.items() if k != "hedge_drift_by_strike"}
    fig = gamma.bar_figure(plain, 450.0, view="DEX")
    assert [s["name"] for s in fig["series"]] == [gamma._view_label("DEX")]
