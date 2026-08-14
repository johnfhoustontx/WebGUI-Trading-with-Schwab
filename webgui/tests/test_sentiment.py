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


# ── Local Tailwind color-class maps (Phase 5) — driven by config [charts] ──
# These test the band→color MAPPING, so they reference the module's own
# config-derived class constants (S.TXT_G, S.BG_R, …) rather than literal hexes,
# and stay correct when config/theme.toml is re-themed (e.g. Deep Slate).
def test_local_class_constants_mirror_palette():
    # The CLR_* constants mirror config/theme.toml [charts]; the class strings
    # wrap them as text-[]/bg-[] utilities.
    assert S.TXT_G == f"text-[{S.CLR_GREEN}]" and S.TXT_R == f"text-[{S.CLR_RED}]"
    assert S.TXT_Y == f"text-[{S.CLR_YELLOW}]" and S.TXT_FLAT == f"text-[{S.CLR_FLAT}]"
    assert S.TXT_CY == f"text-[{S.CLR_CYAN}]"
    assert S.BG_G == f"bg-[{S.CLR_GREEN}]" and S.BG_R == f"bg-[{S.CLR_RED}]" and S.BG_Y == f"bg-[{S.CLR_YELLOW}]"
    # remove-sets cover every class an in-place element can apply
    assert set(S.SENT_TEXT_CLASSES.split()) == {S.TXT_G, S.TXT_R, S.TXT_Y, S.TXT_FLAT, S.TXT_CY}
    assert set(S.TRAFFIC_BG_CLASSES.split()) == {S.BG_G, S.BG_R, S.BG_Y}


def test_traffic_bg_class_maps_bands():
    assert S.traffic_bg_class(7) == S.BG_G
    assert S.traffic_bg_class(4) == S.BG_R
    assert S.traffic_bg_class(5.5) == S.BG_Y


def test_bias_text_class_buckets():
    assert S.bias_text_class("Bullish") == S.TXT_G
    assert S.bias_text_class("Bearish") == S.TXT_R
    assert S.bias_text_class("Neutral") == S.TXT_Y


def test_pct_text_class():
    assert S.pct_text_class(0.5) == S.TXT_G
    assert S.pct_text_class(-0.5) == S.TXT_R
    assert S.pct_text_class(0.0) == S.TXT_FLAT
    assert S.pct_text_class(None) == S.TXT_FLAT


def test_pcr_text_class_and_rrg_text_class():
    assert S.pcr_text_class(0.9) == S.TXT_G
    assert S.pcr_text_class(1.1) == S.TXT_R
    assert S.pcr_text_class(1.0) == S.TXT_FLAT
    assert S.rrg_text_class("Improving") == S.TXT_CY
    assert S.rrg_text_class("Lagging") == S.TXT_R
    assert S.rrg_text_class("Leading") == S.TXT_G
    assert S.rrg_text_class("Weakening") == S.TXT_Y
    assert S.rrg_text_class("???") == S.TXT_FLAT


def test_sc_text_class():
    assert S.sc_text_class(7) == S.TXT_G
    assert S.sc_text_class(3) == S.TXT_R
    assert S.sc_text_class(5) == S.TXT_Y


def test_trend_text_class():
    # old-vocab (still used by the 30-day structural gauge)
    assert S.trend_text_class("bull_trend") == S.TXT_G
    assert S.trend_text_class("pullback_in_bull") == S.TXT_G
    assert S.trend_text_class("bear_trend") == S.TXT_R
    assert S.trend_text_class("bear_rally") == S.TXT_R
    assert S.trend_text_class("range") == S.TXT_Y
    # new five-state vocab (direction x aggression) used by the Today gauge
    assert S.trend_text_class("bullish") == S.TXT_G
    assert S.trend_text_class("lack_of_bearishness") == S.TXT_G
    assert S.trend_text_class("bearish") == S.TXT_R
    assert S.trend_text_class("lack_of_bullishness") == S.TXT_Y
    assert S.trend_text_class("neutral") == S.TXT_Y
    assert S.trend_text_class("mystery") == S.TXT_Y  # unknown -> amber default


def test_trend_short_covers_both_vocabularies():
    for k in ("bullish", "lack_of_bullishness", "neutral",
              "lack_of_bearishness", "bearish"):
        assert k in S._TREND_SHORT
    for k in ("bull_trend", "pullback_in_bull", "range",
              "bear_rally", "bear_trend"):
        assert k in S._TREND_SHORT


def test_market_state_evidence_rows():
    ev = ["direction 75/100", "aggression -0.37"]
    assert S.market_state_evidence_rows({"evidence": ev}) == ev
    assert S.market_state_evidence_rows({}) == []
    assert S.market_state_evidence_rows(None) == []
    assert S.market_state_evidence_rows({"evidence": None}) == []


def test_rotation_text_class():
    assert S.rotation_text_class(S.CLR_GREEN) == S.TXT_G
    assert S.rotation_text_class(S.CLR_RED) == S.TXT_R
    assert S.rotation_text_class(S.CLR_YELLOW) == S.TXT_Y
    assert S.rotation_text_class(S.CLR_FLAT) == S.TXT_FLAT


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


# --- Market Regime panel (blended structural regime) --------------------------
def _regime(**over):
    r = {"ts": "2026-07-23T10:05:00-05:00", "label": "Trending",
         "committed_label": "trending", "confidence": 0.62, "unclear": False,
         "memberships": {"mean_reversion": 0.28, "trending": 0.52, "breakout": 0.08,
                         "choppy": 0.09, "crisis": 0.03},
         "transition": {"from": "mean_reversion", "to": "trending", "progress": 0.6},
         "evidence": ["ADX 32 rising", "VWAP held 95%"]}
    r.update(over)
    return r


