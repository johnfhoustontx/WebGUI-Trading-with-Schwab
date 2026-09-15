"""The scan funnel — the engine counts WHY a symbol produced no trade.

The scanner drops a strike through many gates and, until these counters, recorded
nothing: the page could only say "no signals". These tests pin two invariants that
matter more than any individual count:

  * **the scan's OUTPUT does not change.** Every counter is opt-in (``reasons=`` /
    ``funnel=`` default to ``None``) and with it off the decision must be the one it
    was. Each half of this file proves that by equivalence, not by inspection.
  * **a strike that found no width is attributed to the FURTHEST stage its best
    width reached.** "every width cleared the credit floor but one contract costs
    more than the cap" is actionable; "no width" is not.
"""
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner_engine import WIDTH_STAGES, select_best_width  # noqa: E402

# The fixture builders live with the tests they were written for; reuse them
# rather than growing a second, drifting copy of the same chain shape.
from tests.test_scanner_engine import _chain_at, _opts_from  # noqa: E402


class TestWidthStages:
    """``select_best_width(..., reasons=Counter())`` names the furthest stage."""

    def test_the_stage_order_is_the_search_order(self):
        """The constant is the vocabulary the page will render, so pin it whole."""
        assert WIDTH_STAGES == (
            "long_leg_missing", "long_leg_unpriced", "long_leg_illiquid",
            "no_credit", "sanity_cap", "credit_floor", "edge_floor",
            "over_trade_cap", "no_contracts", "no_positive_ev")

    @pytest.mark.parametrize("marks,kwargs", [
        # A width is found (the E[PnL] race of test_picks_width_with_highest_total_epnl).
        ({510.0: 2.50, 505.0: 1.50, 500.0: 0.50}, {"min_cr_pct": 0.10}),
        # No long strike at any multiple of the increment.
        ({510.0: 2.50}, {"min_cr_pct": 0.10}),
        # Every width fails the regime credit floor.
        ({510.0: 0.50, 509.0: 0.45, 508.0: 0.40}, {"min_cr_pct": 0.10}),
        # The real per-trade budget cannot afford one contract of any width.
        ({510.0: 2.50, 505.0: 1.50}, {"min_cr_pct": 0.10, "max_risk_dollars": 100.0}),
        # The risk cap leaves no room (account_size path).
        ({510.0: 0.60, 509.0: 0.10},
         {"min_cr_pct": 0.05, "account_size": 1000, "max_risk_pct": 0.05}),
    ])
    def test_the_counter_never_changes_the_answer(self, marks, kwargs):
        """Equivalence: the returned tuple is identical with and without a counter.

        This is the guard that matters — the whole feature is counting, and a
        counter that moved a decision would change which trades the app emits.
        """
        opts = _opts_from(510.0, marks)
        without = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                    trade_type="0-DTE", **kwargs)
        opts2 = _opts_from(510.0, marks)
        with_counter = select_best_width(opts2[510.0], opts2, "PCS", strike_increment=1.0,
                                         trade_type="0-DTE", reasons=Counter(), **kwargs)
        if without is None:
            assert with_counter is None
        else:
            w0, lo0, c0, m0 = without
            w1, lo1, c1, m1 = with_counter
            assert (w0, c0, m0) == (w1, c1, m1)
            assert lo0["strike"] == lo1["strike"]

    def test_a_width_over_the_trade_cap_is_named(self):
        """The one width offered clears every credit gate; only the budget refuses.

        width 5, credit 0.992 (the realistic fill), max loss 4.008 -> $400.80 for
        ONE contract, against a $100 budget. cr/w = 0.198 clears both the 0.10
        regime floor and the 0.12 edge floor (|delta| 0.10 + 0.02 margin), so the
        furthest stage reached is ``over_trade_cap`` and not a credit stage.
        """
        opts = _opts_from(510.0, {510.0: 2.50, 505.0: 1.50})
        reasons = Counter()
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10,
                                   max_risk_dollars=100.0, reasons=reasons)
        assert result is None
        assert reasons == Counter({"over_trade_cap": 1})

    def test_a_chain_with_no_long_strike_is_named(self):
        """Only the short strike exists, so every width's long leg is missing."""
        opts = _opts_from(510.0, {510.0: 2.50})
        reasons = Counter()
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10, reasons=reasons)
        assert result is None
        assert reasons == Counter({"long_leg_missing": 1})

    def test_an_illiquid_long_leg_is_named(self):
        """The only long strike fails the liquidity gate (oi 5, below 20)."""
        opts = _opts_from(510.0, {510.0: 2.00, 505.0: 0.50})
        opts[505.0]["oi"] = 5
        reasons = Counter()
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10, reasons=reasons)
        assert result is None
        assert reasons == Counter({"long_leg_illiquid": 1})

    def test_the_credit_floor_is_named(self):
        """Every width pays ~5% of its width against a 10% regime floor."""
        opts = _opts_from(510.0, {510.0: 0.50, 509.0: 0.45, 508.0: 0.40})
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 reasons=reasons) is None
        assert reasons == Counter({"credit_floor": 1})

    def test_the_edge_floor_is_named(self):
        """cr/w ~0.262 at short delta -0.25 clears the 0.10 regime floor and the
        bare breakeven, and fails only the 0.02 edge margin."""
        opts = _opts_from(510.0, {510.0: 0.67, 509.0: 0.40})
        opts[510.0]["delta"] = -0.25
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 reasons=reasons) is None
        assert reasons == Counter({"edge_floor": 1})

    def test_the_risk_cap_leaving_no_room_is_named(self):
        """A $1,000 account at 5% affords $50 against a $51 max loss."""
        opts = _opts_from(510.0, {510.0: 0.60, 509.0: 0.10})
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.05,
                                 account_size=1000, max_risk_pct=0.05,
                                 reasons=reasons) is None
        assert reasons == Counter({"no_contracts": 1})

    def test_a_found_width_records_nothing(self):
        """The counter names failures only — a strike that traded is not a reason."""
        opts = _opts_from(510.0, {510.0: 2.50, 505.0: 1.50, 500.0: 0.50})
        reasons = Counter()
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10, reasons=reasons)
        assert result is not None
        assert reasons == Counter()

    def test_the_furthest_stage_wins_not_the_last_one(self):
        """Two widths: the near one has no long leg at all, the far one clears
        every credit gate and dies on the budget. The report must be the FURTHEST
        stage reached across all widths, so a later width failing EARLY cannot
        mask the real answer.

        Increment 5 offers 505 (w=5, priced, over the cap) and 500 (w=10, absent).
        The 500 leg is examined last, so a "last continue wins" rule would say
        ``long_leg_missing``.
        """
        opts = _opts_from(510.0, {510.0: 2.50, 505.0: 1.50})
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=5.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 max_risk_dollars=100.0, reasons=reasons) is None
        assert reasons == Counter({"over_trade_cap": 1})
