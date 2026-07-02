"""Live swing-model scorer (Tier-2). Scores ONE symbol's current factors
CROSS-SECTIONALLY — each factor z-scored against the SAME factor across the
current universe snapshot. This matches how the offline calibration bands were
built (``zscore_by_date`` winsorizes+standardizes per date, i.e. relative to that
day's cross-section) and, crucially, RE-CENTERS to the current regime: a
market-wide shift (e.g. elevated momentum/volatility in a bull run) no longer
pushes every symbol into the top band.

The cross-sectional z uses the SAME transform as the offline fit
(``backtest.zscore_by_date``): winsorize the current cross-section to the 2nd/98th
percentiles then standardize by the winsorized column's mean/std — a genuine 2/98
winsorization, NOT a hard ±3 clip. This keeps the live composite on the exact scale
the calibration bands were built on, so a live symbol with a true |z|>3 is not biased
toward the middle band (fixes C11 mild tail miscalibration).

The artifact's time-averaged per-factor ``norm`` is a FALLBACK only — used when the
live universe snapshot is too thin (<5 names) or absent. (It was previously the
PRIMARY basis, which made every symbol score BUY: the stale 5-yr norm does not
re-center, so in an elevated regime every z shifts positive into the top/BUY
band.) The norm was never winsorized, so the norm-fallback path keeps a defensive ±3
clip (``Z_CLIP``). Pure scoring; the artifact loader is thin (monkeypatched in tests).
Defensive: returns None on any failure so analyze() falls back to legacy.

Weights are SIGNED (a negative-IC factor like low_vol carries a negative weight),
so the composite is sum(weight * zscore). The BUY/HOLD/SELL verdict is taken from
which calibration band the composite lands in (top band -> BUY, bottom -> SELL)."""
import json
import numpy as np
from repo_paths import SWING_MODEL

MIN_XSECTION = 5   # a cross-section thinner than this can't form stable 2/98
                   # quantiles -> fall back to the artifact norm (documented
                   # thin-snapshot fallback).
WINSOR = (0.02, 0.98)   # MUST match backtest.zscore_by_date's winsor bounds.
Z_CLIP = 3.0   # defensive cap for the NORM-FALLBACK path ONLY (the stale 5-yr norm
               # was NOT winsorized, so a live outlier against a tiny norm std could
               # otherwise blow up the signed composite). The PRIMARY cross-sectional
               # path is winsorized to match the fit instead — see _cross_section_z.

# NOTE (C12/C13, 2026-07-01): two known weighting/scoring limitations are documented
# but NOT fixed here — both require regenerating swing_model.json via a manual
# fit_swing_model.py refit (needs live proxy 5-yr data, out of scope for this session):
#   C12 — the fit's signed_ic_weights uses UNIVARIATE signed IC, which double-counts
#         the correlated momentum cluster (mom_12_1 / mom_6_1 / vol_adj_mom /
#         trend_quality). A covariance-aware weighting is the fix (offline harness
#         change + refit).
#   C13 — the dominant factor low_vol (weight -0.34) rests on a regime-overfit inverted
#         sign; regime-gating (the artifact's regime-key structure exists for it) is the
#         intended fix, also an offline refit.
# The shipped artifact and its weights are LEFT UNCHANGED. See
# docs/audits/2026-07-01-calculation-accuracy-audit.md.


