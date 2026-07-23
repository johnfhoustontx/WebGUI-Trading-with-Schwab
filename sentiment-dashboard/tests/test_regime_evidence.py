"""Tests for scoring.regime_evidence — bar-derived market-regime evidence assembler.

The assembler turns already-fetched market data (5-min bars, daily bars, a VIX
snapshot, a matrix row, prior BB widths) into the flat evidence dict that
market_regime.score_regimes consumes. Every key degrades to None on thin/missing
inputs; the function never raises.
"""
import numpy as np
import pandas as pd

from scoring import market_regime as MR
from scoring import regime_evidence as RE


# ---------------------------------------------------------------- fixtures


def _bars(highs, lows, closes, opens=None, vols=None,
          start="2026-07-22 08:30", freq="5min"):
    n = len(closes)
    opens = list(closes) if opens is None else opens
    vols = [1000] * n if vols is None else vols
    return pd.DataFrame({
        "datetime": pd.date_range(start, periods=n, freq=freq),
        "open": list(opens),
        "high": list(highs),
        "low": list(lows),
        "close": list(closes),
        "volume": list(vols),
    })


def _trend_bars(n=60, step=0.3):
    base = np.arange(n) * step + 100.0
    return _bars(base + 0.15, base - 0.15, base, opens=base - 0.05)


def _range_bars(n=90, center=100.0, seed=59):
    # Mean-reverting AR(1) (phi<0) around a center with gapless opens + tiny wicks:
    # oscillates without persistent direction (low ADX) and concentrates centrally
    # (balanced volume profile) — the way real range sessions read on OHLC.
    rng = np.random.default_rng(seed)
    c = [center]
    for _ in range(n - 1):
        c.append(center - 0.5 * (c[-1] - center) + rng.normal(0.0, 0.3))
    close = np.array(c)
    opens = np.concatenate([[center], close[:-1]])
    body_hi = np.maximum(opens, close)
    body_lo = np.minimum(opens, close)
    highs = body_hi + np.abs(rng.normal(0.0, 0.03, n))
    lows = body_lo - np.abs(rng.normal(0.0, 0.03, n))
    return _bars(highs, lows, close, opens=opens)


# ---------------------------------------------------------------- contract


def test_evidence_has_exactly_the_contract_keys():
    out = RE.evidence_from_bars(_trend_bars(), None,
                                {"vix": 20, "vix1d": 22, "vix3m": 19},
                                {"gex_regime": "above"}, None)
    assert set(out) == set(MR.EVIDENCE_KEYS)


# ---------------------------------------------------------------- trend


def test_trend_bars_high_adx_and_band_hug():
    out = RE.evidence_from_bars(_trend_bars(), None, None, None, None)
    assert out["adx"] is not None and out["adx"] > 20
    assert out["band_hug_frac"] is not None and out["band_hug_frac"] > 0.4
    assert out["ema_slope_atr"] is not None and out["ema_slope_atr"] > 0


def test_trend_bars_produce_trending_membership():
    ev = RE.evidence_from_bars(_trend_bars(), None, None, None, None)
    scores = MR.score_regimes(ev)
    assert max(scores.raw, key=scores.raw.get) == "trending"


# ---------------------------------------------------------------- range


def test_range_bars_low_adx_balanced():
    out = RE.evidence_from_bars(_range_bars(), None, None, None, None)
    assert out["adx"] is not None and out["adx"] < 20
    assert out["profile_balance"] is not None and out["profile_balance"] > 0.5


# ---------------------------------------------------------------- matrix row


def test_above_flip_from_matrix_row():
    assert RE.evidence_from_bars(None, None, None,
                                 {"gex_regime": "above"}, None)["above_flip"] is True
    assert RE.evidence_from_bars(None, None, None,
                                 {"gex_regime": "below"}, None)["above_flip"] is False
    assert RE.evidence_from_bars(None, None, None, None, None)["above_flip"] is None
    assert RE.evidence_from_bars(None, None, None, {}, None)["above_flip"] is None


def test_below_flip_deep_passthrough():
    assert RE.evidence_from_bars(None, None, None,
                                 {"below_flip_deep": 0.7}, None)["below_flip_deep"] == 0.7
    assert RE.evidence_from_bars(None, None, None,
                                 {"gex_regime": "above"}, None)["below_flip_deep"] is None
    assert RE.evidence_from_bars(None, None, None, None, None)["below_flip_deep"] is None


# ---------------------------------------------------------------- vix


def test_term_inversion_from_vix():
    inv = RE.evidence_from_bars(None, None, {"vix": 25, "vix1d": 30, "vix3m": 20},
                                None, None)["term_inversion"]
    assert inv is not None and inv > 0
    normal = RE.evidence_from_bars(None, None, {"vix": 20, "vix1d": 18, "vix3m": 22},
                                   None, None)["term_inversion"]
    assert not (normal and normal > 0)


