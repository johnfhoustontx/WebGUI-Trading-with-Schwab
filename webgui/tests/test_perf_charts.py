"""Tests for the shared perf-charts builders (equity curve + MAE/MFE line)."""
from pages.options import perf_charts as pc


def test_signed_dollar():
    assert pc.signed_dollar(120) == "+$120"
    assert pc.signed_dollar(-90) == "-$90"
    assert pc.signed_dollar(None) == "$0"


def test_equity_curve_figure_maps_series():
    curve = [{"date": "2026-07-08", "equity": 24900.0, "realized": -100.0},
             {"date": "2026-07-09", "equity": 24950.0, "realized": 50.0}]
    fig = pc.equity_curve_figure(curve)
    assert fig["xAxis"]["categories"] == ["2026-07-08", "2026-07-09"]
    series = {s["name"]: s for s in fig["series"]}
    assert series["Equity"]["data"] == [24900.0, 24950.0]
    assert series["Daily P&L"]["data"] == [-100.0, 50.0]


def test_equity_curve_figure_empty_is_valid():
    fig = pc.equity_curve_figure([])
    assert fig["xAxis"]["categories"] == []
    assert all(s["data"] == [] for s in fig["series"])


def test_excursion_text():
    txt = pc.excursion_text({"n": 4, "avg_mae": -25.0, "avg_mfe": 70.0, "mfe_capture": 0.6})
    assert "peak +$70" in txt and "drawdown -$25" in txt and "0.60×" in txt and "4 closed" in txt
    assert pc.excursion_text({"n": 0}) == "" and pc.excursion_text(None) == ""
