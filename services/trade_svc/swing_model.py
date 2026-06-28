"""Live swing-model scorer (Tier-2). Scores ONE symbol's current factors
CROSS-SECTIONALLY — each factor z-scored against the SAME factor across the
current universe snapshot. This matches how the offline calibration bands were
built (``zscore_by_date`` winsorizes+standardizes per date, i.e. relative to that
day's cross-section) and, crucially, RE-CENTERS to the current regime: a
market-wide shift (e.g. elevated momentum/volatility in a bull run) no longer
pushes every symbol into the top band.

The artifact's time-averaged per-factor ``norm`` is a FALLBACK only — used when the
live universe snapshot is too thin (<5 names) or absent. (It was previously the
PRIMARY basis, which made every symbol score BUY: the stale 5-yr norm does not
re-center, so in an elevated regime every z shifts positive into the top/BUY
band.) Pure scoring; the artifact loader is thin (monkeypatched in tests).
Defensive: returns None on any failure so analyze() falls back to legacy.

Weights are SIGNED (a negative-IC factor like low_vol carries a negative weight),
so the composite is sum(weight * zscore). The BUY/HOLD/SELL verdict is taken from
which calibration band the composite lands in (top band -> BUY, bottom -> SELL)."""
import json
import numpy as np
from repo_paths import SWING_MODEL

Z_CLIP = 3.0   # cap live z-scores; the offline fit winsorized per-date (2/98) so
               # per-date z was bounded — this prevents a live outlier (e.g. a
               # volume spike in `turnover`) from hijacking the signed composite.


def load_artifact():
    try:
        return json.loads(SWING_MODEL.read_text(encoding="utf-8"))
    except Exception:
        return None


def _zscore(value, basis):
    arr = np.asarray(basis, dtype="float64")
    arr = arr[np.isfinite(arr)]
    if len(arr) < 5:
        return None
    mu, sd = float(arr.mean()), float(arr.std(ddof=0))
    return (value - mu) / sd if sd > 0 else 0.0


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
                z = _zscore(v, basis)                      # cross-section (calibration-consistent;
                                                           # _zscore needs >=5 names, else None)
            if z is None:                                  # thin/absent snapshot -> norm fallback
                nf = norm.get(f)
                if nf and nf.get("std"):
                    z = (v - nf["mean"]) / nf["std"]       # stale 5-yr norm (NOT regime-centered)
            if z is not None:
                z = float(np.clip(z, -Z_CLIP, Z_CLIP))
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
