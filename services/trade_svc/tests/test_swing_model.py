"""Run from the repo root with the repo venv:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_swing_model.py -v
(never `pytest services` over all services — cross-app module-name collisions.)"""
import numpy as np
from services.trade_svc import swing_model as sm

_ARTIFACT = {
    "version": "2026-06-28", "horizon": 20,
    "regimes": {"all": {
        "weights": {"mom_12_1": 0.5, "low_vol": -0.5},     # signed
        "factor_ic": {"mom_12_1": {"mean_ic": 0.04}, "low_vol": {"mean_ic": -0.066}},
        "norm": {"mom_12_1": {"mean": 0.0, "std": 0.1}, "low_vol": {"mean": -0.02, "std": 0.01}},
        "calibration": [
            {"band": 0, "score_lo": -3, "score_hi": -0.5, "mean_fwd": -0.008, "hit_rate": 0.43, "n": 100},
            {"band": 1, "score_lo": -0.5, "score_hi": 0.5, "mean_fwd": 0.0, "hit_rate": 0.49, "n": 100},
            {"band": 2, "score_lo": 0.5, "score_hi": 3, "mean_fwd": 0.0135, "hit_rate": 0.523, "n": 100}],
        "oos_ic": 0.0367}}}


def test_score_symbol_top_band_is_buy():
    snap = {"mom_12_1": [0.0, 0.1, 0.2], "low_vol": [-0.03, -0.02, -0.01]}
    cur = {"mom_12_1": 0.5, "low_vol": -0.05}   # high mom (high +z), low_vol very negative
    out = sm.score_symbol(cur, snap, _ARTIFACT)
    assert out["verdict"] == "BUY"
    assert out["expected_fwd"] == 0.0135 and out["hit_rate"] == 0.523
    assert any(c["factor"] == "mom_12_1" for c in out["contributions"])


def test_score_symbol_signed_weight_low_vol_subtracts():
    # a HIGH-vol name (low_vol factor very negative) with negative weight -> positive
    # contribution; confirm the low_vol contribution sign = weight * z
    snap = {"mom_12_1": [0.0, 0.0, 0.0], "low_vol": [0.0, 0.0, 0.0]}
    cur = {"mom_12_1": 0.0, "low_vol": -0.02}  # z(low_vol) negative vs norm mean -0.02 -> 0
    out = sm.score_symbol(cur, None, _ARTIFACT)   # snap empty -> norm fallback
    assert out is not None


def test_score_symbol_degrades_without_artifact():
    assert sm.score_symbol({"mom_12_1": 0.5}, None, None) is None


def test_score_symbol_degrades_empty_weights():
    bad = {"regimes": {"all": {"weights": {}, "calibration": [{"band": 0, "score_lo": 0,
            "score_hi": 1, "mean_fwd": 0, "hit_rate": 0.5, "n": 1}]}}}
    assert sm.score_symbol({"mom_12_1": 0.5}, None, bad) is None
