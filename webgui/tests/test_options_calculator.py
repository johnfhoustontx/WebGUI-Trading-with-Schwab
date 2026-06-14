"""Tests for the Calculator pure transforms (banding, grid mapping, formatting)."""
import datetime as dt

from pages.options import calculator as calc


def test_pnl_cell_class_neutral_for_zero():
    assert calc.pnl_cell_class(0, 100, -100) == "neutral"
    assert calc.pnl_cell_class(None, 100, -100) == "neutral"


def test_pnl_cell_class_profit_bands():
    assert calc.pnl_cell_class(100, 100, -100) == "p5"
    assert calc.pnl_cell_class(10, 100, -100) == "p1"
    assert calc.pnl_cell_class(50, 100, -100) == "p3"


def test_pnl_cell_class_loss_bands():
    assert calc.pnl_cell_class(-100, 100, -100) == "l5"
    assert calc.pnl_cell_class(-10, 100, -100) == "l1"


def test_grid_rows_shapes_price_and_cell_pairs():
    data = [{"price": 450.0, "pnl": [10, -5], "pnl_pct": [2.0, -1.0]}]
    rows = calc.grid_rows(data)
    assert rows[0]["price"] == 450.0
    assert len(rows[0]["cells"]) == 2
    assert rows[0]["cells"][0]["pnl"] == 10
    assert rows[0]["cells"][1]["pnl_pct"] == -1.0


def test_grid_extremes():
    data = [{"price": 1, "pnl": [10, -5], "pnl_pct": [0, 0]},
            {"price": 2, "pnl": [3, -20], "pnl_pct": [0, 0]}]
    g_max, g_min = calc.grid_extremes(data)
    assert g_max == 10 and g_min == -20


def test_eval_date_labels():
    dates = [dt.date(2026, 1, 15), dt.date(2026, 2, 28)]
    assert calc.eval_date_labels(dates) == ["01/15", "02/28"]


def test_formatters():
    assert calc.fmt_dollar(1234) == "+1,234"
    assert calc.fmt_dollar(-5) == "-5"
    assert calc.fmt_pct(12.34) == "+12.3%"
    assert calc.fmt_dollar(None) == "—"


def test_leg_specs_cover_strategies():
    for strat in ("PCS", "CCS", "IC", "LONG_PUT", "NAKED_CALL"):
        assert strat in calc.LEG_SPECS
    assert len(calc.LEG_SPECS["IC"]) == 4
    assert len(calc.LEG_SPECS["PCS"]) == 2
