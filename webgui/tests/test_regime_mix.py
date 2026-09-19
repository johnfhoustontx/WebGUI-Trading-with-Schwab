"""Tests for the ranked Market Regime membership panel (pages/regime_mix.py)."""
import re

import pytest

from pages import regime_mix as rm


def _pt(ts, **mem):
    full = {k: 0.0 for k in rm.REGIME_ORDER}
    full.update(mem)
    return {"ts": ts, "memberships": full, "confidence": 0.5, "label": "choppy"}


def _session():
    """Three points: Balanced bleeds, Stressed wakes from zero, Whipsaw takes
    the lead — the shape of a real session (2026-08-14)."""
    return [
        _pt(1000, mean_reversion=0.37, trending=0.26, choppy=0.367, crisis=0.0),
        _pt(1300, mean_reversion=0.33, trending=0.27, choppy=0.378, crisis=0.02),
        _pt(1600, mean_reversion=0.286, trending=0.277, choppy=0.388, crisis=0.049),
    ]


# --------------------------------------------------------------- sanitation
@pytest.mark.parametrize("junk", [None, "x", {}, [], float("nan"),
                                  float("inf"), float("-inf")])
def test_safe_frac_rejects_junk(junk):
    assert rm._safe_frac(junk) == 0.0


def test_safe_frac_clamps_to_unit_interval():
    assert rm._safe_frac(1.4) == 1.0
    assert rm._safe_frac(-0.2) == 0.0
    assert rm._safe_frac(0.25) == 0.25


# ------------------------------------------------------------------ ranking
def test_rows_are_ranked_by_current_membership():
    rows = rm.rank_rows(_session())
    assert [r["key"] for r in rows[:3]] == ["choppy", "mean_reversion", "trending"]
    assert rows[0]["now"] == pytest.approx(0.388)


def test_ties_break_on_fixed_order_so_repaints_cannot_jitter():
    """Identical data must always produce identical row order."""
    tied = [_pt(1000, mean_reversion=0.25, trending=0.25, breakout=0.25,
                choppy=0.25, crisis=0.0)]
    first = [r["key"] for r in rm.rank_rows(tied)]
    assert first == [k for k in rm.REGIME_ORDER if k != "crisis"] + ["crisis"]
    assert first == [r["key"] for r in rm.rank_rows(tied)]


def test_change_is_measured_from_the_session_open():
    rows = {r["key"]: r for r in rm.rank_rows(_session())}
    assert rows["crisis"]["change"] == pytest.approx(0.049)
    assert rows["mean_reversion"]["change"] == pytest.approx(-0.084)


def test_a_single_point_has_no_change_yet():
    rows = rm.rank_rows([_pt(1000, choppy=0.4)])
    assert all(r["change"] == 0.0 for r in rows)


def test_session_split_measures_change_from_todays_open_not_yesterdays():
    """The published history is one session, but a day boundary must not make
    the change column compare across the gap."""
    old = _pt(1000, mean_reversion=0.90)
    today = [_pt(1000 + rm.SESSION_GAP_SEC + 60, mean_reversion=0.30),
             _pt(1000 + rm.SESSION_GAP_SEC + 360, mean_reversion=0.35)]
    rows = {r["key"]: r for r in rm.rank_rows([old] + today)}
    assert rows["mean_reversion"]["change"] == pytest.approx(0.05)
    assert len(rm.session_points([old] + today)) == 2


# ------------------------------------------------------------- lead margin
def test_lead_margin_reports_now_and_the_tightest_of_the_session():
    key, now, tightest = rm.lead_margin(_session())
    assert key == "choppy"
    assert now == pytest.approx(0.388 - 0.286)
    # The open was the coin-flip: 0.370 vs 0.367.
    assert tightest == pytest.approx(0.003)


def test_lead_margin_is_none_without_history():
    assert rm.lead_margin([]) == (None, None, None)


def test_colour_follows_the_regime_not_the_rank():
    """Filtering or reordering must never repaint an entity a new colour."""
    rows = {r["key"]: r for r in rm.rank_rows(_session())}
    for key, row in rows.items():
        assert row["color"] == rm.REGIME_COLORS[key]


# ── the Regime word's hover ──────────────────────────────────────────────────
# Every word the service can print: the five displays, the direction
# adornments, and "Unclear". The cross-tier test in shared/tests reads the
# service's own tables; this list is the page-side statement of the same set.
_REGIME_WORDS = ("Balanced", "Trending", "Rallying", "Firming", "Retreating",
                 "Softening", "Breakout", "Breakdown", "Whipsaw", "Stressed",
                 "Unclear")


def test_every_regime_word_has_a_picture():
    for word in _REGIME_WORDS:
        assert rm.regime_picture(word).strip(), word


def test_a_non_word_has_no_picture():
    for word in ("", None, "wat", "—", "balanced"):   # keys are the DISPLAY words
        assert rm.regime_picture(word) == "", word


def test_the_directional_pictures_name_their_direction():
    assert "higher" in rm.regime_picture("Rallying")
    assert "climb" in rm.regime_picture("Firming")
    assert "lower" in rm.regime_picture("Retreating")
    assert "decline" in rm.regime_picture("Softening")
    assert "downside" in rm.regime_picture("Breakdown")
