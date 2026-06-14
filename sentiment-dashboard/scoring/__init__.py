"""Sentiment scoring package. UI must import from here, not duplicate constants.

This package is the single source of truth for sentiment component weights
and the contract every scoring module honors (see ``types.ScoreResult``).
"""

WEIGHTS = {
    "vix_complex":  0.20,   # merged: term 50% + vix1d 33% + slope 17% (sub-weights below)
    "put_call":     0.20,   # v4.3: cap-weighted sector P/C (was 15% market $CPCE)
    "breadth":      0.20,
    "rotation":     0.15,
    "sector_perf":  0.25,   # cap-weighted across 11 GICS sectors
}                            # credit_pulse removed v4.3
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, \
    f"WEIGHTS must sum to 1.0, got {sum(WEIGHTS.values())}"

# Internal sub-weights for VIX Complex (15/10/5 normalized over 30).
VIX_SUB_WEIGHTS = {"term": 0.50, "vix1d": 0.33, "slope": 0.17}
