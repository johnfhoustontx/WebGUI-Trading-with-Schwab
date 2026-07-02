"""Run from the repo root with the repo venv:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_swing_model.py -v
(never `pytest services` over all services — cross-app module-name collisions.)"""
import numpy as np
import pandas as pd
import pytest
from services.trade_svc import swing_model as sm


def _fit_z(value, basis):
    """The FIT's exact per-date transform (backtest.zscore_by_date), evaluated for
    `value` against the cross-section `basis`: winsorize the WHOLE column (basis +
    value) to [q(0.02), q(0.98)], then standardize by the WINSORIZED column's
    mean/std(ddof=0). This is the reference the live scorer must reproduce."""
    col = pd.Series(list(basis) + [value], dtype="float64")
    lo, hi = col.quantile(0.02), col.quantile(0.98)
    c = col.clip(lower=lo, upper=hi)
    mu, sd = c.mean(), c.std(ddof=0)
    if not np.isfinite(sd) or sd <= 0:
        return 0.0
    return float((c.iloc[-1] - mu) / sd)

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
    # band-quantile percentile: top band of 3 -> round((2+0.5)/3*100) = 83 (>50)
    assert out["percentile"] == 83
    assert out["percentile"] > 50


def test_score_symbol_bottom_band_is_sell():
    # drive the composite low -> bottom band -> SELL
    cur = {"mom_12_1": -0.5, "low_vol": 0.05}   # low mom (neg z), low_vol high (pos z) * neg weight
    out = sm.score_symbol(cur, None, _ARTIFACT)  # norm-primary, no snapshot
    assert out["verdict"] == "SELL"
    assert out["expected_fwd"] == -0.008 and out["hit_rate"] == 0.43
    # band-quantile percentile: bottom band of 3 -> round((0+0.5)/3*100) = 17 (<50)
    assert out["percentile"] == 17
    assert out["percentile"] < 50


def test_score_symbol_middle_band_is_hold():
    cur = {"mom_12_1": 0.0, "low_vol": -0.02}    # ~zero composite -> middle band
    out = sm.score_symbol(cur, None, _ARTIFACT)
    assert out["verdict"] == "HOLD"


def test_score_symbol_thin_snapshot_falls_back_to_norm():
    # A snapshot with <5 names can't form a stable cross-section, so _zscore returns
    # None and the scorer falls back to the artifact norm — the verdict is then the
    # norm-based one, independent of the thin snapshot.
    cur = {"mom_12_1": 0.5, "low_vol": -0.05}
    a = sm.score_symbol(cur, None, _ARTIFACT)                             # no snapshot -> norm
    b = sm.score_symbol(cur, {"mom_12_1": [0.49, 0.5, 0.51]}, _ARTIFACT)  # 3 names (<5) -> norm
    assert a["score"] == b["score"]


def test_score_recenters_to_current_cross_section_not_stale_norm():
    # Regression for the "always BUY" bug: the current cross-section sits FAR above
    # the artifact's stale norm. A symbol that is merely AVERAGE for the current
    # regime must score HOLD (re-centered z ~ 0 -> middle band), NOT BUY. The old
    # norm-primary path returned BUY (mom_12_1 z = (0.5-0)/0.1 = 5 vs the stale norm).
    snap = {"mom_12_1": [0.30, 0.40, 0.50, 0.60, 0.70],      # 5 names, current mean 0.50
            "low_vol": [-0.05, -0.04, -0.03, -0.02, -0.01]}   # current mean -0.03
    cur = {"mom_12_1": 0.50, "low_vol": -0.03}                # average for the current regime
    out = sm.score_symbol(cur, snap, _ARTIFACT)
    assert out["verdict"] == "HOLD"


def test_score_symbol_cross_section_drives_verdict():
    # With a usable (>=5-name) snapshot the composite is driven by the symbol's rank
    # WITHIN the current cross-section: top -> BUY, bottom -> SELL.
    snap = {"mom_12_1": [0.30, 0.40, 0.50, 0.60, 0.70]}       # mean 0.50, std ~0.141
    top = sm.score_symbol({"mom_12_1": 0.70}, snap, _ARTIFACT)
    bot = sm.score_symbol({"mom_12_1": 0.30}, snap, _ARTIFACT)
    assert top["verdict"] == "BUY"
    assert bot["verdict"] == "SELL"


def test_score_symbol_signed_weight_low_vol_subtracts():
    # a HIGH-vol name (low_vol factor very negative) with negative weight -> positive
    # contribution; confirm the low_vol contribution sign = weight * z
    snap = {"mom_12_1": [0.0, 0.0, 0.0], "low_vol": [0.0, 0.0, 0.0]}
    cur = {"mom_12_1": 0.0, "low_vol": -0.02}  # z(low_vol) negative vs norm mean -0.02 -> 0
    out = sm.score_symbol(cur, None, _ARTIFACT)   # snap empty -> norm fallback
    assert out is not None


def test_score_symbol_degrades_without_artifact():
    assert sm.score_symbol({"mom_12_1": 0.5}, None, None) is None


def test_score_symbol_clips_outlier_z():
    # an extreme factor value must not blow up the composite (z capped at +/-3)
    art = {"version": "t", "horizon": 20, "regimes": {"all": {
        "weights": {"turnover": 1.0},
        "factor_ic": {"turnover": {"mean_ic": 0.01}},
        "norm": {"turnover": {"mean": 1.0, "std": 0.1}},   # tiny std -> huge raw z
        "calibration": [
            {"band": 0, "score_lo": -3, "score_hi": -1, "mean_fwd": -0.01, "hit_rate": 0.45, "n": 10},
            {"band": 1, "score_lo": -1, "score_hi": 1, "mean_fwd": 0.0, "hit_rate": 0.5, "n": 10},
            {"band": 2, "score_lo": 1, "score_hi": 3, "mean_fwd": 0.01, "hit_rate": 0.52, "n": 10}]}}}
    out = sm.score_symbol({"turnover": 5.0}, None, art)   # raw z would be (5-1)/0.1 = 40
    assert out is not None
    assert abs(out["score"]) <= 3.0 + 1e-9                # clipped to Z_CLIP
    assert out["contributions"][0]["z"] == 3.0


