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


def test_composite_series_filters_zeros_and_blanks():
    snaps = [_snap("2026-06-01", 6.0), _snap("2026-06-02", 0.0),
             _snap("2026-06-03", 7.0)]
    dates, scores = S.composite_series(snaps)
    assert scores == [6.0, 7.0]
    assert dates == ["2026-06-01", "2026-06-03"]


def test_velocity_line_formats_and_flags():
    scores = [5.0, 5.1, 5.0, 5.2, 5.1]
    line, flag = S.velocity_line(scores, today_score=8.0)
    assert "3d ROC" in line and "20d Z" in line
    assert "REGIME BREAK" in flag


def test_velocity_line_insufficient_history():
    line, flag = S.velocity_line([], today_score=5.0)
    assert "—" in line
    assert flag == ""


def test_divergence_named_extracts_confident_components():
    snap = _snap("2026-06-03", 6.0, vix_complex=9, sector_perf=2)
    named = S.divergence_named(snap)
    names = [n for n, _ in named]
    assert "VIX Complex" in names and "Sector Performance" in names


def test_build_history_figure_shape():
    snaps = [_snap("2026-06-01", 6.0), _snap("2026-06-02", 7.0)]
    fig = S.build_history_figure(snaps)
    assert fig["data"][0]["type"] == "scatter"
    assert fig["data"][0]["y"] == [6.0, 7.0]


def test_commit_trend_regime_returns_state():
    closes = [100.0 + i * 0.5 for i in range(260)]
    tr, committed, days = S.commit_trend_regime(closes)
    assert committed in {"bull_trend", "pullback_in_bull", "range",
                         "bear_rally", "bear_trend"}
    assert days >= 1


def test_commit_trend_regime_short_series_is_range():
    tr, committed, days = S.commit_trend_regime([100.0, 101.0])
    assert committed == "range"


def test_commit_trend_regime_holds_on_single_bar_flip():
    # Long healthy uptrend -> trailing raw + committed state is bull_trend.
    base = [100.0 + i * 0.5 for i in range(260)]
    assert S.commit_trend_regime(base)[1] == "bull_trend"
    # Append exactly ONE bar that drops sharply below the 50DMA, flipping
    # the latest raw classify away from bull_trend. With HYSTERESIS_DAYS=2,
    # a single non-confirmed bar must NOT flip the committed state.
    series = base + [200.0]
    tr, committed, _ = S.commit_trend_regime(series)
    assert tr.state != "bull_trend"      # raw verdict flipped (range)
    assert committed == "bull_trend"     # hysteresis held the committed state
    assert tr.state != committed


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
