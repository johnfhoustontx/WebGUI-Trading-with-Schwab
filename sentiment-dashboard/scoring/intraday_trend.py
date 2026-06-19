"""Intraday directional Market Trend score (0-100, 50 = neutral).

Pure functions — scalar in, scalar out (no pandas, no tk, no I/O). The sentiment
service extracts scalars from proxy data and calls these; the webgui renders the
result. Distinct from the 1-10 *contrarian* composite: this is *directional*
(100 = max bull, 0 = max bear). Reuses the confidence-weighted blend idiom of
scoring/composite.py and the state vocabulary of scoring/trend_regime.py.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class TrendSub:
    score: float          # 0-100 directional
    confidence: float     # [0.0, 1.0]
    interp: str = ""


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))
