"""Tests for the shared Highcharts solid-gauge builder (pages/gauge.py).

The speedometer used by the Sentiment, Market Trend, and Trade-detail panels.
"""
from pages import gauge


def test_gauge_figure_solidgauge_value_and_color_stops():
    fig = gauge.gauge_figure(76.0, "Long")
    assert fig["chart"]["type"] == "solidgauge"
    assert fig["series"][0]["data"] == [76.0]
    # Value-mapped fill color: stops anchored at the zone thresholds (40/55/75)
    # so the fill flips color where the legacy SVG speedometer's zones did.
    positions = [round(p, 4) for p, _c in fig["yAxis"]["stops"]]
    assert 0.40 in positions and 0.55 in positions and 0.75 in positions
    assert "Long" in fig["series"][0]["dataLabels"]["format"]
    assert fig["accessibility"]["enabled"] is False
    assert fig["pane"]["startAngle"] == -90 and fig["pane"]["endAngle"] == 90


def test_gauge_figure_truncates_displayed_value_like_svg():
    # The legacy SVG showed int(score) (truncates 52.8 -> 52); match it so the
    # number doesn't shift. The arc fill still uses the true float.
    fig = gauge.gauge_figure(52.8, "x")
    assert ">52<" in fig["series"][0]["dataLabels"]["format"]
    assert fig["series"][0]["data"] == [52.8]


def test_gauge_figure_clamps_and_handles_bad():
    assert gauge.gauge_figure(150, "x")["series"][0]["data"] == [100.0]
    assert gauge.gauge_figure(-10, "x")["series"][0]["data"] == [0.0]
    assert gauge.gauge_figure("bad", "x")["series"][0]["data"] == [0.0]


def test_gauge_figure_escapes_label_html():
    fig = gauge.gauge_figure(50, "<b>x</b>")
    fmt = fig["series"][0]["dataLabels"]["format"]
    assert "<b>x</b>" not in fmt
    assert "&lt;b&gt;x&lt;/b&gt;" in fmt
