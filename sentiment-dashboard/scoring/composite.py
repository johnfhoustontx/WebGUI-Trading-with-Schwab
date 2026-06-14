"""Composite blending: confidence-weighted score, velocity, divergence.

Pure functions. Replicates the math in
``sentiment_dashboard.calculate_all_scores``, ``_update_velocity``, and
``_update_divergence_flag`` without touching tkinter.
"""
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


def blend(scores: Mapping[str, float],
          confs: Mapping[str, float],
          weights: Mapping[str, float]) -> Tuple[float, float]:
    """Confidence-weighted blend.

    composite = Σ(w_i · score_i · conf_i) / Σ(w_i · conf_i)
    agg_conf  = Σ(w_i · conf_i)            (== weighted-avg conf since Σw=1)

    Components missing from ``scores`` or ``confs`` are treated as 0.
    Returns ``(composite, agg_conf)``. Both 0.0 when denominator is 0.
    """
    numerator = 0.0
    denominator = 0.0
    for key, w in weights.items():
        s = float(scores.get(key, 0.0) or 0.0)
        c = float(confs.get(key, 0.0) or 0.0)
        numerator += w * s * c
        denominator += w * c
    composite = numerator / denominator if denominator > 0 else 0.0
    return composite, denominator


def velocity(history_scores: Sequence[float],
             today_score: float) -> dict:
    """Compute 3d ROC, 5d ROC, 20d z-score, and regime-break flag.

    ``history_scores`` is the list of prior composite scores (filtered
    to exclude zeros, matching the original behavior). Returns dict with
    ``roc_3d``, ``roc_5d``, ``z_20d`` (any may be None), and
    ``regime_break`` (bool).
    """
    scores = list(history_scores)
    roc_3d = (today_score - scores[-3]) if len(scores) >= 3 else None
    roc_5d = (today_score - scores[-5]) if len(scores) >= 5 else None
    z = None
    if len(scores) >= 5:
        sample = scores[-20:]
        mean = sum(sample) / len(sample)
        var = sum((s - mean) ** 2 for s in sample) / len(sample)
        std = var ** 0.5
        if std > 0:
            z = (today_score - mean) / std
    return {
        'roc_3d': roc_3d,
        'roc_5d': roc_5d,
        'z_20d': z,
        'regime_break': z is not None and abs(z) > 1.5,
    }


def divergence(named_scores: Sequence[Tuple[str, float]]) -> Optional[str]:
    """Return ``"DIVERGENCE: X N vs Y M — low conviction"`` when any two
    components disagree by >= 4 points, else ``None``.

    ``named_scores`` is a sequence of ``(display_name, score)`` tuples;
    callers should pre-filter to scored, confident components.
    """
    scored = [(n, s) for n, s in named_scores if s and s > 0]
    if len(scored) < 2:
        return None
    scored = sorted(scored, key=lambda x: x[1])
    lo_name, lo_s = scored[0]
    hi_name, hi_s = scored[-1]
    if hi_s - lo_s >= 4:
        return (f"DIVERGENCE: {hi_name} {int(hi_s)} vs "
                f"{lo_name} {int(lo_s)} — low conviction")
    return None
