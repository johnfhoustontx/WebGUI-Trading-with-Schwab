# services/options_svc/tests/test_expected_move.py
from services.options_svc import compute


def _chain(vol_by_strike, exp_key="2026-07-18:28"):
    return {"callExpDateMap": {exp_key: {
        f"{k:.1f}": [{"volatility": v}] for k, v in vol_by_strike.items()}}}


def test_atm_iv_picks_nearest_strike_and_normalizes_percent():
    chain = _chain({100.0: 18.0, 105.0: 22.0})  # Schwab gives vol as a percent
    iv = compute.atm_iv_from_chain(chain, spot=101.0, expiry="2026-07-18")
    assert abs(iv - 0.18) < 1e-9  # nearest strike 100 -> 18% -> 0.18 decimal


def test_atm_iv_none_when_no_contracts():
    assert compute.atm_iv_from_chain({}, spot=100.0, expiry="2026-07-18") is None


import math


def test_em_cone_widens_as_sqrt_time():
    cone = compute.em_cone(spot=100.0, atm_iv=0.20, dte=5, start_ts_ms=0)
    upper, lower = cone["upper"], cone["lower"]
    assert len(upper) == 6 and len(lower) == 6
    assert upper[0][1] == 100.0 and lower[0][1] == 100.0
    w3 = 100.0 * 0.20 * math.sqrt(3 / 365)
    assert abs(upper[3][1] - (100.0 + w3)) < 1e-9
    assert abs(lower[3][1] - (100.0 - w3)) < 1e-9
    assert upper[1][0] - upper[0][0] == 86_400_000


def test_em_cone_empty_on_bad_inputs():
    assert compute.em_cone(None, 0.2, 5, 0) == {"upper": [], "lower": []}
    assert compute.em_cone(100.0, None, 5, 0) == {"upper": [], "lower": []}
    assert compute.em_cone(100.0, 0.2, 0, 0) == {"upper": [], "lower": []}
