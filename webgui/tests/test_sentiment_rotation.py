"""Pure-transform + Tier-3-reader tests for the Sector Rotation page."""
import inspect

from pages import sentiment_rotation as R


def _assessment():
    return {
        "date": "2026-06-13",
        "headline": {"regime": "Risk-ON", "text": "Cyclicals leading",
                     "spread": 2.1, "cyclical_mom_mean": 101.2, "defensive_mom_mean": 99.1},
        "sectors": [
            {"name": "Technology", "etf": "XLK", "rs_ratio": 101.5, "rs_momentum": 102.0,
             "quadrant": "Leading", "direction": "INTO",
             "tail": [{"rs_ratio": 100.2, "rs_momentum": 99.5},
                      {"rs_ratio": 100.9, "rs_momentum": 100.8},
                      {"rs_ratio": 101.5, "rs_momentum": 102.0}]},
            {"name": "Utilities", "etf": "XLU", "rs_ratio": 98.0, "rs_momentum": 97.0,
             "quadrant": "Lagging", "direction": "FROM",
             "tail": [{"rs_ratio": 99.1, "rs_momentum": 99.0},
                      {"rs_ratio": 98.5, "rs_momentum": 98.0},
                      {"rs_ratio": 98.0, "rs_momentum": 97.0}]},
        ],
        "rotating_from": [{"name": "Utilities", "etf": "XLU", "quadrant": "Lagging"}],
        "rotating_into": [{"name": "Technology", "etf": "XLK", "quadrant": "Leading"}],
    }


def test_quadrant_color():
    assert R.quadrant_color("Leading") == R.CLR_GREEN
    assert R.quadrant_color("Improving") == R.CLR_CYAN
    assert R.quadrant_color("Weakening") == R.CLR_YELLOW
    assert R.quadrant_color("Lagging") == R.CLR_RED
    assert R.quadrant_color("???") == R.CLR_FLAT


# ── Local Tailwind color-class maps (Phase 5) — exact local palette preserved ──
def test_local_text_class_constants():
    assert R.TXT_G == "text-[#66bb6a]" and R.TXT_R == "text-[#ef5350]"
    assert R.TXT_Y == "text-[#ffd54f]" and R.TXT_CY == "text-[#3fb6c7]"
    assert R.TXT_FLAT == "text-[#9e9e9e]"
    assert set(R.SENT_TEXT_CLASSES.split()) == {R.TXT_G, R.TXT_R, R.TXT_Y, R.TXT_CY, R.TXT_FLAT}


def test_quadrant_text_class():
    assert R.quadrant_text_class("Leading") == "text-[#66bb6a]"
    assert R.quadrant_text_class("Improving") == "text-[#3fb6c7]"
    assert R.quadrant_text_class("Weakening") == "text-[#ffd54f]"
    assert R.quadrant_text_class("Lagging") == "text-[#ef5350]"
    assert R.quadrant_text_class("???") == "text-[#9e9e9e]"


def test_regime_text_class():
    assert R.regime_text_class("Risk-ON") == "text-[#66bb6a]"
    assert R.regime_text_class("Risk-OFF") == "text-[#ef5350]"
    assert R.regime_text_class("Mixed") == "text-[#ffd54f]"


def test_headline_parts():
    regime, color, text, detail = R.headline_parts(_assessment(), 1.5)
    assert regime == "Risk-ON" and color == R.CLR_GREEN
    assert "Cyclicals leading" in text
    assert "spread" in detail and "+2.1" in detail
    assert "threshold ±1.5" in detail


def test_headline_parts_threshold_param_and_default():
    # Explicit threshold flows through to the detail string.
    _, _, _, detail = R.headline_parts(_assessment(), 2.0)
    assert "threshold ±2.0" in detail
    # None / omitted -> falls back to DEFAULT_RISK_THRESHOLD (no engine import).
    _, _, _, detail_none = R.headline_parts(_assessment(), None)
    assert f"threshold ±{R.DEFAULT_RISK_THRESHOLD}" in detail_none
    _, _, _, detail_default = R.headline_parts(_assessment())
    assert f"threshold ±{R.DEFAULT_RISK_THRESHOLD}" in detail_default


def test_side_rows():
    weights = {"XLK": 32.5, "XLU": 2.1}
    rows, total = R.side_rows(_assessment(), "rotating_into", weights)
    assert rows[0]["name"] == "Technology" and rows[0]["quadrant"] == "Leading"
    assert rows[0]["weight"] == 32.5
    assert round(total, 1) == 32.5


def test_rotation_rows_sorted_and_colored():
    rows = R.rotation_rows(_assessment())
    assert [r["etf"] for r in rows] == ["XLK", "XLU"]      # rs_momentum desc
    assert rows[0]["color"] == R.CLR_GREEN                 # Leading
    assert rows[1]["color"] == R.CLR_RED                   # Lagging
    assert rows[0]["rs_ratio"] == 101.5


def _sector_traces(fig):
    return [t for t in fig["series"] if t.get("type") == "spline"]


def _head(trace):
    return trace["data"][-1]


def test_rrg_scatter_one_trace_per_sector():
    fig = R.rrg_scatter_figure(_assessment())
    traces = _sector_traces(fig)
    assert len(traces) == 2                       # one series per sector
    # series order maps to the sectors order (head = current point).
    assert _head(traces[0])["x"] == 101.5 and _head(traces[1])["x"] == 98.0
    # crosshair reference lines at 100/100 present as plotLines on both axes
    assert any(pl.get("value") == 100 for pl in fig["xAxis"]["plotLines"])
    assert any(pl.get("value") == 100 for pl in fig["yAxis"]["plotLines"])
    # native hover-isolation: other series dim (no plotly event round-trip)
    assert fig["plotOptions"]["series"]["states"]["inactive"]["opacity"] < 1.0
    assert fig["accessibility"]["enabled"] is False


