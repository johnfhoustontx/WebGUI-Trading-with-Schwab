import numpy as np
import pandas as pd
from src.analysis import factors


def _df(closes, highs=None, lows=None, vols=None):
    n = len(closes)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "datetime": idx,
        "open": closes,
        "high": highs if highs is not None else [c * 1.01 for c in closes],
        "low": lows if lows is not None else [c * 0.99 for c in closes],
        "close": closes,
        "volume": vols if vols is not None else [1_000_000] * n,
    })


def test_winsorize_clips_tails():
    s = pd.Series([-100, 0, 0, 0, 100])
    w = factors.winsorize(s, lower=0.2, upper=0.8)
    assert w.max() < 100 and w.min() > -100


def test_registry_lists_core_factors():
    names = set(factors.FACTORS)
    for f in ("mom_12_1", "mom_6_1", "pth", "str_5d", "vol_adj_mom",
              "trend_quality", "low_vol", "rs_spy", "rs_sector", "turnover"):
        assert f in names
    for name, spec in factors.FACTORS.items():
        assert callable(spec["fn"])
        assert spec["direction"] in (1, -1)


def test_mom_12_1_positive_for_uptrend():
    closes = list(np.linspace(100, 200, 300))
    s = factors.mom_12_1(_df(closes))
    assert s.iloc[-1] > 0


def test_pth_near_high_is_high():
    closes = list(np.linspace(100, 200, 300))
    assert factors.pth(_df(closes)).iloc[-1] > 0.95
    closes2 = list(np.linspace(200, 120, 300))
    assert factors.pth(_df(closes2)).iloc[-1] < 0.8


def test_str_5d_is_negative_recent_return():
    closes = [100] * 300
    closes[-1] = 110
    s = factors.str_5d(_df(closes))
    assert s.iloc[-1] < 0


def test_low_vol_higher_for_calm_series():
    calm = list(100 + np.sin(np.arange(300)) * 0.5)
    wild = list(100 + np.sin(np.arange(300)) * 10)
    assert factors.low_vol(_df(calm)).iloc[-1] > factors.low_vol(_df(wild)).iloc[-1]


def test_trend_quality_positive_above_emas():
    closes = list(np.linspace(100, 200, 300))
    assert factors.trend_quality(_df(closes)).iloc[-1] > 0


def test_vol_adj_mom_defined():
    closes = list(np.linspace(100, 160, 300))
    assert np.isfinite(factors.vol_adj_mom(_df(closes)).iloc[-1])


def test_rs_spy_positive_when_outperforming():
    up = list(np.linspace(100, 200, 300))
    flat = list(np.linspace(100, 105, 300))
    sym, ref = _df(up), _df(flat)
    s = factors.rs_spy(sym, ref_close=factors._close(ref))
    assert s.iloc[-1] > 0


def test_turnover_defined():
    closes = [100] * 300
    vols = list(np.linspace(1e6, 5e6, 300))
    assert np.isfinite(factors.turnover(_df(closes, vols=vols)).iloc[-1])


def test_compute_factor_frame_columns():
    closes = list(np.linspace(100, 180, 300))
    ref = factors._close(_df(list(np.linspace(100, 110, 300))))
    frame = factors.compute_factor_frame(_df(closes), spy_close=ref, sector_close=ref)
    assert set(factors.FACTORS).issubset(set(frame.columns))
    assert len(frame) == 300


def test_factor_is_causal_no_lookahead():
    # raw factors must be causal: value at bar k uses only data <= k.
    closes = list(np.linspace(100, 200, 300))
    rng = np.random.default_rng(0)
    closes = list(100 + np.cumsum(rng.normal(0.2, 1.0, 300)))
    df = _df(closes)
    k = 250
    full = factors.mom_6_1(df).iloc[k - 1]
    trunc = factors.mom_6_1(_df(closes[:k])).iloc[-1]
    assert np.isclose(full, trunc, equal_nan=True)


def test_str_5d_direction_drop_beats_rally():
    drop = [100] * 300; drop[-1] = 90       # recent -10%
    rally = [100] * 300; rally[-1] = 110    # recent +10%
    assert factors.str_5d(_df(drop)).iloc[-1] > factors.str_5d(_df(rally)).iloc[-1]


def test_turnover_direction_rises_with_volume():
    vols = [1_000_000] * 300; vols[-1] = 5_000_000
    s = factors.turnover(_df([100] * 300, vols=vols))
    assert s.iloc[-1] > 1.0


def test_trend_quality_warmup_is_nan():
    closes = list(np.linspace(100, 200, 300))
    s = factors.trend_quality(_df(closes))
    assert s.iloc[:199].isna().all() and np.isfinite(s.iloc[-1])


def test_rs_sector_positive_when_outperforming():
    up = list(np.linspace(100, 200, 300))
    flat = list(np.linspace(100, 105, 300))
    s = factors.rs_sector(_df(up), ref_close=factors._close(_df(flat)))
    assert s.iloc[-1] > 0


def test_pth_within_bounds_on_gap_up():
    closes = list(np.linspace(100, 150, 299)) + [10_000]  # gap far above prior high
    s = factors.pth(_df(closes))
    assert (s.dropna() <= 1.5).all() and (s.dropna() >= 0).all()


def test_winsorize_degenerate_band_passes_through():
    s = pd.Series([5.0] + [0.0] * 99)   # 2/98 band collapses to 0
    w = factors.winsorize(s)
    assert w.max() == 5.0                # the lone signal is NOT annihilated


def test_compute_factor_frame_bad_factor_becomes_nan_column(monkeypatch):
    def boom(df, **k):
        raise ValueError("boom")
    monkeypatch.setitem(factors.FACTORS, "turnover",
                        {**factors.FACTORS["turnover"], "fn": boom})
    frame = factors.compute_factor_frame(_df(list(np.linspace(100, 180, 300))))
    assert "turnover" in frame.columns and frame["turnover"].isna().all()
    assert set(factors.FACTORS).issubset(set(frame.columns))


def test_compute_factor_frame_short_history_degrades():
    frame = factors.compute_factor_frame(_df(list(np.linspace(100, 110, 80))))
    assert set(factors.FACTORS).issubset(set(frame.columns)) and len(frame) == 80
    # long-lookback factors are NaN on the last row, but it does not crash
    assert np.isnan(frame["mom_12_1"].iloc[-1])
