"""Sector rotation scoring.

v4.4: Dual Momentum Ranking (3-month relative + $IRX absolute crash
filter) is the new score driver. RRG quadrants (Leading / Weakening /
Lagging / Improving) are computed per sector for UI display.

The legacy cyclical-vs-defensive spread (``compute_rotation`` /
``score_fallback``) is retained for backward compatibility with any
external importer.
"""
from typing import Dict, List, Mapping, Optional, Sequence

from .types import ScoreResult

CYCLICAL_SECTORS = {
    "Consumer Discretionary", "Financials", "Industrials",
    "Information Technology", "Communication Services",
    "Materials", "Energy",
}
DEFENSIVE_SECTORS = {
    "Consumer Staples", "Utilities", "Health Care", "Real Estate",
}

# Sector ETF → cyclical/defensive grouping.
# Source of truth for the new dual-momentum aggregation. ETFs not
# listed here are ignored by the cyc/def split (but still ranked).
CYCLICAL_ETFS = frozenset({"XLY", "XLF", "XLI", "XLK", "XLC", "XLB", "XLE"})
DEFENSIVE_ETFS = frozenset({"XLP", "XLU", "XLV", "XLRE"})


def _spread_to_score(spread_pct: Optional[float]) -> Optional[float]:
    """Linear map: -2% → 1, 0% → 5, +2% → 10, clipped to [1, 10]."""
    if spread_pct is None:
        return None
    return max(1.0, min(10.0, 5.0 + spread_pct * 2.5))


def compute_rotation(
    sector_data: List[dict],
    sector_trends: Dict[str, dict],
    last_sector_quotes: Dict[str, dict],
) -> Optional[dict]:
    """Compute blended day/3d/week cyclical-vs-defensive rotation signal.

    Returns the same rich dict the original ``_rotation_from_sectors``
    produced, or ``None`` when no sector rows / no usable timeframe.
    """
    if not sector_data:
        return None

    day_by_sector, day3_by_sector, wk_by_sector = {}, {}, {}
    for row in sector_data:
        if row.get('kind') != 'sector':
            continue
        etf = row.get('etf')
        if not etf:
            continue
        day = (last_sector_quotes.get(etf) or {}).get('change_pct')
        tr = sector_trends.get(etf) or {}
        d3 = tr.get('day3_pct')
        wk = tr.get('week_pct')
        if day is not None:
            day_by_sector[row['sector']] = day
        if d3 is not None:
            day3_by_sector[row['sector']] = d3
        if wk is not None:
            wk_by_sector[row['sector']] = wk
    if not (day_by_sector or day3_by_sector or wk_by_sector):
        return None

    def spread_of(m):
        cyc = [v for n, v in m.items() if n in CYCLICAL_SECTORS]
        defn = [v for n, v in m.items() if n in DEFENSIVE_SECTORS]
        if not cyc or not defn:
            return None, None, None
        cyc_avg = sum(cyc) / len(cyc)
        def_avg = sum(defn) / len(defn)
        return cyc_avg - def_avg, cyc_avg, def_avg

    day_sp,  day_cyc,  day_def  = spread_of(day_by_sector)
    d3_sp,   d3_cyc,   d3_def   = spread_of(day3_by_sector)
    wk_sp,   wk_cyc,   wk_def   = spread_of(wk_by_sector)

    day_score = _spread_to_score(day_sp)
    d3_score  = _spread_to_score(d3_sp)
    wk_score  = _spread_to_score(wk_sp)

    target_weights = {'day': 0.4, '3d': 0.4, 'week': 0.2}
    components = {
        'day':  (day_score, target_weights['day']),
        '3d':   (d3_score,  target_weights['3d']),
        'week': (wk_score,  target_weights['week']),
    }
    present = {k: (s, w) for k, (s, w) in components.items() if s is not None}
    if not present:
        return None
    total_w = sum(w for _, w in present.values())
    blended = sum(s * w for s, w in present.values()) / total_w

    def rank(m):
        if not m:
            return [], []
        ranked = sorted(m.items(), key=lambda kv: kv[1], reverse=True)
        return [n for n, _ in ranked[:3]], [n for n, _ in ranked[-3:]]

    day_top3, day_bot3 = rank(day_by_sector)
    d3_top3,  d3_bot3  = rank(day3_by_sector)
    wk_top3,  wk_bot3  = rank(wk_by_sector)

    ref_map = wk_by_sector or day3_by_sector or day_by_sector
    ranked = sorted(ref_map.items(), key=lambda kv: kv[1], reverse=True)
    top3 = [n for n, _ in ranked[:3]]
    bot3 = [n for n, _ in ranked[-3:]]

    confidence = (len(present) / 3.0) ** 0.5

    return {
        'score': max(1, min(10, round(blended))),
        'blended_raw': blended,
        'day_score':  day_score,  'day_spread':  day_sp,
        'day_cyc':    day_cyc,    'day_def':     day_def,
        'day_top3':   day_top3,   'day_bot3':    day_bot3,
        '3d_score':   d3_score,   '3d_spread':   d3_sp,
        '3d_cyc':     d3_cyc,     '3d_def':      d3_def,
        '3d_top3':    d3_top3,    '3d_bot3':     d3_bot3,
        'week_score': wk_score,   'week_spread': wk_sp,
        'week_cyc':   wk_cyc,     'week_def':    wk_def,
        'week_top3':  wk_top3,    'week_bot3':   wk_bot3,
        'top3': top3, 'bot3': bot3,
        'confidence': confidence,
        'timeframes_present': list(present.keys()),
    }


