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
    b = gamma.bars_from_gex(GEX, 450.0, pct=0.01)
    assert b["strikes"] == [448.0, 450.0, 452.0]
    assert b["nets"] == [60.0, -50.0, 20.0]
    assert b["colors"][1] != b["colors"][0]


def test_bars_from_gex_excludes_out_of_band():
    b = gamma.bars_from_gex(GEX, 450.0, pct=0.001)
    assert b["strikes"] == [450.0]


def test_bar_figure_is_plotly_dict():
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", walls=[450.0], flip=449.5, pct=0.02)
    assert "data" in fig and "layout" in fig


def test_bar_figure_yaxis_hugs_visible_bars_and_is_tall():
    # spot*0.95..1.05 would be 427.5..472.5; visible bars only span 448..452
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", pct=0.02)
    lo, hi = fig["layout"]["yaxis"]["range"]
    assert lo > 440.0 and hi < 460.0          # tight to the bars, not spot*0.95/1.05
    assert lo <= 448.0 and hi >= 452.0        # outermost bars not clipped
    assert fig["layout"]["height"] >= 600     # fills the lower screen


def test_bar_yrange_empty_falls_back_to_spot_band():
    assert gamma.bar_yrange([], 450.0) == [450.0 * 0.98, 450.0 * 1.02]


def test_panel_flex_endpoints_and_monotonic():
    bar0, heat0 = gamma.panel_flex(0)
    assert heat0 == 0.28 and round(bar0 + heat0, 4) == 1.0      # session start: bars wide
    barf, heatf = gamma.panel_flex(82)
    assert heatf == 0.70 and round(barf + heatf, 4) == 1.0      # full session: heat wide
    # clamps past a full session
    assert gamma.panel_flex(200) == gamma.panel_flex(82)
    # heat fraction is non-decreasing with more snapshots
    heats = [gamma.panel_flex(n)[1] for n in range(0, 90, 10)]
    assert heats == sorted(heats)
    # midpoint is between the endpoints
    _, heat_mid = gamma.panel_flex(41)
    assert 0.28 < heat_mid < 0.70


def test_significant_strikes_crops_near_zero_tails():
    bars = {"strikes": [440.0, 448.0, 450.0, 452.0, 460.0],
            "nets": [5.0, 600.0, -900.0, 500.0, 8.0]}   # tails ≈ 0 vs 900 peak
    assert gamma.significant_strikes(bars, frac=0.03) == [448.0, 450.0, 452.0]


def test_significant_strikes_noop_when_all_significant():
    bars = {"strikes": [448.0, 450.0, 452.0], "nets": [600.0, -900.0, 500.0]}
    assert gamma.significant_strikes(bars, frac=0.03) == [448.0, 450.0, 452.0]


def test_significant_strikes_all_zero_returns_all():
    bars = {"strikes": [448.0, 450.0], "nets": [0.0, 0.0]}
    assert gamma.significant_strikes(bars) == [448.0, 450.0]
    assert gamma.significant_strikes({"strikes": [], "nets": []}) == []


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
    types = [t["type"] for t in fig["data"]]
    assert "heatmap" in types and "scatter" in types
    spot_trace = next(t for t in fig["data"] if t["type"] == "scatter")
    assert spot_trace["y"] == [450.0, 451.5]
    assert spot_trace["x"] == ["09:30", "09:35"]


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
    assert fig["data"][0]["x"] == ["2026-06-18", "2026-06-19"]
    assert 450.0 in fig["data"][0]["y"]
    assert 451.0 not in fig["data"][0]["y"]   # all-zero strike filtered out


def test_term_heatmap_empty():
    assert gamma.term_heatmap({})["data"][0]["z"] == []


def test_wrap_explain_fragment_and_document():
    body = "<h2>GAMMA EXPOSURE (GEX)</h2><p>hi</p>"
    frag = gamma.wrap_explain("$SPX", body, full=False)
    assert "gx-explain" in frag and body in frag and "$SPX" in frag
    assert not frag.lstrip().startswith("<!DOCTYPE")
    doc = gamma.wrap_explain("$SPX", body, full=True)
    assert doc.lstrip().startswith("<!DOCTYPE") and body in doc


