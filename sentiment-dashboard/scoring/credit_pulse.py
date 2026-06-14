"""Credit Pulse scoring — HYG/IEI z-score + HYG vs 50d MA distance.

Pure function lifted from ``sentiment_dashboard.calculate_credit_score``.
"""
from typing import Optional

from .types import ScoreResult


def score(credit_hist: Optional[dict]) -> ScoreResult:
    """Score the Credit Pulse component.

    Parameters
    ----------
    credit_hist : dict or None
        Must contain ``hyg_closes`` and ``iei_closes`` — lists of >= 60
        daily closes. Anything else returns an undefined ScoreResult.

    Returns
    -------
    ScoreResult
        ``.interp`` carries a tuple-equivalent state payload encoded for
        the UI; callers that need the structured ``state`` dict should
        use :func:`score_with_state` instead.
    """
    res, _state = score_with_state(credit_hist)
    return res


def score_with_state(credit_hist: Optional[dict]):
    """Like :func:`score` but also returns the diagnostics dict the UI
    stashes in ``self._credit_state``.

    Returns ``(ScoreResult, state_dict_or_None)``.
    """
    if not credit_hist:
        return ScoreResult(score=0, confidence=0.0, interp=""), None
    hyg = credit_hist.get('hyg_closes') or []
    iei = credit_hist.get('iei_closes') or []
    if len(hyg) < 60 or len(iei) < 60:
        confidence = (min(len(hyg), len(iei)) / 60.0) ** 0.5
        return (
            ScoreResult(score=0, confidence=confidence, interp=""),
            None,
        )

    ratios = [h / i for h, i in zip(hyg, iei) if i > 0]
    if len(ratios) < 60:
        return ScoreResult(score=0, confidence=0.0, interp=""), None

    last_ratio = ratios[-1]
    mean = sum(ratios) / len(ratios)
    var = sum((r - mean) ** 2 for r in ratios) / len(ratios)
    std = var ** 0.5
    z = (last_ratio - mean) / std if std > 0 else 0.0

    if   z > 2.0:   sub_a = 10.0
    elif z > 0.75:  sub_a = 7.0 + (z - 0.75) / 1.25 * 2.0
    elif z > -0.75: sub_a = 5.0 + (z + 0.75) / 1.50 * 1.0
    elif z > -2.0:  sub_a = 3.0 + (z + 2.0) / 1.25 * 1.0
    else:           sub_a = 1.0
    sub_a = max(1.0, min(10.0, sub_a))

    ma_50 = sum(hyg[-50:]) / 50
    hyg_last = hyg[-1]
    dist_pct = (hyg_last - ma_50) / ma_50 * 100 if ma_50 > 0 else 0
    if   dist_pct > 3:    sub_b = 10.0
    elif dist_pct > 1:    sub_b = 7.0 + (dist_pct - 1) / 2.0 * 2.0
    elif dist_pct > -1:   sub_b = 5.0 + dist_pct / 1.0 * 0.5
    elif dist_pct > -3:   sub_b = 3.0 + (dist_pct + 3.0) / 2.0 * 1.0
    else:                 sub_b = 1.0
    sub_b = max(1.0, min(10.0, sub_b))

    combined = 0.6 * sub_a + 0.4 * sub_b
    s = max(1, min(10, round(combined)))
    interp = (f"HYG/IEI z={z:+.2f} (sub_a {sub_a:.1f}) | "
              f"HYG vs 50d MA {dist_pct:+.2f}% (sub_b {sub_b:.1f}) → {s}")
    state = {
        'z': z, 'sub_a': sub_a, 'sub_b': sub_b,
        'dist_pct': dist_pct, 'last_ratio': last_ratio,
        'combined_raw': combined,
    }
    return ScoreResult(score=s, confidence=1.0, interp=interp), state
