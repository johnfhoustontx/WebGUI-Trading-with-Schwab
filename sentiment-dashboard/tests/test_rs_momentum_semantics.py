"""DOCUMENTS the RS-Momentum axis's present semantics. It does not bless them.

`compute_rs_momentum` subtracts ROC's own rolling mean, so the axis measures the
ACCELERATION of relative strength rather than its rate — which means a sector
steadily beating the benchmark lands in "Weakening" and one steadily trailing it
lands in "Improving". That is a real defect (measured 2026-08-20), left in place
because correcting it re-assigns every quadrant on three pages and moves the
risk-on/risk-off headline with them — a product decision, not a bug fix.

⚠ These tests exist so the behaviour cannot drift SILENTLY, and so that changing
it is a deliberate act with a visible diff. They are named for what they are.
Batch 1 of this audit was a lesson in what happens when a characterization test is
mistaken for a specification: `test_adx_uses_wilder_smoothing` pinned a broken
formula's output for months. Do not read a passing test here as "working".
"""
import numpy as np
import pandas as pd
import pytest

import sector_rotation_assessment as S

_N = 300
_IDX = pd.RangeIndex(_N)
_BENCH = pd.Series(100 + np.arange(_N) * 0.05, index=_IDX)


def _quadrant(sector):
    rr = S.compute_rs_ratio(sector, _BENCH)
    rm = S.compute_rs_momentum(rr)
    return S.classify_quadrant(rr.iloc[-1], rm.iloc[-1]), rr.iloc[-1], rm.iloc[-1]


def test_KNOWN_DEFECT_steady_outperformance_is_labelled_weakening():
    """A sector beating the benchmark at a CONSTANT rate. A reader of the words
    would expect "Leading"; the acceleration axis reports "Weakening"."""
    q, rr, rm = _quadrant(_BENCH * (1 + np.linspace(0, 0.30, _N)))
    assert rr > 100.0            # it IS out-performing...
    assert rm < 100.0            # ...but the momentum axis reads negative
    assert q == "Weakening"


def test_KNOWN_DEFECT_steady_underperformance_is_labelled_improving():
    q, rr, rm = _quadrant(_BENCH * (1 - np.linspace(0, 0.25, _N)))
    assert rr < 100.0 and rm > 100.0
    assert q == "Improving"


def test_only_accelerating_outperformance_reaches_leading():
    q, rr, rm = _quadrant(_BENCH * (1 + 0.30 * np.linspace(0, 1, _N) ** 2))
    assert q == "Leading"


def test_rs_ratio_itself_is_sound():
    """The RS-Ratio axis is fine — it tracks relative strength as advertised. Only
    the momentum axis is at issue, which is why this is a semantics question and
    not a rewrite."""
    _, out_rr, _ = _quadrant(_BENCH * (1 + np.linspace(0, 0.30, _N)))
    _, under_rr, _ = _quadrant(_BENCH * (1 - np.linspace(0, 0.25, _N)))
    assert out_rr > 100.0 > under_rr
