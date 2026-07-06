"""Pure-transform tests for the Sentiment page."""
import bus_client
from pages import sentiment as S


def _snap(date, total, **comp):
    base = {"vix_complex": 5, "put_call": 5, "breadth": 5,
            "rotation": 5, "sector_perf": 5, "credit_pulse": 5}
    base.update(comp)
    return {
        "date": date,
        "composite": {"total_score": f"{total:.2f}", "bias": "Neutral",
                      "size_modifier": "1.00x", "aggregate_confidence": 0.8},
        "component_scores": base,
        "component_confidence": {k: 0.9 for k in base},
    }


def test_gauge_score_scales_0_10_to_0_100():
    assert S.gauge_score("7.5") == 75.0
    assert S.gauge_score(0) == 0.0
    assert S.gauge_score("bad") == 0.0


def test_gauge_figure_reexported_for_render():
    # gauge_figure is the shared pages.gauge builder, re-exported here because
    # render() uses the bare name; its behavior is covered by test_gauge.py.
    from pages import gauge
    assert S.gauge_figure is gauge.gauge_figure


def test_bias_color_buckets():
    assert S.bias_color("Bullish") == S.CLR_GREEN
    assert S.bias_color("Bearish") == S.CLR_RED
    assert S.bias_color("Neutral") == S.CLR_YELLOW


# ── Local Tailwind color-class maps (Phase 5) — exact local palette preserved ──
def test_local_class_constants_mirror_palette():
    assert S.TXT_G == "text-[#66bb6a]" and S.TXT_R == "text-[#ef5350]"
    assert S.TXT_Y == "text-[#ffd54f]" and S.TXT_FLAT == "text-[#9e9e9e]"
    assert S.TXT_CY == "text-[#3fb6c7]"
    assert S.BG_G == "bg-[#66bb6a]" and S.BG_R == "bg-[#ef5350]" and S.BG_Y == "bg-[#ffd54f]"
    # remove-sets cover every class an in-place element can apply
    assert set(S.SENT_TEXT_CLASSES.split()) == {S.TXT_G, S.TXT_R, S.TXT_Y, S.TXT_FLAT, S.TXT_CY}
    assert set(S.TRAFFIC_BG_CLASSES.split()) == {S.BG_G, S.BG_R, S.BG_Y}


def test_traffic_bg_class_maps_bands():
    assert S.traffic_bg_class(7) == "bg-[#66bb6a]"
    assert S.traffic_bg_class(4) == "bg-[#ef5350]"
    assert S.traffic_bg_class(5.5) == "bg-[#ffd54f]"


def test_bias_text_class_buckets():
    assert S.bias_text_class("Bullish") == "text-[#66bb6a]"
    assert S.bias_text_class("Bearish") == "text-[#ef5350]"
    assert S.bias_text_class("Neutral") == "text-[#ffd54f]"


def test_pct_text_class():
    assert S.pct_text_class(0.5) == "text-[#66bb6a]"
    assert S.pct_text_class(-0.5) == "text-[#ef5350]"
    assert S.pct_text_class(0.0) == "text-[#9e9e9e]"
    assert S.pct_text_class(None) == "text-[#9e9e9e]"


def test_pcr_text_class_and_rrg_text_class():
    assert S.pcr_text_class(0.9) == "text-[#66bb6a]"
    assert S.pcr_text_class(1.1) == "text-[#ef5350]"
    assert S.pcr_text_class(1.0) == "text-[#9e9e9e]"
    assert S.rrg_text_class("Improving") == "text-[#3fb6c7]"
    assert S.rrg_text_class("Lagging") == "text-[#ef5350]"
    assert S.rrg_text_class("Leading") == "text-[#66bb6a]"
    assert S.rrg_text_class("Weakening") == "text-[#ffd54f]"
    assert S.rrg_text_class("???") == "text-[#9e9e9e]"


def test_sc_text_class():
    assert S.sc_text_class(7) == "text-[#66bb6a]"
    assert S.sc_text_class(3) == "text-[#ef5350]"
    assert S.sc_text_class(5) == "text-[#ffd54f]"


def test_trend_text_class():
    assert S.trend_text_class("bull_trend") == "text-[#66bb6a]"
    assert S.trend_text_class("pullback_in_bull") == "text-[#66bb6a]"
    assert S.trend_text_class("bear_trend") == "text-[#ef5350]"
    assert S.trend_text_class("bear_rally") == "text-[#ef5350]"
    assert S.trend_text_class("range") == "text-[#ffd54f]"


