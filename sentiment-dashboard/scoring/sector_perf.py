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
) -> float:
    """Compute 0..10 sentiment score from S&P cap-weighted sector moves.

    +2% weighted move → 10; -2% → 0; 0% → 5. Adjusted ±1 by the breadth
    of green sectors. Mirrors ``_sectors_score`` exactly.
    """
    wpct, _ = weighted_sector_pct(sector_data, last_sector_quotes)
    if wpct is None:
        return 0.0
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
        if pct_up >= 0.80:
            score += 1.0
        elif pct_up <= 0.20:
            score -= 1.0
    return round(max(0.0, min(10.0, score)), 2)
