"""Tests for the shared Highcharts solid-gauge builder (pages/gauge.py).

The speedometer used by the Sentiment, Market Trend, and Trade-detail panels.
"""
from pages import gauge


def _rgb(hexc):
    h = hexc.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def test_gauge_figure_rainbow_face_and_needle():
    fig = gauge.gauge_figure(76.0, "Long")
    assert fig["chart"]["type"] == "gauge"            # angular gauge (needle)
    assert fig["series"][0]["data"] == [76.0]         # needle position
    # The arc FACE is painted as a red -> yellow -> green rainbow: many graduated
    # plotBands spanning the full range (a smooth sweep, not 4 discrete zones).
    bands = fig["yAxis"]["plotBands"]
    assert len(bands) >= 20
    assert bands[0]["from"] == 0 and bands[-1]["to"] == 100
    r0, rN = _rgb(bands[0]["color"]), _rgb(bands[-1]["color"])
    assert r0[0] > r0[1]            # left end reddish (R > G)
    assert rN[1] > rN[0]            # right end greenish (G > R)
    # Middle is a WARM tone (yellow/amber): red & green both dominate blue. Kept
    # palette-relative so a re-theme (config [gauge].mid) can't break it — the
    # default #ffd54f and Deep Slate #e6b45a both satisfy this.
    mid = _rgb(bands[len(bands) // 2]["color"])
    assert mid[0] > 150 and mid[1] > 150 and mid[2] < mid[0] and mid[2] < mid[1]
    assert fig["plotOptions"]["gauge"]["dial"]        # needle present
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