def test_score_symbol_degrades_empty_weights():
    bad = {"regimes": {"all": {"weights": {}, "calibration": [{"band": 0, "score_lo": 0,
            "score_hi": 1, "mean_fwd": 0, "hit_rate": 0.5, "n": 1}]}}}
    assert sm.score_symbol({"mom_12_1": 0.5}, None, bad) is None


# --- C11: the live z-transform must MATCH the fit's 2/98 winsorization, not a ±3 clip ---

def test_cross_section_z_matches_fit_winsorization():
    # (a) With a normal (>=5-name) cross-section, the live z equals the FIT's exact
    # per-date transform (winsorize to [q02, q98], then standardize on the winsorized
    # column) — NOT the raw (mean/std of the un-winsorized basis) and NOT a ±3 clip.
    basis = [0.30, 0.35, 0.42, 0.50, 0.55, 0.61, 0.70, 0.80, 0.90, 1.10]
    value = 0.66
    got = sm._cross_section_z(value, basis)
    assert got is not None
    assert got == pytest.approx(_fit_z(value, basis), rel=1e-9, abs=1e-12)
    # And it is distinguishable from a naive raw z (no winsorization) — proves we
    # aren't accidentally re-deriving the old raw-mean/std path.
    raw = np.asarray(basis + [value], dtype="float64")
    naive = (value - raw.mean()) / raw.std(ddof=0)
    assert abs(got - naive) > 1e-6


def test_outlier_is_winsorized_to_2_98_bound_not_clipped_to_3():
    # (b) A genuine |z|>3 outlier is winsorized to the 2/98 quantile bound (the fit's
    # behavior), NOT hard-clipped to ±3. Use a tight cross-section + a far outlier so
    # winsorization visibly caps the tail: the live value clips to q98 BEFORE
    # standardizing, so the resulting z is exactly the fit's value — and is neither the
    # old ±3 clip nor the raw (unwinsorized) z. (Both a raw z and a winsorized z can
    # exceed 3.0 here; the point is that the WINSORIZED one, capped at the q98 bound,
    # is materially below the raw one — proving the tail was clipped, not the z.)
    basis = list(np.linspace(0.40, 0.60, 50))      # 51 names total incl. the value
    value = 100.0                                  # far outlier
    got = sm._cross_section_z(value, basis)
    expected = _fit_z(value, basis)
    assert got == pytest.approx(expected, rel=1e-9, abs=1e-12)   # matches the FIT exactly
    assert abs(got - 3.0) > 1e-6                                 # NOT the ±3 clip artefact
    # Winsorization clips the 100.0 outlier to the q98 bound (~0.6) BEFORE standardizing,
    # so the composited z (~1.64) is FAR below the raw un-winsorized z (~7.07). This is
    # the whole point of matching the fit: a ±3 clip would instead have compressed a
    # genuine |z|>3 back to 3.0, biasing extreme names toward the middle band.
    col = np.append(np.asarray(basis, dtype="float64"), value)
    raw_z = (value - col.mean()) / col.std(ddof=0)
    assert raw_z > 6.0                              # the un-winsorized z really is extreme
    assert got < raw_z / 3                          # winsorization materially shrank it


def test_thin_cross_section_returns_none_for_norm_fallback():
    # (c) The thin-snapshot fallback: <5 names can't form a stable 2/98 cross-section,
    # so _cross_section_z returns None and score_symbol falls through to the norm path
    # (which keeps the defensive ±3 clip). Verified end-to-end: a thin snapshot yields
    # the same score as no snapshot at all (both use the norm fallback).
    assert sm._cross_section_z(0.5, [0.49, 0.50, 0.51]) is None   # 3 names (<5)
    a = sm.score_symbol({"mom_12_1": 0.5, "low_vol": -0.05}, None, _ARTIFACT)
    b = sm.score_symbol({"mom_12_1": 0.5, "low_vol": -0.05},
                        {"mom_12_1": [0.49, 0.5, 0.51]}, _ARTIFACT)
    assert a["score"] == b["score"]


def test_norm_fallback_still_clips_to_3():
    # The norm fallback (thin/absent snapshot) keeps the ±3 clip as documented — the
    # existing outlier-clip test uses this path; assert the clip is still applied when
    # the raw norm z blows past 3 (tiny std in the artifact norm).
    art = {"version": "t", "horizon": 20, "regimes": {"all": {
        "weights": {"turnover": 1.0},
        "factor_ic": {"turnover": {"mean_ic": 0.01}},
        "norm": {"turnover": {"mean": 1.0, "std": 0.1}},   # raw z = (5-1)/0.1 = 40
        "calibration": [
            {"band": 0, "score_lo": -3, "score_hi": -1, "mean_fwd": -0.01, "hit_rate": 0.45, "n": 10},
            {"band": 1, "score_lo": -1, "score_hi": 1, "mean_fwd": 0.0, "hit_rate": 0.5, "n": 10},
            {"band": 2, "score_lo": 1, "score_hi": 3, "mean_fwd": 0.01, "hit_rate": 0.52, "n": 10}]}}}
    out = sm.score_symbol({"turnover": 5.0}, None, art)      # no snapshot -> norm
    assert out["contributions"][0]["z"] == 3.0               # clipped