def load_artifact():
    try:
        return json.loads(SWING_MODEL.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cross_section_z(value, basis):
    """z-score `value` against the current-universe cross-section `basis`, using the
    SAME transform the offline fit applies per date (backtest.zscore_by_date): treat
    (basis + value) as one cross-sectional column, winsorize it to [q(0.02), q(0.98)],
    then standardize by the WINSORIZED column's mean/std(ddof=0). This is a genuine
    2/98 winsorization — NOT a hard ±3 clip — so a live symbol with |z|>3 is composited
    on the SAME scale the calibration bands were built on (no tail miscalibration).

    Returns None when the cross-section is too thin (<MIN_XSECTION finite names) so the
    caller falls back to the artifact norm."""
    arr = np.asarray(basis, dtype="float64")
    arr = arr[np.isfinite(arr)]
    if len(arr) < MIN_XSECTION or not np.isfinite(value):
        return None
    # Include the live value in the cross-section exactly as the fit includes every
    # symbol's value on that date, so the same winsor bounds/mean/std apply to it.
    col = np.append(arr, float(value))
    lo = np.quantile(col, WINSOR[0])           # linear interpolation == pandas default
    hi = np.quantile(col, WINSOR[1])
    c = np.clip(col, lo, hi)                    # winsorize the whole column
    mu, sd = float(c.mean()), float(c.std(ddof=0))   # stats on the WINSORIZED column
    if not np.isfinite(sd) or sd <= 0:
        return 0.0
    return (float(c[-1]) - mu) / sd            # the (winsorized) live value's z


def _band_for(comp, calib):
    """Return (band_dict, index, n_bands) for the band whose score range contains
    comp (ascending bands; clamps to the top band above the last score_hi)."""
    for i, b in enumerate(calib):
        if comp <= b["score_hi"]:
            return b, i, len(calib)
    return calib[-1], len(calib) - 1, len(calib)


def _percentile(idx, n_bands):
    """Band-quantile percentile: the midpoint of the band's equal-n quantile range
    (e.g. top band of 5 -> ~90th, bottom -> ~10th). Matches the BUY/SELL verdict."""
    if n_bands <= 0:
        return 50
    return int(round((idx + 0.5) / n_bands * 100))


def score_symbol(current_factors, universe_snapshot, artifact):
    """current_factors: {factor: value} for the symbol now.
    universe_snapshot: {factor: [values across the watchlist]} or None.
    artifact: the loaded swing_model.json (or None). Returns the swing_model
    verdict dict, or None to degrade to the legacy verdict."""
    try:
        if not artifact:
            return None
        reg = artifact["regimes"]["all"]
        weights, norm, calib = reg.get("weights", {}), reg.get("norm", {}), reg.get("calibration", [])
        if not weights or not calib:
            return None
        contribs, comp = [], 0.0
        for f, w in weights.items():
            v = current_factors.get(f)
            if v is None or not isinstance(v, (int, float)) or not np.isfinite(v):
                continue
            z = None
            basis = (universe_snapshot or {}).get(f)
            if basis:                                      # PRIMARY: re-center to the current
                z = _cross_section_z(v, basis)             # cross-section, winsorized 2/98 to
                                                           # MATCH the fit's zscore_by_date exactly
                                                           # (needs >=MIN_XSECTION names, else None).
                                                           # Already bounded by winsorization — do
                                                           # NOT ±3-clip it (would re-compress the
                                                           # scale the bands were built on).
            if z is None:                                  # thin/absent snapshot -> norm fallback
                nf = norm.get(f)
                if nf and nf.get("std"):
                    z = (v - nf["mean"]) / nf["std"]       # stale 5-yr norm (NOT regime-centered);
                    z = float(np.clip(z, -Z_CLIP, Z_CLIP)) # unwinsorized norm -> defensive ±3 clip
            if z is None:
                continue
            c = w * z
            comp += c
            contribs.append({"factor": f, "z": round(z, 3), "weight": round(w, 3),
                             "contribution": round(c, 3),
                             "ic": reg.get("factor_ic", {}).get(f, {}).get("mean_ic")})
        if not contribs:
            return None
        band, idx, n = _band_for(comp, calib)
        verdict = "BUY" if idx >= n - 1 else "SELL" if idx <= 0 else "HOLD"
        return {
            "verdict": verdict, "score": round(comp, 3),
            "percentile": _percentile(idx, n),
            "expected_fwd": band["mean_fwd"], "hit_rate": band["hit_rate"],
            "horizon_days": artifact.get("horizon", 20),
            "contributions": sorted(contribs, key=lambda d: abs(d["contribution"]), reverse=True),
            "model_version": artifact.get("version"), "oos_ic": reg.get("oos_ic"),
            "source": "validated",
        }
    except Exception:
        return None