def test_rotation_text_class():
    assert S.rotation_text_class(S.CLR_GREEN) == "text-[#66bb6a]"
    assert S.rotation_text_class(S.CLR_RED) == "text-[#ef5350]"
    assert S.rotation_text_class(S.CLR_YELLOW) == "text-[#ffd54f]"
    assert S.rotation_text_class(S.CLR_FLAT) == "text-[#9e9e9e]"


# ── Market Trend speedometer (needle = the directional 0-100 trend score) ─────
def test_trend_gauge_value_uses_score_directly():
    assert S.trend_gauge_value({"score": 84.0}) == 84.0
    assert S.trend_gauge_value({"smoothed_score": 62.5, "score": 70}) == 62.5  # prefers smoothed
    assert S.trend_gauge_value({"score": 0.0}) == 0.0                          # 0 is valid
    assert S.trend_gauge_value(None) == 50.0
    assert S.trend_gauge_value({}) == 50.0
    assert S.trend_gauge_value({"score": 150}) == 100.0                        # clamped


def test_trend_subscore_rows():
    rows = S.trend_subscore_rows({
        "sub_scores": {"price": 88, "breadth": 80, "sector": 82, "vix": 70},
        "sub_confidence": {"price": 1.0, "breadth": 0.9, "sector": 1.0, "vix": 1.0}})
    assert len(rows) == 4
    assert {"name": "Price / MTF", "score": "88.0", "weight": "45%", "conf": "1.00"} in rows


def test_trend_subscore_rows_skips_missing_and_handles_empty():
    assert S.trend_subscore_rows(None) == []
    assert S.trend_subscore_rows({}) == []
    rows = S.trend_subscore_rows({"sub_scores": {"price": 60, "sector": 55}})  # 30d shape
    assert [r["name"] for r in rows] == ["Price / MTF", "Sector"]
    assert rows[0]["conf"] == "0.00"   # missing sub_confidence -> 0.00


def test_composite_series_filters_zeros_and_blanks():
    snaps = [_snap("2026-06-01", 6.0), _snap("2026-06-02", 0.0),
             _snap("2026-06-03", 7.0)]
    dates, scores = S.composite_series(snaps)
    assert scores == [6.0, 7.0]
    assert dates == ["2026-06-01", "2026-06-03"]


