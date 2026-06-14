import math
import pandas as pd
import pytest

from src.evaluation import (
    slice_since,
    window_return,
    annualized_volatility,
    latest_atr,
    entry_percentile,
    compute_baseline,
)


def make_df(closes, start="2026-01-05", highs=None, lows=None):
    """Daily candles; high/low default to close +/- 1."""
    n = len(closes)
    dates = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({
        "datetime": dates,
        "open": closes,
        "high": highs if highs is not None else [c + 1 for c in closes],
        "low": lows if lows is not None else [c - 1 for c in closes],
        "close": closes,
        "volume": [1000] * n,
    })


def test_slice_since_keeps_rows_on_or_after_entry():
    df = make_df([10, 11, 12, 13, 14])          # 2026-01-05..09 (business days)
    out = slice_since(df, "2026-01-07")
    assert list(out["close"]) == [12, 13, 14]


def test_slice_since_handles_none_df_and_no_rows():
    assert slice_since(None, "2026-01-07") is None
    df = make_df([10, 11])
    assert slice_since(df, "2030-01-01") is None  # empty slice -> None


def test_window_return():
    df = make_df([100, 105, 110])
    assert window_return(df, "2026-01-05") == pytest.approx(0.10)
    assert window_return(None, "2026-01-05") is None


def test_annualized_volatility_hand_computed():
    # closes 100 -> 110 -> 99: daily rets +0.10, -0.10; sample std * sqrt(252)
    df = make_df([100, 110, 99])
    rets = pd.Series([0.10, -0.10])
    expected = rets.std(ddof=1) * math.sqrt(252)
    assert annualized_volatility(df) == pytest.approx(expected)


def test_annualized_volatility_needs_three_closes():
    assert annualized_volatility(make_df([100, 110])) is None
    assert annualized_volatility(None) is None


def test_latest_atr_simple_mean_of_true_ranges():
    # Constant high-low spread of 2, no gaps: every TR = 2 -> ATR = 2.
    df = make_df([100] * 20)
    assert latest_atr(df, period=14) == pytest.approx(2.0)
    assert latest_atr(make_df([100] * 5), period=14) is None  # too short
    assert latest_atr(None) is None


def test_latest_atr_overnight_gap_uses_prev_close_terms():
    # 16 rows: days 0-7 close at 100 (high 101 / low 99), day 8 gaps up to
    # close 110 (high 111 / low 109), days 9-15 stay at 110 (high 111 / low 109).
    #
    # True ranges (TR = max(high-low, |high-prev_close|, |low-prev_close|)):
    #   every non-gap day:  max(2, 1, 1) = 2
    #   gap day (idx 8):    max(111-109=2, |111-100|=11, |109-100|=9) = 11
    #
    # ATR(14) = mean of TRs for the last 14 rows (idx 2..15), which includes
    # the gap day: (13 * 2 + 11) / 14 = 37 / 14.
    # A naive high-low implementation would return 2.0 instead.
    closes = [100] * 8 + [110] * 8
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    df = make_df(closes, highs=highs, lows=lows)
    assert latest_atr(df, period=14) == pytest.approx(37 / 14)


def test_entry_percentile_position_in_window_range():
    # closes since entry span 90..110; entry at 95 -> (95-90)/(110-90) = 0.25
    df = make_df([90, 100, 110])
    assert entry_percentile(95.0, df) == pytest.approx(0.25)
    # entry below the whole range clamps to 0 (bought cheaper than any close)
    assert entry_percentile(80.0, df) == pytest.approx(0.0)
    assert entry_percentile(120.0, df) == pytest.approx(1.0)


def test_entry_percentile_degenerate_range_or_missing():
    assert entry_percentile(100.0, make_df([100])) is None      # zero range
    assert entry_percentile(None, make_df([90, 110])) is None
    assert entry_percentile(95.0, None) is None


def _holding(**over):
    base = {"symbol": "ABC", "asset_type": "EQUITY", "sector_etf": "XLK",
            "quantity": 10, "avg_price": 100.0}
    base.update(over)
    return base


def test_compute_baseline_full():
    stock = make_df([95, 100, 105, 110] + [110] * 16)   # 20 rows for ATR
    sector = make_df([50, 51, 52, 53] + [53] * 16)
    spy = make_df([400, 404, 408, 412] + [412] * 16)
    entry = {"avg_price": 100.0, "entry_date": "2026-01-05",
             "total_quantity": 10.0}
    b = compute_baseline(_holding(), stock, sector, spy, entry)
    assert b["symbol"] == "ABC"
    assert b["entry_date"] == "2026-01-05"
    assert b["days_held"] >= 1
    assert b["peak_close"] == pytest.approx(110.0)
    assert b["sector_ret"] == pytest.approx(53 / 50 - 1)
    assert b["spy_ret"] == pytest.approx(412 / 400 - 1)
    assert b["ann_vol"] is not None
    assert b["atr"] is not None
    assert b["entry_pct"] is not None


def test_compute_baseline_no_entry_uses_position_avg_price_no_window_stats():
    stock = make_df([95, 100, 105])
    b = compute_baseline(_holding(), stock, None, None, None)
    # Without an entry date there is no holding window: window stats are None.
    assert b["entry_date"] is None
    assert b["days_held"] is None
    assert b["sector_ret"] is None and b["spy_ret"] is None
    assert b["peak_close"] is None
    # entry price still falls back to the position's avg_price
    assert b["entry_price"] == pytest.approx(100.0)


def test_compute_baseline_all_history_missing_is_all_none_but_total():
    b = compute_baseline(_holding(), None, None, None,
                         {"avg_price": 100.0, "entry_date": "2026-01-05",
                          "total_quantity": 10.0})
    assert b["ann_vol"] is None and b["atr"] is None and b["peak_close"] is None
    assert b["entry_date"] == "2026-01-05"   # entry survives without history


def test_compute_baseline_garbage_entry_date_does_not_raise():
    # A hand-edited bad date in data/entries.json must not crash the worker.
    stock = make_df([95, 100, 105, 110] + [110] * 16)
    entry = {"avg_price": 100.0, "entry_date": "garbage",
             "total_quantity": 10.0}
    b = compute_baseline(_holding(), stock, stock, stock, entry)
    assert b["days_held"] is None
    # slice_since on an unparseable date degrades to None -> window stats None
    assert b["peak_close"] is None
    assert b["sector_ret"] is None and b["spy_ret"] is None
    assert b["ann_vol"] is None and b["entry_pct"] is None
