"""VIX-family scoring: term structure, VIX1D (0DTE), and VIX9D/VIX slope.

Pure functions lifted verbatim from ``sentiment_dashboard.py`` (v3.9.1).
Each returns a :class:`ScoreResult`. No tkinter or I/O imports.
"""
from . import VIX_SUB_WEIGHTS
from .types import ScoreResult


def _vix_term_piecewise(ratio):
    """v3.9 piecewise mapping for VIX/10d_MA → raw score (float, 1..10).

    Replicates ``SentimentDashboardApp._vix_term_piecewise``.
    """
    if ratio is None:
        return 5.0
    if ratio < 0.85:
        return 10.0
    if ratio < 0.95:
        return 7.0 + (ratio - 0.85) / 0.10 * 2.0
    if ratio < 1.05:
        return 5.0 + (ratio - 0.95) / 0.10 * 1.0
    if ratio < 1.15:
        return 4.0 - (ratio - 1.05) / 0.10 * 1.0
    if ratio < 1.30:
        return 2.0 - (ratio - 1.15) / 0.15 * 1.0
    return 1.0


def score_term(vix: float, vix_ma: float,
               vix_term_regime: str = "Flat") -> ScoreResult:
    """Score the VIX term structure component (the legacy ``vix_term``).

    Parameters
    ----------
    vix : float
        Current VIX level. <= 0 returns an undefined ScoreResult.
    vix_ma : float
        VIX 10-day moving average. If <= 0, falls back to the
        categorical ``vix_term_regime`` mapping with reduced confidence.
    vix_term_regime : str
        One of ``"Contango"``, ``"Flat"``, ``"Backwardation"`` used only
        when the MA is unavailable.
    """
    if vix is None or vix <= 0:
        return ScoreResult(score=0, confidence=0.0, interp="")
    if vix_ma and vix_ma > 0:
        ratio = vix / vix_ma
        raw = _vix_term_piecewise(ratio)
        score = max(1, min(10, round(raw)))
        confidence = 1.0
        ratio_str = f"{ratio:.2f}x MA"
    else:
        mapping = {"Contango": 8, "Flat": 5, "Backwardation": 2}
        score = mapping.get(vix_term_regime, 5)
        confidence = (1.0 / 2.0) ** 0.5  # only VIX level, not the ratio
        ratio_str = "MA n/a"

    if score >= 8:
        interp = (f"VIX {vix:.1f} ({ratio_str}, {vix_term_regime}) — "
                  f"calm term structure, bullish")
    elif score >= 5:
        interp = (f"VIX {vix:.1f} ({ratio_str}, {vix_term_regime}) — "
                  f"neutral term structure")
    else:
        interp = (f"VIX {vix:.1f} ({ratio_str}, {vix_term_regime}) — "
                  f"stress term structure, bearish")
    return ScoreResult(score=score, confidence=confidence, interp=interp)


def score_vix1d(vix1d: float, vix: float,
                vix1d_prior: float = 0.0) -> ScoreResult:
    """0DTE volatility expansion gauge: VIX1D / VIX ratio (v3.9.1 bands)."""
    if not vix1d or vix1d <= 0 or not vix or vix <= 0:
        return ScoreResult(score=0, confidence=0.0, interp="")
    ratio = vix1d / vix
    if ratio < 0.80:
        raw = 10.0
    elif ratio < 0.88:
        raw = 7.0 + (ratio - 0.80) / 0.08 * 2.0
    elif ratio < 0.98:
        raw = 5.0 + (ratio - 0.88) / 0.10 * 1.0
    elif ratio < 1.05:
        raw = 4.0 - (ratio - 0.98) / 0.07 * 1.0
    elif ratio < 1.15:
        raw = 2.0 - (ratio - 1.05) / 0.10 * 1.0
    else:
        raw = 1.0
    score = max(1, min(10, round(raw)))
    delta = (vix1d - vix1d_prior) if vix1d_prior else None
    delta_str = f" (Δ {delta:+.2f})" if delta is not None else ""
    interp = (f"VIX1D {vix1d:.2f}{delta_str} / VIX {vix:.2f} = {ratio:.3f}")
    return ScoreResult(score=score, confidence=1.0, interp=interp)


def score_term_slope(vix9d: float, vix: float) -> ScoreResult:
    """VIX9D / VIX slope (v3.9.1 bands). Backwardation = leading risk-off."""
    if not vix9d or vix9d <= 0 or not vix or vix <= 0:
        return ScoreResult(score=0, confidence=0.0, interp="")
    slope = vix9d / vix
    if slope < 0.85:
        raw = 10.0
    elif slope < 0.92:
        raw = 7.0 + (slope - 0.85) / 0.07 * 2.0
    elif slope < 1.00:
        raw = 5.0 + (slope - 0.92) / 0.08 * 1.0
    elif slope < 1.05:
        raw = 4.0 - (slope - 1.00) / 0.05 * 1.0
    else:
        raw = 1.0
    score = max(1, min(10, round(raw)))
    regime = ("Contango" if slope < 0.98
              else ("Flat" if slope < 1.02 else "Backwardation"))
    interp = f"VIX9D {vix9d:.2f} / VIX {vix:.2f} = {slope:.3f} [{regime}]"
    return ScoreResult(score=score, confidence=1.0, interp=interp)


def score_complex(term_result: ScoreResult,
                  vix1d_result: ScoreResult,
                  slope_result: ScoreResult) -> ScoreResult:
    """Merge the three VIX sub-scores into a single VIX Complex score.

    Uses :data:`VIX_SUB_WEIGHTS` (term 0.50, vix1d 0.33, slope 0.17 —
    the legacy 15/10/5 composite weights, normalized to 1.0 within the
    combined 30% VIX Complex slot).

    An undefined sub-component (``score == 0``) DROPS OUT: the score is
    renormalized over the weight actually present, matching ``composite.blend``
    and every other blend in this package. Before 2026-08-20 it contributed a
    zero to the numerator with no change to the denominator, which dragged the
    published score toward 1 whenever $VIX1D was unavailable (routine off-hours).

    The confidence is deliberately NOT renormalized -- it stays the weighted sum
    of the sub-confidences, so a reading resting on fewer components is
    down-weighted by the top-level composite exactly as before.
    """
    w_term = VIX_SUB_WEIGHTS["term"]
    w_v1d = VIX_SUB_WEIGHTS["vix1d"]
    w_slope = VIX_SUB_WEIGHTS["slope"]
    present = [(w, r) for w, r in ((w_term, term_result),
                                   (w_v1d, vix1d_result),
                                   (w_slope, slope_result)) if r.score > 0]
    den = sum(w for w, _ in present)
    raw = (sum(w * r.score for w, r in present) / den) if den > 0 else 0.0
    conf = (w_term * term_result.confidence
            + w_v1d * vix1d_result.confidence
            + w_slope * slope_result.confidence)
    if raw <= 0 and conf <= 0:
        return ScoreResult(score=0, confidence=0.0, interp="")
    score = max(1, min(10, round(raw)))
    interp = (f"Term {term_result.score} · "
              f"1D {vix1d_result.score} · "
              f"Slope {slope_result.score}")
    return ScoreResult(score=score, confidence=conf, interp=interp)