def test_summary_text_mentions_spot_and_flip():
    txt = gamma.summary_text({"spot": 450.0, "flip": 449.5, "net_total": 1234.0,
                              "strike_count": 3}, "GEX")
    assert "450" in txt and "449.5" in txt


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
    b = gamma.bars_from_gex(fixed, 450.0, pct=0.01)
    assert b["strikes"] == [448.0, 450.0, 452.0]      # numeric, sorted, in range
    assert b["nets"] == [60.0, -50.0, 20.0]


# ── Charts refresh (2026-06-16): dark theme, beveled bars, line labels,
#    relabeled views, heatmap separators/contrast ──────────────────────────────
def test_view_label_renames_gex_and_dex():
    assert gamma._view_label("GEX") == "GAMMA"
    assert gamma._view_label("DEX") == "DELTA"
    assert gamma._view_label("Charm") == "Charm"
    assert gamma._view_label("Term") == "Term"


def test_dark_layout_sets_dark_backgrounds():
    layout = gamma._apply_dark({"xaxis": {"title": "x"}, "yaxis": {}})
    assert layout["paper_bgcolor"] == gamma.DARK_BG
    assert layout["plot_bgcolor"] == gamma.DARK_BG
    assert layout["font"]["color"] == gamma.FONT
    assert layout["xaxis"]["title"] == "x"          # existing keys preserved
    assert "gridcolor" in layout["xaxis"]


def test_bar_figure_is_dark_and_beveled_with_friendly_title():
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", walls=[452.0], flip=449.5, pct=0.02)
    assert fig["layout"]["paper_bgcolor"] == gamma.DARK_BG
    marker = fig["data"][0]["marker"]
    assert "line" in marker and marker["line"]["width"] >= 1   # beveled border
    assert isinstance(marker["line"]["color"], list)           # per-bar darker shade
    assert "GAMMA" in fig["layout"]["title"]                   # friendly label, not "GEX"


def test_line_annotations_label_spot_flip_and_call_put_walls():
    anns = gamma.line_annotations(450.0, 449.0, [455.0, 445.0])
    texts = [a["text"] for a in anns]
    assert any(t.startswith("Spot") for t in texts)
    assert any("Gamma flip" in t for t in texts)
    assert any("Call wall" in t for t in texts)   # 455 >= spot
    assert any("Put wall" in t for t in texts)    # 445 < spot


def test_bar_figure_includes_reference_line_annotations():
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", walls=[452.0], flip=449.5, pct=0.02)
    texts = [a["text"] for a in fig["layout"].get("annotations", [])]
    assert any(t.startswith("Spot") for t in texts)


def test_bar_figure_accepts_explicit_yrange():
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", pct=0.02, yrange=[400.0, 500.0])
    assert fig["layout"]["yaxis"]["range"] == [400.0, 500.0]


def test_darker_returns_a_darker_hex():
    assert gamma._darker("#66bb6a") != "#66bb6a"
    assert gamma._darker("#66bb6a").startswith("#") and len(gamma._darker("#66bb6a")) == 7


def test_heatmap_figure_dark_with_cell_separators_and_concise_hover():
    rows = [("09:30", 450, None, None, None, 0, {448.0: 5, 450.0: -3})]
    fig = gamma.heatmap_figure(rows, "GEX", yrange=[440.0, 460.0])
    d, lay = fig["data"][0], fig["layout"]
    assert d["xgap"] == 1 and d["ygap"] == 1            # faint cell separators
    assert "hovertemplate" in d                          # concise hover
    assert lay["paper_bgcolor"] == gamma.DARK_BG
    assert lay["yaxis"]["range"] == [440.0, 460.0]       # aligned to bars


def test_term_heatmap_dark_separators_and_contrast():
    tg = {"underlying_price": 450.0, "expirations": ["2026-06-18"],
          "cells": {"2026-06-18": {450.0: {"net_gex_usd": 5},
                                   460.0: {"net_gex_usd": -200}}}}
    fig = gamma.term_heatmap(tg)
    d, lay = fig["data"][0], fig["layout"]
    assert d["xgap"] == 1 and d["ygap"] == 1
    assert lay["paper_bgcolor"] == gamma.DARK_BG
    # symmetric contrast clamp present
    assert d.get("zmax") is not None and d.get("zmin") == -d["zmax"]


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
    assert "update_figure" in src
    assert "chart_box.clear()" not in src
    assert "heatmap_box.clear()" not in src
    assert "panel_flex" in src           # proportional split is wired
    assert "significant_strikes" in src  # tight y-range is wired


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
