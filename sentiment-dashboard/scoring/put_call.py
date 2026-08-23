"""Put/Call ratio scoring. Pure function lifted from sentiment_dashboard.py."""
from typing import Mapping, Optional

from .types import ScoreResult
from ._common import score_from_thresholds

# Mirrors PC_THRESHOLDS in sentiment_dashboard.py (kept local to keep this
# module decoupled from the UI shell).
PC_THRESHOLDS = [(1.3, 1), (1.1, 2), (0.9, 5), (0.7, 8), (0.0, 10)]


def score(pc_equity: float, pc_ma: float = 0.0,
          options_skew: str = "Normal") -> ScoreResult:
    """Score equity Put/Call ratio with MA + skew adjustments.

    Parameters
    ----------
    pc_equity : float
        CBOE Equity Put/Call ratio. <= 0 returns undefined.
    pc_ma : float
        Trailing MA of the P/C ratio (optional bonus signal).
    options_skew : str
        ``"Normal"``, ``"Elevated"``, or ``"Inverted"``.
    """
    if pc_equity is None or pc_equity <= 0:
        return ScoreResult(score=0, confidence=0.0, interp="")

    s = score_from_thresholds(pc_equity, PC_THRESHOLDS, ascending=True)
    if pc_ma and pc_ma > 0:
        r = pc_equity / pc_ma
        if r > 1.15:
            s = max(1, s - 1)        # put spike = more bearish
        elif r < 0.85:
            s = min(10, s + 1)       # call lean = more bullish
    if options_skew == "Elevated":
        s = max(1, s - 1)            # elevated put skew = bearish
    elif options_skew == "Inverted":
        s = min(10, s + 1)           # inverted skew = bullish
    s = max(1, min(10, s))

    present = 1 + (1 if pc_ma and pc_ma > 0 else 0) + (
        1 if options_skew != "Normal" else 0)
    confidence = (present / 3.0) ** 0.5

    if s >= 8:
        interp = f"P/C {pc_equity:.2f} — call-dominant, bullish options sentiment"
    elif s >= 5:
        interp = f"P/C {pc_equity:.2f} — balanced options"
    else:
        interp = f"P/C {pc_equity:.2f} — put-heavy, bearish options sentiment"
    return ScoreResult(score=s, confidence=confidence, interp=interp)


def score_sector_weighted(
    sector_pcr: Optional[Mapping[str, float]],
    sp_weights: Mapping[str, float],
) -> ScoreResult:
    """Cap-weighted blend of per-sector ETF Put/Call ratios.

    Parameters
    ----------
    sector_pcr : Mapping[str, float] | None
        ``{etf_symbol: pcr}`` for each sector ETF that returned a usable
        chain. Missing sectors are downweighted, not excluded with a
        penalty.
    sp_weights : Mapping[str, float]
        ``{etf_symbol: sp500_weight_pct}`` (percentages; relative weights
        only — the function normalizes).
    """
    if not sector_pcr or not sp_weights:
        return ScoreResult(score=0, confidence=0.0, interp="")

    num = 0.0
    den = 0.0
    used = 0
    for etf, pcr in sector_pcr.items():
        if pcr is None or pcr <= 0:
            continue
        w = float(sp_weights.get(etf, 0.0) or 0.0)
        if w <= 0:
            continue
        num += pcr * w
        den += w
        used += 1
    if den <= 0 or used == 0:
        return ScoreResult(score=0, confidence=0.0, interp="")

    blended = num / den
    raw = score_from_thresholds(blended, PC_THRESHOLDS, ascending=True)
    s = max(1, min(10, int(raw)))
    n_possible = max(len(sp_weights), 1)
    confidence = (used / float(n_possible)) ** 0.5

    if s >= 8:
        interp = (f"Cap-weighted sector P/C {blended:.2f} "
                  f"({used}/{n_possible} sectors) — call-dominated")
    elif s >= 5:
        interp = (f"Cap-weighted sector P/C {blended:.2f} "
                  f"({used}/{n_possible} sectors) — balanced")
    else:
        interp = (f"Cap-weighted sector P/C {blended:.2f} "
                  f"({used}/{n_possible} sectors) — put-dominated")
    return ScoreResult(score=s, confidence=confidence, interp=interp)


def pcr_from_chain(chain):
    """Sum put vs call ``totalVolume`` from a Schwab /chains payload -> ratio.

    Returns None when there is no chain or no call volume. THE one copy: this
    lived byte-for-byte in both ``services/sentiment_svc/compute.py`` and
    ``sentiment-dashboard/live_composite.py``, two tiers feeding the SAME
    composite — so a threshold tweak in one would have silently diverged the live
    P/C from the sector table's (consolidated 2026-08-20). Ported from source
    sentiment_dashboard.py:2939-2953.
    """
    if not chain:
        return None
    pv = cv = 0
    for strikes in (chain.get("putExpDateMap") or {}).values():
        for contracts in strikes.values():
            for c in contracts:
                v = c.get("totalVolume", 0) or 0
                if v > 0:
                    pv += v
    for strikes in (chain.get("callExpDateMap") or {}).values():
        for contracts in strikes.values():
            for c in contracts:
                v = c.get("totalVolume", 0) or 0
                if v > 0:
                    cv += v
    return round(pv / cv, 3) if cv > 0 else None