def score_fallback(xly_xlp: str = "Neutral",
                   smh_spy: str = "Neutral",
                   iwm_spy: str = "Neutral",
                   qqq_spy: str = "Neutral") -> ScoreResult:
    """Legacy categorical scoring from dropdowns when no sector data exists."""
    s = 5
    sigs = []
    if xly_xlp == "Risk-On":
        s += 2; sigs.append("XLY/XLP Risk-On")
    elif xly_xlp == "Risk-Off":
        s -= 2; sigs.append("XLY/XLP Risk-Off")
    for v, label in ((smh_spy, "Semis"),
                     (iwm_spy, "Small caps"),
                     (qqq_spy, "Growth")):
        if v == "Leading":
            s += 1; sigs.append(f"{label} leading")
        elif v == "Lagging":
            s -= 1; sigs.append(f"{label} lagging")
    s = max(1, min(10, s))
    interp = (f"Rotation (no sector data yet): "
              f"{', '.join(sigs) if sigs else 'Neutral'}")
    # The legacy fallback historically did not publish a confidence — keep
    # at 0.0 so callers that adopt it treat the signal as low-conviction
    # (preserves prior composite behavior, where the fallback bypassed
    # _component_confidence['rotation']).
    return ScoreResult(score=s, confidence=0.0, interp=interp)


# ── v4.4 Dual Momentum + RRG ──────────────────────────────────────

def _trailing_return(closes: Sequence[float], lookback: int) -> Optional[float]:
    """Return (close[-1] / close[-1-lookback]) - 1, or None if insufficient."""
    if not closes or len(closes) <= lookback:
        return None
    last = float(closes[-1])
    prev = float(closes[-1 - lookback])
    if prev <= 0:
        return None
    return last / prev - 1.0


def _irx_to_period_return(irx_yield_pct: Optional[float],
                          days: int = 63) -> Optional[float]:
    """Convert annualized $IRX yield (%) to an N-day equivalent return."""
    if irx_yield_pct is None or irx_yield_pct <= 0:
        return None
    return (1.0 + irx_yield_pct / 100.0) ** (days / 252.0) - 1.0


