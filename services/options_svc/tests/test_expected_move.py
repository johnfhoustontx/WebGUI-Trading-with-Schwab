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
