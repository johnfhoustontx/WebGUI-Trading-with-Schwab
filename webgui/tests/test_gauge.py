"""Tests for the shared Highcharts solid-gauge builder (pages/gauge.py).

The speedometer used by the Sentiment, Market Trend, and Trade-detail panels.
"""
from pages import gauge


def test_gauge_figure_solidgauge_value_and_color_stops():
    fig = gauge.gauge_figure(76.0, "Long")
    assert fig["chart"]["type"] == "solidgauge"
    assert fig["series"][0]["data"] == [76.0]
    # Smooth value-mapped fill: a continuous red -> yellow -> green ramp that
    # Highcharts interpolates between (no discrete zone flips).
    stops = fig["yAxis"]["stops"]
    positions = [round(p, 4) for p, _c in stops]
    assert positions[0] == 0.0 and positions[-1] == 1.0   # spans the full range
    assert 0.5 in positions                                # yellow at the midpoint
    assert positions == sorted(positions)                  # monotonic, no duplicates
    assert len(positions) == len(set(positions))           # crisp-flip dupes removed
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
