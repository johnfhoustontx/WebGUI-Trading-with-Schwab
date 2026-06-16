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


def test_bias_color_buckets():
    assert S.bias_color("Bullish") == S.CLR_GREEN
    assert S.bias_color("Bearish") == S.CLR_RED
    assert S.bias_color("Neutral") == S.CLR_YELLOW


# ── Market Trend speedometer (hybrid: state anchor + slope/drawdown nudge) ────
def test_trend_gauge_value_bull_high_bear_low():
    bull = S.trend_gauge_value({"state": "bull_trend",
                                "sma_200_slope_pct": 0.1, "drawdown_pct": -2.0})
    bear = S.trend_gauge_value({"state": "bear_trend",
                                "sma_200_slope_pct": -0.2, "drawdown_pct": -15.0})
    assert bull > 75          # green zone
    assert bear < 40          # red zone


def test_trend_gauge_value_range_mid_and_default():
    assert abs(S.trend_gauge_value(
        {"state": "range", "sma_200_slope_pct": 0, "drawdown_pct": 0}) - 50.0) < 1e-9
    assert S.trend_gauge_value({}) == 50.0       # unknown state -> mid
    assert S.trend_gauge_value(None) == 50.0


def test_trend_gauge_value_nudge_bounded_and_clamped():
    # Extreme inputs: nudge is capped (slope ±8, dd ±5) and value clamps to [0,100].
    v = S.trend_gauge_value({"state": "bull_trend",
                             "sma_200_slope_pct": 99, "drawdown_pct": -99})
    assert v == 88.0          # 85 + clamp(+8) + clamp(-5)
    lo = S.trend_gauge_value({"state": "bear_trend",
                              "sma_200_slope_pct": -99, "drawdown_pct": -99})
    assert lo == 2.0          # 15 + clamp(-8) + clamp(-5)
    assert 0.0 <= v <= 100.0 and 0.0 <= lo <= 100.0


def test_composite_series_filters_zeros_and_blanks():
    snaps = [_snap("2026-06-01", 6.0), _snap("2026-06-02", 0.0),
             _snap("2026-06-03", 7.0)]
    dates, scores = S.composite_series(snaps)
    assert scores == [6.0, 7.0]
    assert dates == ["2026-06-01", "2026-06-03"]


def test_build_history_figure_shape():
    snaps = [_snap("2026-06-01", 6.0), _snap("2026-06-02", 7.0)]
    fig = S.build_history_figure(snaps)
    assert fig["data"][0]["type"] == "scatter"
    assert fig["data"][0]["y"] == [6.0, 7.0]


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
