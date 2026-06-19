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


def test_gauge_figure_solidgauge_value_and_color_stops():
    fig = S.gauge_figure(76.0, "Long")
    assert fig["chart"]["type"] == "solidgauge"
    assert fig["series"][0]["data"] == [76.0]
    # Value-mapped fill color: stops anchored at the zone thresholds (40/55/75)
    # so the fill flips color where the old needle zones did.
    positions = [round(p, 4) for p, _c in fig["yAxis"]["stops"]]
    assert 0.40 in positions and 0.55 in positions and 0.75 in positions
    # The label is rendered beneath the value in the dataLabel.
    assert "Long" in fig["series"][0]["dataLabels"]["format"]


def test_gauge_figure_truncates_displayed_value_like_svg():
    # The legacy SVG showed int(score) (truncates 52.8 -> 52); match it so the
    # number doesn't shift (rounding would show 53). Needle still uses the float.
    fig = S.gauge_figure(52.8, "x")
    assert ">52<" in fig["series"][0]["dataLabels"]["format"]
    assert fig["series"][0]["data"] == [52.8]      # needle keeps the true value
    assert fig["accessibility"]["enabled"] is False
    # Semicircle pane (matches the SVG's upper half).
    assert fig["pane"]["startAngle"] == -90 and fig["pane"]["endAngle"] == 90


def test_gauge_figure_clamps_and_handles_bad():
    assert S.gauge_figure(150, "x")["series"][0]["data"] == [100.0]
    assert S.gauge_figure(-10, "x")["series"][0]["data"] == [0.0]
    assert S.gauge_figure("bad", "x")["series"][0]["data"] == [0.0]


def test_gauge_figure_escapes_label_html():
    # A label can't break the HTML dataLabel format string.
    fig = S.gauge_figure(50, "<b>x</b>")
    assert "<b>x</b>" not in fig["series"][0]["dataLabels"]["format"]
    assert "&lt;b&gt;x&lt;/b&gt;" in fig["series"][0]["dataLabels"]["format"]


def test_bias_color_buckets():
    assert S.bias_color("Bullish") == S.CLR_GREEN
    assert S.bias_color("Bearish") == S.CLR_RED
    assert S.bias_color("Neutral") == S.CLR_YELLOW


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


def test_build_history_figure_shape():
    snaps = [_snap("2026-06-01", 6.0), _snap("2026-06-02", 7.0)]
    fig = S.build_history_figure(snaps)
    # Highcharts options dict: one line series over the composite scores.
    assert fig["series"][0]["type"] == "line"
    assert fig["series"][0]["data"] == [6.0, 7.0]
    assert fig["xAxis"]["categories"] == ["2026-06-01", "2026-06-02"]
    assert fig["yAxis"]["min"] == 0 and fig["yAxis"]["max"] == 10
    assert fig["accessibility"]["enabled"] is False     # silence a11y-module nag


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