def compute_dual_momentum(
    sector_history: Mapping[str, Sequence[float]],
    sp_weights:     Mapping[str, float],
    irx_yield_pct:  Optional[float],
    lookback_days:  int = 63,
) -> dict:
    """Dual-momentum sector rotation score.

    Returns a rich dict so the UI can render the leaderboard:
    ``score, confidence, ranks, returns, crash_active, cyc_avg_rank,
    def_avg_rank, rank_spread, cash_return_pct, irx_yield_pct,
    timeframes_present`` (for backward-compat shim).
    """
    returns: Dict[str, float] = {}
    for etf, closes in (sector_history or {}).items():
        if etf not in sp_weights:
            continue
        r = _trailing_return(closes, lookback_days)
        if r is None:
            continue
        returns[etf] = r

    n_possible = max(len([e for e in sp_weights if e]), 1)
    if not returns:
        return {
            'score': 0, 'confidence': 0.0,
            'ranks': {}, 'returns': {},
            'crash_active': False,
            'cyc_avg_rank': None, 'def_avg_rank': None,
            'rank_spread': None,
            'cash_return_pct': None,
            'irx_yield_pct': irx_yield_pct,
            'top_etf': None, 'top_return': None,
            'interp': "no sector returns available",
        }

    ranked = sorted(returns.items(), key=lambda kv: kv[1], reverse=True)
    ranks = {etf: i + 1 for i, (etf, _) in enumerate(ranked)}
    top_etf, top_ret = ranked[0]

    cash_ret = _irx_to_period_return(irx_yield_pct, lookback_days)
    crash_active = cash_ret is not None and top_ret < cash_ret

    cyc_ranks = [ranks[e] for e in CYCLICAL_ETFS if e in ranks]
    def_ranks = [ranks[e] for e in DEFENSIVE_ETFS if e in ranks]
    cyc_avg = sum(cyc_ranks) / len(cyc_ranks) if cyc_ranks else None
    def_avg = sum(def_ranks) / len(def_ranks) if def_ranks else None
    rank_spread = (
        def_avg - cyc_avg
        if cyc_avg is not None and def_avg is not None else None)

    if crash_active:
        score = 1
        interp = (f"CRASH FILTER active — top {top_etf} {top_ret * 100:+.2f}% "
                  f"< cash {cash_ret * 100:+.2f}% (63d)")
    elif rank_spread is None:
        score = 0
        interp = "insufficient sectors for cyc/def split"
    else:
        score = max(1, min(10, round(5.0 + rank_spread)))
        if rank_spread > 0:
            tone = "cyclicals leading, risk-on rotation"
        elif rank_spread < 0:
            tone = "defensives leading, risk-off rotation"
        else:
            tone = "balanced"
        interp = (f"Cyc rank {cyc_avg:.1f} vs Def rank {def_avg:.1f} "
                  f"(spread {rank_spread:+.1f}) — {tone}")

    irx_present = 1.0 if irx_yield_pct is not None else 0.5
    confidence = ((len(returns) / float(n_possible)) * irx_present) ** 0.5

    return {
        'score': int(score),
        'confidence': float(confidence),
        'ranks': ranks,
        'returns': {k: round(v, 5) for k, v in returns.items()},
        'crash_active': bool(crash_active),
        'cyc_avg_rank': cyc_avg,
        'def_avg_rank': def_avg,
        'rank_spread': rank_spread,
        'cash_return_pct': (cash_ret * 100.0) if cash_ret is not None else None,
        'irx_yield_pct': irx_yield_pct,
        'top_etf': top_etf, 'top_return': top_ret,
        'interp': interp,
    }


def compute_rrg_quadrants(
    sector_history:    Mapping[str, Sequence[float]],
    benchmark_history: Sequence[float],
    rs_window:         int = 50,
    mom_window:        int = 20,
) -> Dict[str, str]:
    """Per-sector Relative Rotation Graph quadrant.

    ``Leading``    — RS > 100 and RS-momentum > 100
    ``Weakening``  — RS > 100 and RS-momentum < 100
    ``Lagging``    — RS < 100 and RS-momentum < 100
    ``Improving``  — RS < 100 and RS-momentum > 100
    """
    out: Dict[str, str] = {}
    if not benchmark_history or len(benchmark_history) < max(rs_window, mom_window) + 1:
        return out
    bench = [float(x) for x in benchmark_history]
    needed = max(rs_window, mom_window) + 1
    for etf, closes in (sector_history or {}).items():
        if not closes or len(closes) < needed:
            continue
        e = [float(x) for x in closes]
        # Align tails (pair the most recent N points).
        n = min(len(e), len(bench))
        if n < needed:
            continue
        e_tail = e[-n:]
        b_tail = bench[-n:]
        rs = [ev / bv for ev, bv in zip(e_tail, b_tail) if bv > 0]
        if len(rs) < needed:
            continue
        rs_today = rs[-1]
        rs_mean = sum(rs[-rs_window:]) / rs_window
        rs_prior = rs[-mom_window - 1]
        if rs_mean <= 0 or rs_prior <= 0:
            continue
        rs_strength = 100.0 * (rs_today / rs_mean)
        rs_momentum = 100.0 * (rs_today / rs_prior)
        if rs_strength >= 100.0 and rs_momentum >= 100.0:
            out[etf] = "Leading"
        elif rs_strength >= 100.0 and rs_momentum < 100.0:
            out[etf] = "Weakening"
        elif rs_strength < 100.0 and rs_momentum < 100.0:
            out[etf] = "Lagging"
        else:
            out[etf] = "Improving"
    return out
