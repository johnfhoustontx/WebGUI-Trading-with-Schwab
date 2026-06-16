"""Tests for the scanner quality-score coloring (pages/options/scanner.py).

``score_zone_color`` maps a composite score (0-100) to a hex zone color,
matching the speedometer zones in pages/options/svg.py. ``signal_rows`` stamps
each row with ``_score_color`` so the table body-cell slot can render a colored
chip.
"""
from pages.options import scanner


def test_score_zone_color_none():
    assert scanner.score_zone_color(None) == "#666666"


def test_score_zone_color_red():
    assert scanner.score_zone_color(30) == scanner.RED


def test_score_zone_color_amber():
    assert scanner.score_zone_color(50) == scanner.AMBER


def test_score_zone_color_blue():
    assert scanner.score_zone_color(60) == scanner.BLUE


def test_score_zone_color_green():
    assert scanner.score_zone_color(90) == scanner.GREEN


def test_score_zone_color_boundaries():
    # Zone edges are exclusive lower bounds: <40 RED, <55 AMBER, <75 BLUE, else GREEN.
    assert scanner.score_zone_color(39) == scanner.RED
    assert scanner.score_zone_color(40) == scanner.AMBER
    assert scanner.score_zone_color(54) == scanner.AMBER
    assert scanner.score_zone_color(55) == scanner.BLUE
    assert scanner.score_zone_color(74) == scanner.BLUE
    assert scanner.score_zone_color(75) == scanner.GREEN


def test_signal_rows_stamp_score_color():
    rows = scanner.signal_rows([
        {"symbol": "HI", "composite_score": 90},
        {"symbol": "LO", "composite_score": 30},
        {"symbol": "NA"},
    ])
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["HI"]["_score_color"] == scanner.GREEN
    assert by_sym["LO"]["_score_color"] == scanner.RED
    assert by_sym["NA"]["_score_color"] == "#666666"
