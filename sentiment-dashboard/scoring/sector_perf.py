"""S&P cap-weighted sector performance scoring.

Pure functions lifted from ``sentiment_dashboard.py``
(``_weighted_sector_pct`` + ``_sectors_score``).
"""
from typing import Dict, List, Tuple, Optional


def weighted_sector_pct(
    sector_data: List[dict],
    last_sector_quotes: Dict[str, dict],
) -> Tuple[Optional[float], float]:
    """S&P cap-weighted average daily % move across the 11 sectors.

    Returns ``(weighted_pct, weight_sum)`` or ``(None, 0)`` if no data.
    """
    total_w = 0.0
    weighted = 0.0
    for row in sector_data:
        if row.get('kind') != 'sector':
            continue
        etf = row.get('etf')
        q = last_sector_quotes.get(etf, {}) if etf else {}
        pct = q.get('change_pct')
        if pct is None:
            continue
        w = row.get('sp_weight', 0.0)
        weighted += pct * w
        total_w += w
    if total_w == 0:
        return None, 0
    return weighted / total_w, total_w


def sectors_score(
    sector_data: List[dict],
    last_sector_quotes: Dict[str, dict],
) -> Optional[float]:
    """Sentiment score from S&P cap-weighted sector moves, on the composite's
    1..10 scale, or ``None`` when there is no sector data.

    +2% weighted move → 10; 0% → 5; -1.6% or worse → 1. Adjusted +1 when at
    least 80% of sectors are up and -1 when at least 80% are DOWN.

    ⚠ Two things changed on 2026-10-04 (audit AC-49), and callers rely on both:

    * Absence is ``None``. It was 0.0 - the same value the clamp gave a real
      crash day - and the history backfill then deleted every day scoring 0.
    * A real day never scores below 1.0. On this scale 0 means "no reading".

    The penalty used to fire when at most 20% of sectors were UP, so a flat
    tape (nothing up, nothing down) scored 4.0. It now needs sectors that fell.
    """
    wpct, _ = weighted_sector_pct(sector_data, last_sector_quotes)
    if wpct is None:
        return None
    score = 5.0 + wpct * 2.5
    pcts = []
    for row in sector_data:
        if row.get('kind') != 'sector':
            continue
        etf = row.get('etf')
        q = last_sector_quotes.get(etf, {}) if etf else {}
        p = q.get('change_pct')
        if p is not None:
            pcts.append(p)
    if pcts:
        pct_up = sum(1 for p in pcts if p > 0) / len(pcts)
        pct_down = sum(1 for p in pcts if p < 0) / len(pcts)
        if pct_up >= 0.80:
            score += 1.0
        elif pct_down >= 0.80:
            score -= 1.0
    return round(max(1.0, min(10.0, score)), 2)
