"""Live swing-model scorer (Tier-2). Loads the offline artifact and scores ONE
symbol's current factors against a cached universe snapshot (cross-sectional, to
match how the calibration was built), falling back to the artifact's historical
per-factor norm. Pure scoring; the artifact loader is thin (monkeypatched in
tests). Defensive: returns None on any failure so analyze() falls back to legacy.

Weights are SIGNED (a negative-IC factor like low_vol carries a negative weight),
so the composite is sum(weight * zscore). The BUY/HOLD/SELL verdict is taken from
which calibration band the composite lands in (top band -> BUY, bottom -> SELL),
which is self-calibrating to the fitted hit-rate distribution."""
import json
import numpy as np
from repo_paths import SWING_MODEL


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


def _percentile(comp, calib):
    lo, hi = calib[0]["score_lo"], calib[-1]["score_hi"]
    if hi <= lo:
        return 50
    return int(np.clip((comp - lo) / (hi - lo) * 100, 0, 100))


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
            if v is None or not np.isfinite(v):
                continue
            basis = (universe_snapshot or {}).get(f)
            z = _zscore(v, basis) if basis else None
            if z is None and f in norm and norm[f].get("std"):
                z = (v - norm[f]["mean"]) / norm[f]["std"] if norm[f]["std"] else None
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
            "percentile": _percentile(comp, calib),
            "expected_fwd": band["mean_fwd"], "hit_rate": band["hit_rate"],
            "horizon_days": artifact.get("horizon", 20),
            "contributions": sorted(contribs, key=lambda d: abs(d["contribution"]), reverse=True),
            "model_version": artifact.get("version"), "oos_ic": reg.get("oos_ic"),
            "source": "validated",
        }
    except Exception:
        return None