def test_rrg_scatter_line_plus_single_head_dot():
    fig = R.rrg_scatter_figure(_assessment())
    xlk = next(t for t in _sector_traces(fig) if _head(t)["x"] == 101.5)
    # Trail follows the path, oldest -> newest, ending at the head.
    assert [p["x"] for p in xlk["data"]] == [100.2, 100.9, 101.5]
    assert [p["y"] for p in xlk["data"]] == [99.5, 100.8, 102.0]
    # Only the LAST point (head) has an enabled marker; trail markers off.
    head = _head(xlk)
    assert head["marker"]["enabled"] is True and head["marker"]["radius"] >= 6
    assert all(p["marker"]["enabled"] is False for p in xlk["data"][:-1])
    # Label only on the head; quadrant color; faint rgba trail line.
    assert head["dataLabels"]["format"] == "XLK"
    assert all("dataLabels" not in p for p in xlk["data"][:-1])
    assert head["marker"]["fillColor"] == R.CLR_GREEN
    assert xlk["color"].startswith("rgba(")
    assert xlk.get("showInLegend") is False


def test_rrg_scatter_quadrant_corner_labels():
    """The four quadrant names render as faint in-chart corner labels: Leading
    top-right, Weakening bottom-right, Lagging bottom-left, Improving top-left —
    via transparent xAxis plotBands (label-only, band fill invisible)."""
    fig = R.rrg_scatter_figure(_assessment())
    bands = fig["xAxis"]["plotBands"]
    labels = {b["label"]["text"]: b["label"] for b in bands}
    assert set(labels) == {"Leading", "Weakening", "Lagging", "Improving"}
    corners = {t: (lb["align"], lb["verticalAlign"]) for t, lb in labels.items()}
    assert corners["Leading"] == ("right", "top")
    assert corners["Weakening"] == ("right", "bottom")
    assert corners["Lagging"] == ("left", "bottom")
    assert corners["Improving"] == ("left", "top")
    # Unobtrusive: invisible band fill, faint small text, under the series.
    assert all(b["color"] == "rgba(0,0,0,0)" for b in bands)
    assert all("rgba" in b["label"]["style"]["color"] for b in bands)
    # The right-hand bands start at the 100 crosshair, the left-hand ones end there.
    assert all(b["from"] == 100 for b in bands if b["label"]["align"] == "right")
    assert all(b["to"] == 100 for b in bands if b["label"]["align"] == "left")


def test_rrg_page_caption_removed():
    import inspect

    from pages import sentiment_rrg
    src = inspect.getsource(sentiment_rrg.render)
    assert "Each sector's trail" not in src


def test_rrg_scatter_no_legend_leak():
    fig = R.rrg_scatter_figure(_assessment())
    assert all(t.get("showInLegend") is False for t in fig["series"])


def test_rrg_scatter_handles_missing_tail():
    a = _assessment()
    for s in a["sectors"]:
        s.pop("tail", None)
    fig = R.rrg_scatter_figure(a)
    traces = _sector_traces(fig)
    assert len(traces) == 2                       # single-point head series each
    xlk = next(t for t in traces if [p["x"] for p in t["data"]] == [101.5])
    assert [p["y"] for p in xlk["data"]] == [102.0]
    assert _head(xlk)["marker"]["enabled"] is True


def test_neither_rotation_page_round_trips_hover_to_the_server():
    """The durable half of the old plotly-migration guard.

    Originally this also pinned ``ui.highchart`` on the RRG page. That stopped
    being true on 2026-08-17, when the RRG was rebuilt from a supplied design as
    a hand-drawn plot — absolutely-positioned markers over an SVG trail layer —
    so neither page renders a chart element at all now. What must never come
    back is the per-hover client→server round-trip the plotly version used."""
    import inspect

    from pages import sentiment_rrg
    for src in (inspect.getsource(sentiment_rrg.render),
                inspect.getsource(R.render)):
        assert "plotly_hover" not in src and "plotly_unhover" not in src
        assert "run_plot_method" not in src


def test_hex_to_rgba_helper():
    assert R._hex_to_rgba(R.CLR_GREEN, 0.28) == "rgba(102, 187, 106, 0.28)"


def test_page_has_no_engine_glue():
    """Regression: the migrated page must not re-introduce the sentiment-engine
    imports / sys.path glue (the source of the cross-app ``scoring`` collision)."""
    src = inspect.getsource(R)
    assert "sector_rotation_assessment" not in src
    assert "sectors_ref" not in src
    assert "rotation_tool" not in src
    assert "from repo_paths import SENTIMENT" not in src
    assert "import sys" not in src
    assert "sys.path" not in src
    # The page reads the bus instead of holding a compute path.
    assert "_compute" not in src
    assert "_sector_weights" not in src
    assert "_ROTATION_CACHE" not in src


def test_render_graceful_empty():
    """render() must paint a waiting placeholder without crashing when the bus
    cache is cold (service not running). Mirrors test_sentiment.py: render inside
    a slot context (a card) to exercise the widget wiring + initial paint."""
    import bus_client
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("sentiment:rotation") is None  # confirm empty
    with ui.card():
        R.render()  # must not raise
