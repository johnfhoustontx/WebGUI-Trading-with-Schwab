"""Scoring: metrics math, grade boundaries, peer percentiles, composite re-weighting."""
import pytest

from src.evaluation import evaluate_portfolio, grade_letter


def make_holding(symbol="ABC", last=110.0, qty=10, avg=100.0):
    mv = qty * last
    return {"symbol": symbol, "asset_type": "EQUITY", "sector": "Technology",
            "sector_etf": "XLK", "quantity": qty, "avg_price": avg,
            "last": last, "market_value": mv, "total_pl": mv - qty * avg,
            "day_pl": 0.0}


def make_baseline(symbol="ABC", **over):
    base = {"symbol": symbol, "entry_date": "2026-01-05", "entry_price": 100.0,
            "days_held": 100, "ann_vol": 0.20, "atr": 2.0, "peak_close": 115.0,
            "sector_ret": 0.04, "spy_ret": 0.03, "entry_pct": 0.25}
    base.update(over)
    return base


def model_of(*holdings):
    return {"holdings": list(holdings), "sectors": []}


def test_core_metrics():
    cards = evaluate_portfolio(model_of(make_holding()),
                               {"ABC": make_baseline()})
    c = cards["ABC"]
    assert c["total_return"] == pytest.approx(0.10)          # 110/100 - 1
    assert c["ann_return"] == pytest.approx((1.10) ** (365 / 100) - 1)
    assert c["vs_sector"] == pytest.approx(0.10 - 0.04)
    assert c["vs_spy"] == pytest.approx(0.10 - 0.03)
    assert c["sharpe"] == pytest.approx(c["ann_return"] / 0.20)
    # live 110 vs held peak 115 -> 4.35% off peak
    assert c["drawdown"] == pytest.approx(1 - 110 / 115)
    assert c["weight"] == pytest.approx(1.0)                  # only position


def test_drawdown_uses_live_price_as_new_peak():
    c = evaluate_portfolio(model_of(make_holding(last=120.0)),
                           {"ABC": make_baseline(peak_close=115.0)})["ABC"]
    assert c["drawdown"] == pytest.approx(0.0)


def test_capital_efficiency_percentile_across_peers():
    h1, h2 = make_holding("AAA", last=120.0), make_holding("BBB", last=101.0)
    cards = evaluate_portfolio(
        model_of(h1, h2),
        {"AAA": make_baseline("AAA"), "BBB": make_baseline("BBB")})
    assert cards["AAA"]["capital_pct"] > cards["BBB"]["capital_pct"]
    assert cards["AAA"]["capital_pct"] == pytest.approx(1.0)
    assert cards["BBB"]["capital_pct"] == pytest.approx(0.0)


def test_missing_dimension_is_none_and_composite_reweights():
    cards = evaluate_portfolio(
        model_of(make_holding()),
        {"ABC": make_baseline(ann_vol=None, entry_pct=None)})
    c = cards["ABC"]
    assert c["grades"]["risk"] is None
    assert c["grades"]["execution"] is None
    # composite is the weighted mean of the two available dims only
    g = c["grades"]
    expected = (g["return"] * 0.35 + g["capital"] * 0.25) / (0.35 + 0.25)
    assert c["composite"] == pytest.approx(expected)


def test_no_baseline_or_no_last_yields_minimal_card():
    cards = evaluate_portfolio(model_of(make_holding()), {})
    c = cards["ABC"]
    assert c["total_return"] is None and c["composite"] is None


def test_derived_last_equity_from_market_value():
    h = make_holding()
    h["last"] = None
    h["market_value"] = 1100.0  # qty 10 -> last 110
    cards = evaluate_portfolio(model_of(h), {"ABC": make_baseline()})
    c = cards["ABC"]
    assert c["last"] == pytest.approx(110.0)
    assert c["total_return"] == pytest.approx(0.10)
    assert c["composite"] is not None


def test_derived_last_option_divides_by_contract_multiplier():
    h = make_holding("OPT1", qty=2, avg=4.0)
    h["asset_type"] = "OPTION"
    h["last"] = None
    h["market_value"] = 800.0  # 2 contracts * 4.0 * 100
    cards = evaluate_portfolio(
        model_of(h), {"OPT1": make_baseline("OPT1", entry_price=4.0)})
    c = cards["OPT1"]
    assert c["last"] == pytest.approx(4.0)
    assert c["total_return"] == pytest.approx(0.0)


def test_no_last_and_no_market_value_yields_minimal_card():
    h = make_holding()
    h["last"] = None
    h["market_value"] = None
    cards = evaluate_portfolio(model_of(h), {"ABC": make_baseline()})
    c = cards["ABC"]
    assert c["last"] is None
    assert c["total_return"] is None


def test_flat_return_grades_as_winning_not_lagging():
    cards = evaluate_portfolio(
        model_of(make_holding(last=100.0)),
        {"ABC": make_baseline(sector_ret=None, spy_ret=None)})
    c = cards["ABC"]
    assert c["total_return"] == pytest.approx(0.0)
    assert c["grades"]["return"] == pytest.approx(3.0)


def test_grade_letter_scale():
    assert grade_letter(3.5) == "A"
    assert grade_letter(2.5) == "B"
    assert grade_letter(1.5) == "C"
    assert grade_letter(0.5) == "D"
    assert grade_letter(0.4) == "F"
    assert grade_letter(None) is None