def test_page_imports_no_app_scoring():
    """Regression for the cross-app ``scoring`` collision: the page module must
    NOT carry any app ``scoring``/``live_composite``/trend_regime references —
    those now live only in the service. Importing the page (even with options'
    ``scoring`` already bound process-wide) must not need ``WEIGHTS``."""
    assert not hasattr(S, "scoring_composite")
    assert not hasattr(S, "scoring_sector")
    assert not hasattr(S, "signal_band")
    assert not hasattr(S, "trend_regime")
    assert not hasattr(S, "WEIGHTS")
    # Removed scoring-glue helpers are gone (computed in the service now).
    assert not hasattr(S, "velocity_line")
    assert not hasattr(S, "divergence_named")
    assert not hasattr(S, "commit_trend_regime")


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (service not running / cold start) — the Tier-3 graceful-empty path.

    The webgui suite has no NiceGUI User fixture; rendering inside a slot
    context (a card) is enough to exercise the widget wiring + initial paint.
    """
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("sentiment:composite") is None  # confirm empty
    with ui.card():
        S.render()  # must not raise


def test_sentiment_30d_avg():
    from pages import sentiment as S
    snaps = [{"composite": {"total_score": "6.0"}},
             {"composite": {"total_score": "0.0"}},   # zero filtered out
             {"composite": {"total_score": "8.0"}}]
    assert S.sentiment_30d_avg(snaps) == 7.0          # mean(6,8)
    assert S.sentiment_30d_avg([]) == 0.0


# ── Colorized intraday figures (Task 4) ──────────────────────────────────────
_PTS = [{"ts": 1000, "sentiment": 3.0, "trend": 20.0},
        {"ts": 1120, "sentiment": 6.0, "trend": 55.0},
        {"ts": 1240, "sentiment": 8.0, "trend": 85.0}]


def test_sentiment_intraday_figure_uses_sequential_index_slots():
    # Points map to sequential integer slots (a synthetic category axis packs the
    # trading days contiguously — no overnight dead space). Each point carries its
    # value in ``y`` and its CT date+time in ``name`` (for the tooltip).
    fig = S.build_sentiment_intraday_figure(_PTS)
    data = fig["series"][0]["data"]
    assert len(data) == 3
    assert data[0]["x"] == 0 and data[0]["y"] == 3.0 and "name" in data[0]
    assert [d["x"] for d in data] == [0, 1, 2]           # contiguous slots


def test_sentiment_intraday_figure_has_value_zones():
    fig = S.build_sentiment_intraday_figure(_PTS)
    zones = fig["series"][0]["zones"]
    # red <=4.5, yellow <=6.5, green above
    assert zones[0]["value"] == 4.5 and zones[1]["value"] == 6.5
    assert "color" in zones[-1]


def test_trend_intraday_figure_rescaled_to_0_10():
    # Trend is stored 0-100 but shown on a 0-10 scale (like sentiment): value ×0.1,
    # y-axis max 10, and zone boundaries at 3/7 (the 30/70 trend-state cuts /10).
    fig = S.build_trend_intraday_figure(_PTS)
    data = fig["series"][0]["data"]
    assert data[2]["y"] == 8.5                     # 85.0 → 8.5
    zones = fig["series"][0]["zones"]
    assert zones[0]["value"] == 3 and zones[1]["value"] == 7
    assert fig["yAxis"]["min"] == 0 and fig["yAxis"]["max"] == 10


def test_intraday_figures_break_line_across_overnight_gap():
    # Two RTH points on consecutive CT days (overnight gap > threshold): a NULL slot
    # is inserted (so the line breaks — each trading day is its own segment) but the
    # days stay CONTIGUOUS on the index axis (no dead space). Two days → two labeled
    # tick positions.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
    ts1 = int(datetime(2025, 1, 1, 14, 0, tzinfo=CT).timestamp())   # 14:00 CT day 1
    ts2 = int(datetime(2025, 1, 2, 8, 30, tzinfo=CT).timestamp())   # 08:30 CT day 2
    pts = [{"ts": ts1, "sentiment": 5.0, "trend": 50.0},
           {"ts": ts2, "sentiment": 6.0, "trend": 60.0}]
    for fig in (S.build_sentiment_intraday_figure(pts),
                S.build_trend_intraday_figure(pts)):
        data = fig["series"][0]["data"]
        # 2 real points + 1 null slot between them, all on contiguous integer slots.
        assert len(data) == 3
        assert any(d.get("y") is None for d in data)
        assert [d["x"] for d in data] == [0, 1, 2]
        assert len(fig["xAxis"]["tickPositions"]) == 2   # one labeled tick per day
        # No datetime axis / stock-only keys (those froze updates or dropped labels).
        assert "type" not in fig["xAxis"] and "breaks" not in fig["xAxis"]
        assert "time" not in fig

    # No break when points are within the same session (small gap) → no null slot.
    close = [{"ts": ts1, "sentiment": 5.0, "trend": 50.0},
             {"ts": ts1 + 120, "sentiment": 6.0, "trend": 60.0}]
    d = S.build_sentiment_intraday_figure(close)["series"][0]["data"]
    assert len(d) == 2 and all(pt.get("y") is not None for pt in d)


def test_intraday_figures_empty_points_are_valid():
    assert S.build_sentiment_intraday_figure([])["series"][0]["data"] == []
    assert S.build_trend_intraday_figure(None)["series"][0]["data"] == []


def test_intraday_figures_render_in_central_time():
    # The recorded ts are UTC epoch; the per-point tooltip name (and the day-boundary
    # tick labels) must format in Central Time, not UTC. A ts at 12:00 UTC is 06:00
    # (CST) / 07:00 (CDT) CT — so the name's time is NOT "12:00".
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ts = 1_735_732_800  # 2025-01-01 12:00:00 UTC
    ct = datetime.fromtimestamp(ts, ZoneInfo("America/Chicago"))
    fig = S.build_sentiment_intraday_figure([{"ts": ts, "sentiment": 5.0, "trend": 50.0}])
    name = fig["series"][0]["data"][0]["name"]
    assert f"{ct:%H:%M}" in name          # CT time-of-day
    assert "12:00" not in name            # NOT the UTC time-of-day
