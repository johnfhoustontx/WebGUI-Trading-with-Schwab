import pandas as pd
import pytest

from src.sectors import (
    compute_since_purchase_returns,
    holding_vs_sector,
    since_purchase_vs_sector,
    tailwind_headwind,
    weights_vs_benchmark,
)


def test_weights_vs_benchmark_covers_either_dict():
    my = {"Technology": 0.5, "Energy": 0.1}
    bench = {"Technology": 0.3, "Energy": 0.04, "Healthcare": 0.12}
    out = weights_vs_benchmark(my, bench)
    assert abs(out["Technology"] - 0.2) < 1e-9
    assert abs(out["Energy"] - 0.06) < 1e-9
    assert abs(out["Healthcare"] - (-0.12)) < 1e-9
    assert set(out) == {"Technology", "Energy", "Healthcare"}


def test_weights_vs_benchmark_empty_benchmark_returns_empty():
    assert weights_vs_benchmark({"Technology": 0.5}, {}) == {}


class _FakeRanking:
    def __init__(self, symbol, composite_rs, rank):
        self.symbol = symbol
        self.composite_rs = composite_rs
        self.rank = rank


class _FakeRanker:
    """Matches the documented interface: ``get_rankings()`` returns objects
    each having ``.symbol``, ``.composite_rs`` and ``.rank``."""

    def get_rankings(self):
        return [
            _FakeRanking("XLK", 112.5, 1),
            _FakeRanking("XLE", 88.0, 11),
        ]


def test_tailwind_headwind_adapts_ranker():
    out = tailwind_headwind(_FakeRanker())
    assert out == {
        "XLK": {"score": 112.5, "rank": 1},
        "XLE": {"score": 88.0, "rank": 11},
    }


def test_since_purchase_beats_sector():
    r = since_purchase_vs_sector(stock_return=0.20, sector_return=0.12)
    assert abs(r - 0.08) < 1e-9


def test_holding_vs_sector_uses_rs(monkeypatch):
    import src.sectors as s

    monkeypatch.setattr(
        s,
        "calculate_stock_vs_sector_rs",
        lambda stock_df, sector_df, periods=None: {"1M": 110.0, "3M": 95.0},
    )
    out = holding_vs_sector(stock_df="x", sector_df="y")
    assert out == {"1M": 110.0, "3M": 95.0}


def test_compute_since_purchase_returns_normal():
    df = pd.DataFrame(
        {
            "datetime": [
                pd.Timestamp("2026-01-02"),
                pd.Timestamp("2026-01-03"),
                pd.Timestamp("2026-01-04"),
            ],
            "close": [100.0, 110.0, 120.0],
        }
    )
    r = compute_since_purchase_returns(df, "2026-01-02")
    assert abs(r - 0.20) < 1e-9


def test_compute_since_purchase_returns_entry_between_rows():
    df = pd.DataFrame(
        {
            "datetime": [
                pd.Timestamp("2026-01-02"),
                pd.Timestamp("2026-01-05"),
                pd.Timestamp("2026-01-06"),
            ],
            "close": [100.0, 200.0, 250.0],
        }
    )
    # Entry date falls between row 0 and row 1; first close on/after is 200.
    r = compute_since_purchase_returns(df, "2026-01-03")
    assert abs(r - 0.25) < 1e-9


def test_compute_since_purchase_returns_none_or_empty():
    assert compute_since_purchase_returns(None, "2026-01-02") is None
    empty = pd.DataFrame({"datetime": [], "close": []})
    assert compute_since_purchase_returns(empty, "2026-01-02") is None


def _rs_df(closes):
    n = len(closes)
    return pd.DataFrame({
        "datetime": pd.date_range("2026-01-01", periods=n, freq="B"),
        "close": closes,
    })


def _load_rs():
    """Import the real ``calculate_stock_vs_sector_rs`` from the shared module."""
    import sys
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from repo_paths import SHARED

    sys.path.insert(0, str(SHARED / "analysis_lib"))
    from sector_analysis import calculate_stock_vs_sector_rs

    return calculate_stock_vs_sector_rs


def test_rs_parity_preserving_up_market():
    """C5: RS is 100*(1+stock)/(1+sector), 100 == parity, >100 == outperform."""
    rs = _load_rs()
    # period=1: 1-bar return. Stock +10% (100->110), sector +5% (100->105).
    # Parity RS = 100 * 1.10 / 1.05 = 104.7619...
    stock = _rs_df([100.0, 110.0])
    sector = _rs_df([100.0, 105.0])
    out = rs(stock, sector, periods=[1])
    assert out["1D"] == pytest.approx(100.0 * 1.10 / 1.05)
    assert out["1D"] > 100.0  # stock beat the sector


def test_rs_sign_correct_in_down_market():
    """C5: a stock that FELL LESS than its sector must read as OUTperformance
    (>100), not weakness. The old return/return ratio inverted this."""
    rs = _load_rs()
    # Stock -1% (100->99), sector -2% (100->98). Stock outperformed.
    # Old buggy ratio: (-1)/(-2)*100 = +50  -> wrongly < 100 (weak).
    # Correct parity: 100 * 0.99 / 0.98 = 101.02... -> > 100 (strong).
    stock = _rs_df([100.0, 99.0])
    sector = _rs_df([100.0, 98.0])
    out = rs(stock, sector, periods=[1])
    assert out["1D"] == pytest.approx(100.0 * 0.99 / 0.98)
    assert out["1D"] > 100.0


def test_rs_flat_sector_gives_stock_growth_factor():
    """A flat sector (0% return) yields 100*(1+stock), stable (no divide-by-0)."""
    rs = _load_rs()
    stock = _rs_df([100.0, 108.0])   # +8%
    sector = _rs_df([100.0, 100.0])  # flat
    out = rs(stock, sector, periods=[1])
    assert out["1D"] == pytest.approx(108.0)


def test_compute_since_purchase_returns_entry_after_all_rows():
    df = pd.DataFrame(
        {
            "datetime": [
                pd.Timestamp("2026-01-02"),
                pd.Timestamp("2026-01-03"),
            ],
            "close": [100.0, 110.0],
        }
    )
    assert compute_since_purchase_returns(df, "2026-02-01") is None
