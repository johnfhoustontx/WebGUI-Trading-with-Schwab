"""``tools/sweep_strategy_gates.py`` -- the measurement behind the Strategy
Finder's new gate profiles (``strategy_scoring._TYPE_PROFILE``).

The script is a re-runnable sweep, not a test; what is pinned here is that it
still REACHES all sixteen structures the four new builders emit, that the design
doc's two central cuts -- the short straddle and the covered call grade Weak, and
both for PoP -- still reproduce through the real scorers, and that its scoring
mirrors production's (``score_all`` with a per-candidate ``daily_move``). If any
stops holding, the ``_TYPE_PROFILE`` comments and the design doc's Scoring table
are stale.
"""
import importlib.util
import math
from pathlib import Path

# Every type the four new builders emit (straddles/strangles, butterflies/condors,
# calendars/diagonals, share structures). All sixteen are built at spot 100,
# IV 0.28, front 30 DTE, a 2.5 ladder - so the set is pinned exactly, not as a
# subset: a builder that silently stops emitting one fails here.
NEW_TYPES = {
    "LONG_STRADDLE", "SHORT_STRADDLE", "LONG_STRANGLE", "SHORT_STRANGLE",
    "BUTTERFLY_CALL", "BUTTERFLY_PUT", "IRON_BUTTERFLY", "CONDOR_CALL", "CONDOR_PUT",
    "CALENDAR_CALL", "CALENDAR_PUT", "DIAGONAL_CALL", "DIAGONAL_PUT",
    "COVERED_CALL", "PROTECTIVE_PUT", "COLLAR",
}


def _tool():
    p = Path(__file__).resolve().parents[1] / "sweep_strategy_gates.py"
    spec = importlib.util.spec_from_file_location("sweep_strategy_gates", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sweep_reaches_all_sixteen_new_structures_and_reproduces_the_two_cuts():
    rows = list(_tool().rows(100.0, 0.28, 30, 2.5))
    assert {r["type"] for r in rows} == NEW_TYPES
    by = {r["type"]: r for r in rows}
    assert by["SHORT_STRADDLE"]["grade"] == "Weak"
    assert by["COVERED_CALL"]["grade"] == "Weak"
    # WHY they cut: both fail NAKED's PoP bar - the reason the _TYPE_PROFILE
    # comment and the design doc give. Failing some other bar would leave the
    # grade green while the recorded rationale went stale.
    assert "PoP" in by["SHORT_STRADDLE"]["grade_reason"]
    assert "PoP" in by["COVERED_CALL"]["grade_reason"]


def test_sweep_scores_like_production_with_a_per_candidate_daily_move(monkeypatch):
    """The sweep hands ``score_all`` the DAILY move, as ``compute.swing_scan``
    does, so each candidate is judged against the move to its own expiry."""
    tool = _tool()
    real = tool.sc.score_all
    seen = []

    def _spy(signals, view, atm_iv, em_1sd, market_state=None, daily_move=None):
        seen.append(daily_move)
        return real(signals, view, atm_iv, em_1sd, market_state=market_state,
                    daily_move=daily_move)

    monkeypatch.setattr(tool.sc, "score_all", _spy)
    rows = list(tool.rows(100.0, 0.28, 30, 2.5))
    assert rows and len(seen) == len(rows)
    assert all(d == 100.0 * 0.28 * math.sqrt(1 / 365.0) for d in seen)
