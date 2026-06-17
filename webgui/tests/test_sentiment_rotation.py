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


def _sector_traces(fig):
    return [t for t in fig["data"] if t.get("mode") == "lines+markers+text"]


def test_rrg_scatter_one_trace_per_sector():
    fig = R.rrg_scatter_figure(_assessment())
    traces = _sector_traces(fig)
    assert len(traces) == 2                       # one trace per sector
    # curveNumber/order maps to the sectors order.
    assert traces[0]["x"][-1] == 101.5 and traces[1]["x"][-1] == 98.0
    # crosshair reference lines at 100/100 present as shapes
    assert any(s.get("type") == "line" for s in fig["layout"].get("shapes", []))
    # hovermode closest so the line/head is hoverable
    assert fig["layout"].get("hovermode") == "closest"


def test_rrg_scatter_line_plus_single_head_dot():
    fig = R.rrg_scatter_figure(_assessment())
    xlk = next(t for t in _sector_traces(fig) if t["x"][-1] == 101.5)
    # Trail follows the path, oldest -> newest, ending at the head.
    assert xlk["x"] == [100.2, 100.9, 101.5]
    assert xlk["y"] == [99.5, 100.8, 102.0]
    # Only the LAST marker (head) is visible; trail markers are invisible.
    op = xlk["marker"]["opacity"]
    assert op[-1] == 1.0 and all(o == 0.0 for o in op[:-1])
    sz = xlk["marker"]["size"]
    assert sz[-1] >= 10 and all(s == 0.0 for s in sz[:-1])
    # Label only on the head; quadrant color; faint rgba line.
    assert xlk["text"][-1] == "XLK" and all(t == "" for t in xlk["text"][:-1])
    assert xlk["marker"]["color"] == R.CLR_GREEN
    assert xlk["line"]["color"].startswith("rgba(")
    assert xlk.get("showlegend") is False


def test_rrg_scatter_no_legend_leak():
    fig = R.rrg_scatter_figure(_assessment())
    assert all(t.get("showlegend") is False for t in fig["data"])


def test_rrg_scatter_handles_missing_tail():
    a = _assessment()
    for s in a["sectors"]:
        s.pop("tail", None)
    fig = R.rrg_scatter_figure(a)
    traces = _sector_traces(fig)
    assert len(traces) == 2                       # single-point head trace each
    xlk = next(t for t in traces if t["x"] == [101.5])
    assert xlk["y"] == [102.0]
    assert xlk["marker"]["opacity"][-1] == 1.0


def test_focus_opacities():
    assert R._focus_opacities(3, 1) == [0.12, 1.0, 0.12]
    assert R._focus_opacities(3, 0, dim=0.2) == [1.0, 0.2, 0.2]
    # out-of-range / None -> all visible (restore on unhover)
    assert R._focus_opacities(3, None) == [1.0, 1.0, 1.0]
    assert R._focus_opacities(3, 9) == [1.0, 1.0, 1.0]


def test_render_wires_hover_and_fullwidth_rrg():
    import inspect
    src = inspect.getsource(R.render)
    # hover-isolate wiring present
    assert "plotly_hover" in src and "plotly_unhover" in src
    assert "run_plot_method" in src and "_focus_opacities" in src


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