def test_vix1d_spike_from_prev():
    with_prev = RE.evidence_from_bars(None, None,
                                      {"vix": 20, "vix1d": 24, "vix1d_prev": 20,
                                       "vix3m": 21}, None, None)
    assert with_prev["vix1d_spike_pct"] is not None and with_prev["vix1d_spike_pct"] > 0
    no_prev = RE.evidence_from_bars(None, None, {"vix": 20, "vix1d": 24, "vix3m": 21},
                                    None, None)
    assert no_prev["vix1d_spike_pct"] is None


# ---------------------------------------------------------------- gap (daily)


def test_gap_from_daily():
    # prior close 100.0; today open 102.0 (+2%), range 101.5..102.5 -> doesn't cover 100
    daily = pd.DataFrame({
        "open": [99.0, 102.0],
        "high": [100.5, 102.5],
        "low": [98.0, 101.5],
        "close": [100.0, 102.2],
        "volume": [1e6, 1e6],
    })
    out = RE.evidence_from_bars(None, daily, None, None, None)
    assert out["gap_open_pct"] is not None and abs(out["gap_open_pct"] - 2.0) < 0.2
    assert out["gap_filled"] is False


# ---------------------------------------------------------------- degradation


def test_thin_bars_degrade_to_none():
    thin = _trend_bars(n=3)
    out = RE.evidence_from_bars(thin, None, None, None, None)
    assert set(out) == set(MR.EVIDENCE_KEYS)
    for k, v in out.items():
        assert v is None, f"{k} should be None on thin bars, got {v!r}"


def test_all_none_inputs_never_raises():
    out = RE.evidence_from_bars(None, None, None, None, None)
    assert set(out) == set(MR.EVIDENCE_KEYS)
    assert all(v is None for v in out.values())


# ---------------------------------------------------------------- rel vol


def test_rel_vol_present_on_multiday():
    day1 = _trend_bars(n=30)
    day2 = _trend_bars(n=30)
    day2["datetime"] = pd.date_range("2026-07-23 08:30", periods=30, freq="5min")
    bars = pd.concat([day1, day2], ignore_index=True)
    out = RE.evidence_from_bars(bars, None, None, None, None)
    assert isinstance(out["rel_vol"], float)


# ---------------------------------------------------------------- opening range


def test_or_break_held_vs_failed():
    # HELD: OR ~[99.5,100.5] on first 6, then a clean rally that stays above.
    or_bars = [100.0, 100.2, 99.8, 100.3, 99.9, 100.1]
    held_closes = or_bars + [100.8, 101.3, 101.7, 102.0, 102.3, 102.5, 102.6, 102.8]
    held = _bars([c + 0.1 for c in held_closes], [c - 0.1 for c in held_closes], held_closes)
    assert RE.evidence_from_bars(held, None, None, None, None)["or_break_state"] == "held"

    # FAILED: break above the OR, then close back inside and stay.
    failed_closes = or_bars + [101.2, 101.0, 100.3, 100.0, 99.9, 100.1, 100.0, 100.2]
    failed = _bars([c + 0.1 for c in failed_closes], [c - 0.1 for c in failed_closes],
                   failed_closes)
    assert RE.evidence_from_bars(failed, None, None, None, None)["or_break_state"] == "failed"


# ---------------------------------------------------------------- wicks


def test_wick_two_sided_high_when_both_tails():
    # Oscillate; at near-high bars add big UPPER wicks, at near-low bars big LOWER wicks.
    n = 24
    t = np.arange(n)
    close = 100.0 + 0.5 * np.sin(t * 0.8)
    highs, lows, opens = [], [], []
    for c in close:
        rng = 0.5
        if c > 100.2:            # near a high -> tall upper wick
            highs.append(c + 0.6)
            lows.append(c - 0.05)
            opens.append(c + 0.02)
        elif c < 99.8:           # near a low -> tall lower defense
            highs.append(c + 0.05)
            lows.append(c - 0.6)
            opens.append(c - 0.02)
        else:
            highs.append(c + 0.1)
            lows.append(c - 0.1)
            opens.append(c)
    both = _bars(highs, lows, close, opens=opens)
    two_sided = RE.evidence_from_bars(both, None, None, None, None)["wick_two_sided"]
    assert two_sided is not None and two_sided > 0.3

    # ONE-SIDED: only upper wicks; near-low bars have NO lower wick.
    highs1, lows1, opens1 = [], [], []
    for c in close:
        if c > 100.2:
            highs1.append(c + 0.6)
            lows1.append(c - 0.05)
            opens1.append(c + 0.02)
        else:
            highs1.append(c + 0.1)
            lows1.append(c)      # close sits at the low -> no lower defense
            opens1.append(c + 0.02)
    one_sided = _bars(highs1, lows1, close, opens=opens1)
    one = RE.evidence_from_bars(one_sided, None, None, None, None)["wick_two_sided"]
    assert one is not None and one < two_sided
