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


def _head_trace(fig):
    return next(t for t in fig["data"] if t.get("mode") == "markers+text")


def test_rrg_scatter_figure_shape():
    fig = R.rrg_scatter_figure(_assessment())
    head = _head_trace(fig)
    assert head["type"] == "scatter"
    assert head["mode"].startswith("markers")
    assert set(head["x"]) == {101.5, 98.0}      # rs_ratio (heads)
    # crosshair reference lines at 100/100 present as shapes
    assert any(s.get("type") == "line" for s in fig["layout"].get("shapes", []))


def test_rrg_scatter_has_meteor_tail_per_sector():
    fig = R.rrg_scatter_figure(_assessment())
    head = _head_trace(fig)
    tails = [t for t in fig["data"]
             if t is not head and t.get("mode") == "lines+markers"]
    assert len(tails) == 2                       # one per sector with a tail
    # Tails render BEHIND the head trace (head is last).
    assert fig["data"][-1] is head
    xlk_tail = next(t for t in tails if t["x"][-1] == 101.5)
    # Trail follows the sector's path, oldest -> newest, ending at the head.
    assert xlk_tail["x"] == [100.2, 100.9, 101.5]
    assert xlk_tail["y"] == [99.5, 100.8, 102.0]
    # Meteor fade: marker opacity ramps up toward the newest point.
    op = xlk_tail["marker"]["opacity"]
    assert op == sorted(op) and op[0] < op[-1]
    # Quadrant color (Leading -> green) on the markers; faint rgba line.
    assert xlk_tail["marker"]["color"] == R.CLR_GREEN
    assert xlk_tail["line"]["color"].startswith("rgba(")
    assert xlk_tail.get("showlegend") is False
    assert xlk_tail.get("hoverinfo") == "skip"


def test_rrg_scatter_no_legend_leak():
    # With 12 traces, every trace must opt out of the legend so Plotly does
    # not auto-show a stray "trace N" entry for the head.
    fig = R.rrg_scatter_figure(_assessment())
    assert all(t.get("showlegend") is False for t in fig["data"])


def test_rrg_scatter_handles_missing_tail():
    a = _assessment()
    for s in a["sectors"]:
        s.pop("tail", None)
    fig = R.rrg_scatter_figure(a)
    # No tail traces, but the head trace + crosshairs still render.
    assert all(t.get("mode") != "lines+markers" for t in fig["data"])
    assert _head_trace(fig)["x"]


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
