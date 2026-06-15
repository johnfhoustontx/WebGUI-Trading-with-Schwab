"""Pure-transform tests for the Sector Rotation page."""
from pages import sentiment_rotation as R


def _assessment():
    return {
        "date": "2026-06-13",
        "headline": {"regime": "Risk-ON", "text": "Cyclicals leading",
                     "spread": 2.1, "cyclical_mom_mean": 101.2, "defensive_mom_mean": 99.1},
        "sectors": [
            {"name": "Technology", "etf": "XLK", "rs_ratio": 101.5, "rs_momentum": 102.0,
             "quadrant": "Leading", "direction": "INTO"},
            {"name": "Utilities", "etf": "XLU", "rs_ratio": 98.0, "rs_momentum": 97.0,
             "quadrant": "Lagging", "direction": "FROM"},
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
    regime, color, text, detail = R.headline_parts(_assessment())
    assert regime == "Risk-ON" and color == R.CLR_GREEN
    assert "Cyclicals leading" in text
    assert "spread" in detail and "+2.1" in detail


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


def test_rrg_scatter_figure_shape():
    fig = R.rrg_scatter_figure(_assessment())
    assert fig["data"][0]["type"] == "scatter"
    assert fig["data"][0]["mode"].startswith("markers")
    assert set(fig["data"][0]["x"]) == {101.5, 98.0}      # rs_ratio
    # crosshair reference lines at 100/100 present as shapes
    assert any(s.get("type") == "line" for s in fig["layout"].get("shapes", []))