def _regime_points(n=3, base_ts=1_753_280_700):
    return [{"ts": base_ts + i * 300, "confidence": 0.6, "label": "trending",
             "memberships": {"mean_reversion": 0.3, "trending": 0.5, "breakout": 0.05,
                             "choppy": 0.1, "crisis": 0.05}} for i in range(n)]


def test_regime_headline_parts():
    label, conf, cls = S.regime_headline_parts(_regime())
    assert label == "Trending" and "62" in conf and cls
    # NiceGUI .classes(remove=...) splits a STRING; a list raises at render
    # (caught live, not by the builder tests) — and every class it may ADD
    # must be in the remove-set or repaints stack conflicting colors.
    assert isinstance(S.REGIME_TEXT_CLASSES, str)
    removable = set(S.REGIME_TEXT_CLASSES.split())
    for key in list(S.REGIME_ORDER) + ["", "bogus"]:
        for unclear in (False, True):
            _l, _c, c = S.regime_headline_parts(
                _regime(committed_label=key, unclear=unclear))
            assert c in removable


def test_regime_headline_unclear_and_missing():
    assert S.regime_headline_parts(_regime(label="Unclear", unclear=True))[0] == "Unclear"
    # No payload at all -> a waiting placeholder, never a crash.
    for empty in ({}, None):
        label, conf, cls = S.regime_headline_parts(empty)
        assert label and conf == "" and cls


def test_regime_transition_text():
    assert S.regime_transition_text(_regime()) == "Balanced → Trending · 60%"
    # Stable (no transition) / missing -> empty string, so the row hides.
    assert S.regime_transition_text(_regime(transition=None)) == ""
    assert S.regime_transition_text({}) == ""
    assert S.regime_transition_text(None) == ""


def test_regime_mix_figure_is_a_stacked_area_plain_chart():
    fig = S.build_regime_mix_figure(_regime_points())
    assert fig["chart"]["type"] == "area"
    assert fig["plotOptions"]["area"]["stacking"] == "percent"
    # one series per regime, in a stable order, each with the right point count
    assert len(fig["series"]) == 5
    assert [s["name"] for s in fig["series"]] == [
        "Balanced", "Trending", "Breakout", "Whipsaw", "Stressed"]
    assert all(len(s["data"]) == 3 for s in fig["series"])
    # a stockChart would freeze in-place updates (see _intraday_figure) -> plain chart
    assert "stockChart" not in str(fig)
    assert "categories" in fig["xAxis"]        # synthetic contiguous axis, no dead space
    assert fig["accessibility"]["enabled"] is False


def test_regime_mix_figure_values_and_empty():
    fig = S.build_regime_mix_figure(_regime_points(1))
    trending = next(s for s in fig["series"] if s["name"] == "Trending")
    assert trending["data"][0]["y"] == 0.5
    for empty in ([], None):
        f = S.build_regime_mix_figure(empty)
        assert len(f["series"]) == 5 and all(s["data"] == [] for s in f["series"])


def test_regime_mix_figure_breaks_line_between_days():
    day1 = 1_753_280_700                      # a session point
    pts = _regime_points(2, day1) + _regime_points(1, day1 + 86_400)
    fig = S.build_regime_mix_figure(pts)
    ys = [p.get("y") for p in fig["series"][0]["data"]]
    assert None in ys                          # a null slot separates the two days


def test_regime_evidence_rows():
    assert S.regime_evidence_rows(_regime()) == ["ADX 32 rising", "VWAP held 95%"]
    assert S.regime_evidence_rows({}) == []


def test_regime_transition_text_carries_the_direction():
    r = _regime(direction=1, direction_strong=True)
    assert S.regime_transition_text(r) == "Balanced → Rallying · 60%"
    r = _regime(direction=-1, direction_strong=False)
    assert S.regime_transition_text(r) == "Balanced → Softening · 60%"


def test_regime_mix_series_names_never_take_the_direction():
    """The stacked band's fixed order + names ARE the reading position — a legend
    that renames itself intra-session defeats that. Direction belongs on the
    headline, not the chart."""
    pts = _regime_points()
    fig = S.build_regime_mix_figure(pts)
    assert [s["name"] for s in fig["series"]] == [
        "Balanced", "Trending", "Breakout", "Whipsaw", "Stressed"]


def test_regime_headline_color_follows_the_direction():
    """Trending's band colour is fixed, but the HEADLINE must not paint a
    down-trend green: green up, red down, neutral grey when no direction is
    claimed."""
    removable = set(S.REGIME_TEXT_CLASSES.split())
    up = S.regime_headline_parts(
        _regime(committed_label="trending", direction=1, direction_strong=True))
    down = S.regime_headline_parts(
        _regime(committed_label="trending", direction=-1, direction_strong=True))
    flat = S.regime_headline_parts(_regime(committed_label="trending", direction=0))
    assert up[0] == "Rallying" and down[0] == "Retreating" and flat[0] == "Trending"
    assert up[2] != down[2]
    assert {up[2], down[2], flat[2]} <= removable


def test_regime_headline_direction_junk_is_neutral():
    for bad in ("up", 2, None, True):
        label, _c, cls = S.regime_headline_parts(
            _regime(committed_label="trending", direction=bad))
        assert label == "Trending"
        assert cls in set(S.REGIME_TEXT_CLASSES.split())


def test_regime_headline_prefers_a_locally_derived_label():
    """The payload's own `label` is the service's word; the page re-derives it
    from (committed_label, direction) so a stale/absent label can't outlive a
    rename, and falls back to the payload when the key is unknown."""
    r = _regime(committed_label="choppy", label="Choppy")
    assert S.regime_headline_parts(r)[0] == "Whipsaw"
