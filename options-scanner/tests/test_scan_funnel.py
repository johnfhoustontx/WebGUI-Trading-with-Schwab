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
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scanner_engine  # noqa: E402
from scanner_engine import (DEFAULT_MAX_RISK_DOLLARS, WIDTH_STAGES,  # noqa: E402
                            _STAGE, screen_spreads, select_best_width)

# The fixture builders live with the tests they were written for; reuse them
# rather than growing a second, drifting copy of the same chain shape.
from tests.test_scanner_engine import _chain_at, _opts_from  # noqa: E402


class TestWidthStages:
    """``select_best_width(..., reasons=Counter())`` names the furthest stage."""

    def test_the_stage_order_is_the_search_order(self):
        """The constant is the vocabulary the page will render, so pin it whole."""
        assert WIDTH_STAGES == (
            "increment_over_cap",
            "long_leg_missing", "long_leg_unpriced", "long_leg_illiquid",
            "no_credit", "sanity_cap", "credit_floor", "edge_floor",
            "over_trade_cap", "no_contracts", "no_positive_ev")
        # The names are the page's vocabulary AND the rank: a stage's position
        # is its severity, so _STAGE must stay in step with the tuple.
        assert _STAGE == {n: i for i, n in enumerate(WIDTH_STAGES)}

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


#############################################
# screen_spreads — the per-strike funnel
#############################################

def _spreads_chain(spot=500.0, dte=7, exp=None):
    """One expiration per side on a $1 ladder, dated relative to TODAY.

    The date has to be live: ``check_earnings_conflict`` measures against
    ``datetime.now``, so a hard-coded expiration would drift out of every window
    and the earnings test below would pass by taking an early-out. This repo has
    been bitten by exactly that — three of five ``TestEarningsAvoidance`` cases
    were green for the wrong reason once their fixtures aged into the past.
    """
    if exp is None:
        exp = (date.today() + timedelta(days=dte)).isoformat()
    return _chain_at(spot, exp, dte), exp


def _screen(chain, funnel=None, **over):
    """The shared call. The delta band is wide on purpose: it has to admit
    strikes that then die at the delta ceiling, the expected-move window and the
    width search, or the consistency invariant would be checked over an empty
    funnel and would prove nothing."""
    kw = dict(dte_min=0, dte_max=10, put_d_min=-0.30, put_d_max=-0.05,
              call_d_min=0.05, call_d_max=0.30, min_cr_pct=0.10,
              trade_type="SWING", spot=None, daily_expected_move=None,
              earnings_date=None, widths=None,
              max_risk_dollars=DEFAULT_MAX_RISK_DOLLARS)
    kw.update(over)
    return screen_spreads(
        chain, "TEST", kw["dte_min"], kw["dte_max"], kw["put_d_min"],
        kw["put_d_max"], kw["call_d_min"], kw["call_d_max"], kw["min_cr_pct"],
        kw["trade_type"], spot=kw["spot"],
        daily_expected_move=kw["daily_expected_move"],
        earnings_date=kw["earnings_date"], widths=kw["widths"],
        max_risk_dollars=kw["max_risk_dollars"], funnel=funnel)


def _comparable(signals):
    """Strip the wall-clock stamp — it is written from ``datetime.now`` per
    signal, so two otherwise identical passes never compare equal with it in."""
    return [{k: v for k, v in s.items() if k != "timestamp"} for s in signals]


class TestScreenSpreadsFunnel:
    @pytest.mark.parametrize("over", [
        {},                                             # no expected-move window
        {"spot": 500.0, "daily_expected_move": 5.0},    # EM window active
        {"spot": 500.0, "daily_expected_move": 0.5},    # a tight EM window
        {"max_risk_dollars": 50.0},                     # the trade cap refuses everything
        {"min_cr_pct": 0.90},                           # the credit floor refuses everything
        {"dte_min": 20, "dte_max": 30},                 # nothing in the DTE window
        {"widths": [5.0]},                              # the explicit-widths path
        {"earnings_date": "__soon__"},                  # the whole expiration is dropped
    ])
    def test_the_funnel_never_changes_the_signals(self, over):
        """Equivalence: the returned list is identical with and without ``funnel=``.

        The counters are the whole feature, so the thing worth pinning is that
        they are ONLY counters — a funnel that moved a gate would change which
        trades the app emits, silently and everywhere.
        """
        over = dict(over)
        if over.get("earnings_date") == "__soon__":
            over["earnings_date"] = (date.today() + timedelta(days=2)).isoformat()
        chain, _ = _spreads_chain()
        without = _screen(chain, **over)
        with_funnel = _screen(chain, funnel={}, **over)
        assert _comparable(without) == _comparable(with_funnel)

    @pytest.mark.parametrize("over", [
        {},
        {"spot": 500.0, "daily_expected_move": 5.0},
        {"spot": 500.0, "daily_expected_move": 0.5},
        {"max_risk_dollars": 50.0},
        {"min_cr_pct": 0.90},
    ])
    def test_every_strike_that_passed_the_delta_band_is_accounted_for(self, over):
        """The funnel is a PARTITION of the strikes that entered the search.

        Every strike admitted by the delta band leaves through exactly one door:
        an unpriced mark, the delta ceiling, the expected-move window, the short
        leg's liquidity gate, a width found, or a width search that named its
        furthest stage. If these ever stop adding up, the page is telling the
        user a story with a hole in it.
        """
        chain, _ = _spreads_chain()
        funnel = {}
        signals = _screen(chain, funnel=funnel, **over)
        accounted = (funnel["mark_fail"] + funnel["delta_ceiling"]
                     + funnel["em_fail"] + funnel["liq_fail_short"]
                     + funnel["width_found"]
                     + sum(funnel["width_reasons"].values()))
        assert funnel["delta_pass"] == accounted
        # Vacuity guard: an all-zero funnel satisfies the equation trivially.
        assert funnel["delta_pass"] > 0
        assert funnel["width_found"] == len(signals)

    def test_the_delta_ceiling_really_fires_on_this_fixture(self):
        """Pins the vacuity of the partition test above: the band reaches past
        MAX_ENTRY_SHORT_DELTA (0.27), so that door is genuinely used."""
        chain, _ = _spreads_chain()
        funnel = {}
        _screen(chain, funnel=funnel)
        assert funnel["delta_ceiling"] > 0

    def test_a_strike_outside_the_delta_band_is_counted_as_a_reject(self):
        """delta_reject + delta_pass is every strike the search looked at, and the
        ladder is far wider than any band this scan uses."""
        chain, _ = _spreads_chain()
        funnel = {}
        _screen(chain, funnel=funnel)
        assert funnel["delta_reject"] > 0
        assert funnel["delta_pass"] > 0

    def test_a_narrower_band_rejects_more_and_passes_fewer(self):
        """Pins delta_reject/delta_pass to the BAND rather than to a constant."""
        chain, _ = _spreads_chain()
        wide, narrow = {}, {}
        _screen(chain, funnel=wide, put_d_min=-0.30, put_d_max=-0.05,
                call_d_min=0.05, call_d_max=0.30)
        _screen(chain, funnel=narrow, put_d_min=-0.12, put_d_max=-0.08,
                call_d_min=0.08, call_d_max=0.12)
        assert narrow["delta_pass"] < wide["delta_pass"]
        assert narrow["delta_reject"] > wide["delta_reject"]

    def test_expiration_sides_in_window_counts_the_dte_range(self):
        """⚠ SIDES, not dates — the pass walks the put map then the call map, so
        one Friday listed in both counts twice. The name says so because a page
        reading "2 expirations" over a single Friday would simply be wrong.
        Nothing in the DTE range means nothing counted, and no strike examined."""
        chain, _ = _spreads_chain(dte=7)
        inside, outside = {}, {}
        _screen(chain, funnel=inside, dte_min=0, dte_max=10)
        _screen(chain, funnel=outside, dte_min=20, dte_max=30)
        assert inside["expiration_sides_in_window"] == 2
        assert outside["expiration_sides_in_window"] == 0
        assert outside["delta_pass"] == 0
        assert outside["delta_reject"] == 0

    def test_an_earnings_skip_is_counted_and_leaves_no_strikes(self):
        """A SWING expiration straddling a report is dropped whole: counted as
        in-window (the gate is a later door, not the DTE range) and then as an
        earnings skip, with no strike below it examined."""
        chain, _ = _spreads_chain(dte=7)
        funnel = {}
        signals = _screen(chain, funnel=funnel,
                          earnings_date=(date.today() + timedelta(days=2)).isoformat())
        assert signals == []
        assert funnel["expiration_sides_in_window"] == 2
        assert funnel["expiration_sides_skipped_earnings"] == 2
        assert funnel["delta_pass"] == 0
        assert funnel["delta_reject"] == 0

    def test_no_earnings_date_skips_nothing(self):
        """The counter follows the GATE, not merely the trade type."""
        chain, _ = _spreads_chain(dte=7)
        funnel = {}
        _screen(chain, funnel=funnel)
        assert funnel["expiration_sides_skipped_earnings"] == 0
        assert funnel["delta_pass"] > 0

    def test_the_trade_cap_reaches_the_width_reasons(self):
        """A $50 per-trade budget cannot afford one contract of any width on this
        ladder, so every strike that reached the width search is named there — and
        named as the budget, not as a credit gate."""
        chain, _ = _spreads_chain()
        funnel = {}
        signals = _screen(chain, funnel=funnel, max_risk_dollars=50.0)
        assert signals == []
        assert funnel["width_found"] == 0
        assert funnel["width_reasons"]["over_trade_cap"] > 0
        assert funnel["width_reasons"].most_common(1)[0][0] == "over_trade_cap"

    def test_a_caller_supplied_width_reasons_counter_is_reused(self):
        """``funnel.setdefault`` — a caller accumulating one funnel across symbols
        must not have its counter replaced on the second call."""
        mine = Counter({"edge_floor": 3})
        funnel = {"width_reasons": mine}
        chain, _ = _spreads_chain()
        _screen(chain, funnel=funnel, max_risk_dollars=50.0)
        assert funnel["width_reasons"] is mine
        assert mine["edge_floor"] == 3          # untouched
        assert mine["over_trade_cap"] > 0       # added to

    def test_the_explicit_widths_path_leaves_the_width_counters_alone(self):
        """``widths=[...]`` never runs the width search, so it can contribute no
        width reason and find no width — while the strike-level counters ahead of
        the branch still fill."""
        chain, _ = _spreads_chain()
        funnel = {}
        signals = _screen(chain, funnel=funnel, widths=[5.0])
        assert signals                           # vacuity: this path really produced signals
        assert funnel["width_reasons"] == Counter()
        assert funnel["width_found"] == 0
        assert funnel["delta_pass"] > 0
        assert funnel["expiration_sides_in_window"] == 2

    def _break_puts(self, chain, **fields):
        """Apply `fields` to every put contract in the chain's one expiration."""
        key = next(iter(chain["putExpDateMap"]))
        for leg in chain["putExpDateMap"][key].values():
            leg[0].update(fields)
        return chain

    def test_an_unpriced_short_leg_is_counted_and_stays_in_the_partition(self):
        """``mark_fail`` and ``liq_fail_short`` are both ZERO on the clean
        fixture, so the partition test above cannot tell whether those two doors
        are wired at all. Break the chain deliberately, check the counter fills,
        and check the equation still balances once it does."""
        chain, _ = _spreads_chain()
        # Every price field screen_spreads falls back through, so the short leg
        # really has no usable mark rather than a mid or a prior close.
        self._break_puts(chain, mark=0, bid=0, ask=0, close=0,
                         theoreticalOptionValue=0)
        funnel = {}
        _screen(chain, funnel=funnel)
        assert funnel["mark_fail"] > 0
        accounted = (funnel["mark_fail"] + funnel["delta_ceiling"]
                     + funnel["em_fail"] + funnel["liq_fail_short"]
                     + funnel["width_found"]
                     + sum(funnel["width_reasons"].values()))
        assert funnel["delta_pass"] == accounted

    def test_an_illiquid_short_leg_is_counted_and_stays_in_the_partition(self):
        """The other door the clean fixture never opens."""
        chain, _ = _spreads_chain()
        self._break_puts(chain, openInterest=0, totalVolume=0)
        funnel = {}
        _screen(chain, funnel=funnel)
        assert funnel["liq_fail_short"] > 0
        accounted = (funnel["mark_fail"] + funnel["delta_ceiling"]
                     + funnel["em_fail"] + funnel["liq_fail_short"]
                     + funnel["width_found"]
                     + sum(funnel["width_reasons"].values()))
        assert funnel["delta_pass"] == accounted

    @pytest.mark.parametrize("broken", [
        {"mark": 0, "bid": 0, "ask": 0, "close": 0, "theoreticalOptionValue": 0},
        {"openInterest": 0, "totalVolume": 0},
    ])
    def test_a_broken_chain_still_screens_identically_with_a_funnel(self, broken):
        """Equivalence again, over the two doors the clean fixture never opens."""
        a, _ = _spreads_chain()
        b, _ = _spreads_chain()
        self._break_puts(a, **broken)
        self._break_puts(b, **broken)
        assert _comparable(_screen(a)) == _comparable(_screen(b, funnel={}))

    def _no_spreads_line(self, caplog, **over):
        caplog.clear()
        chain, _ = _spreads_chain()
        with caplog.at_level("INFO", logger="scanner"):
            assert _screen(chain, **over) == []
        lines = [r.getMessage() for r in caplog.records if "NO SPREADS" in r.getMessage()]
        assert len(lines) == 1
        return lines[0]

    def test_the_auto_width_log_names_the_width_stage(self, caplog):
        """The auto-width path never touches ``liq_long`` / ``credit``, so printing
        them meant printing two zeroes on every scan that took this branch —
        indistinguishable from "nothing failed there". The width search's own
        stages are the answer, and they must appear WITHOUT a funnel being asked
        for: the line has to be useful on a scan nobody instrumented."""
        line = self._no_spreads_line(caplog, min_cr_pct=0.90)
        assert "credit_floor=14" in line
        assert "liq_long" not in line
        assert "credit=" not in line

    def test_the_auto_width_log_distinguishes_the_trade_cap_from_the_credit_floor(self, caplog):
        """The same fixture, refused by a different gate, must read differently —
        otherwise the line is decoration."""
        capped = self._no_spreads_line(caplog, max_risk_dollars=50.0)
        floored = self._no_spreads_line(caplog, min_cr_pct=0.90)
        assert "over_trade_cap=14" in capped
        assert "credit_floor" not in capped
        assert "credit_floor=14" in floored
        assert "over_trade_cap" not in floored

    def test_the_explicit_widths_log_keeps_its_own_counters(self, caplog):
        """That branch really does fill liq_long / credit, so its line is unchanged."""
        line = self._no_spreads_line(caplog, widths=[5.0], min_cr_pct=0.90)
        assert "credit=14" in line
        assert "liq_long=" in line
        assert "widths:" not in line

    def test_a_reused_funnel_accumulates_every_key_the_same_way(self):
        """``width_reasons`` is reached through ``setdefault``, so it accumulates
        across calls — and every scalar beside it therefore has to accumulate too.
        A funnel whose Counter summed two symbols while its scalars reported only
        the second would be worse than no funnel: the partition equation would
        silently stop holding, and the page would render a tally that does not add
        up without anything failing.
        """
        chain, _ = _spreads_chain()
        once, twice = {}, {}
        _screen(chain, funnel=once)
        _screen(chain, funnel=twice)
        _screen(chain, funnel=twice)
        for key in ("expiration_sides_in_window", "expiration_sides_skipped_earnings",
                    "delta_reject", "delta_pass", "mark_fail", "delta_ceiling",
                    "em_fail", "liq_fail_short", "width_found"):
            assert twice[key] == 2 * once[key], key
        assert twice["width_reasons"] == once["width_reasons"] + once["width_reasons"]

    def test_the_partition_survives_a_reused_funnel(self):
        """The same equation, over two symbols' worth of counts in one funnel."""
        a, _ = _spreads_chain(spot=500.0)
        b, _ = _spreads_chain(spot=430.0)
        funnel = {}
        n = len(_screen(a, funnel=funnel, max_risk_dollars=50.0))
        n += len(_screen(b, funnel=funnel))
        accounted = (funnel["mark_fail"] + funnel["delta_ceiling"]
                     + funnel["em_fail"] + funnel["liq_fail_short"]
                     + funnel["width_found"]
                     + sum(funnel["width_reasons"].values()))
        assert funnel["delta_pass"] == accounted
        assert funnel["width_found"] == n
        # Vacuity: the two calls really did land in different buckets.
        assert funnel["width_found"] > 0
        assert funnel["width_reasons"]["over_trade_cap"] > 0


class TestTheFourUntestedStages:
    """``long_leg_unpriced`` · ``no_credit`` · ``sanity_cap`` · ``no_positive_ev``
    had no test at all, which is exactly what makes a renumbering safe to get
    wrong: swapping two literals keeps a green suite. One test each, in the shape
    of the per-gate tests above."""

    def test_an_unpriced_long_leg_is_named(self):
        """The only long strike quotes a zero mark. It is examined and rejected,
        so it must outrank the further widths whose strikes are simply absent."""
        opts = _opts_from(510.0, {510.0: 2.50, 509.0: 0.0})
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 reasons=reasons) is None
        assert reasons == Counter({"long_leg_unpriced": 1})

    def test_a_long_leg_dearer_than_the_short_is_named_no_credit(self):
        """Buying the long for more than the short sells for is not a credit
        spread. Whether ``_entry_credit`` answers None (untradeable) or a negative
        number, both land in the same bucket — which is the point of the bucket."""
        opts = _opts_from(510.0, {510.0: 1.00, 509.0: 3.00})
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 reasons=reasons) is None
        assert reasons == Counter({"no_credit": 1})

    def test_a_credit_at_or_above_the_width_is_named_no_credit_not_sanity_cap(self):
        """``ml <= 0`` is checked BEFORE the 0.95 cap, so a credit that exceeds
        its own width lands in no_credit. Pins the boundary between the two
        stages, which is the one place the ordering could silently flip."""
        opts = _opts_from(510.0, {510.0: 2.50, 509.0: 1.40})
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 reasons=reasons) is None
        assert reasons == Counter({"no_credit": 1})

    def test_an_absurd_credit_below_the_width_is_named_sanity_cap(self):
        """credit ~0.972 on a $1 width: above the 0.95 cap (bad market data) but
        still leaving a positive max loss, so it reaches the cap rather than
        stopping at no_credit."""
        opts = _opts_from(510.0, {510.0: 2.50, 509.0: 1.52})
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 reasons=reasons) is None
        assert reasons == Counter({"sanity_cap": 1})

    def test_no_positive_ev_is_unreachable_while_the_edge_margin_is_positive(self):
        """⚠ A fact worth writing down: for a PCS, E[PnL] per contract reduces to
        ``credit - width*|delta|``, so ``e_pnl <= 0`` is exactly ``cr/w <= |d|``
        — and the edge floor one line above already demands ``cr/w >= |d| +
        EDGE_MARGIN``. With EDGE_MARGIN at 0.02 the EV branch is therefore DEAD
        CODE, and no fixture can reach it.

        That is why this test relaxes the floor instead of pretending otherwise:
        it pins the attribution for the day the margin is lowered, and records
        that the stage is currently unreachable rather than untested.
        """
        opts = _opts_from(510.0, {510.0: 2.50, 505.0: 1.50})
        opts[510.0]["delta"] = -0.25          # cr/w is ~0.198, so EV is negative
        reasons = Counter()
        with mock.patch.object(scanner_engine, "EDGE_MARGIN", -0.10):
            assert select_best_width(opts[510.0], opts, "PCS", strike_increment=5.0,
                                     trade_type="0-DTE", min_cr_pct=0.10,
                                     reasons=reasons) is None
        assert reasons == Counter({"no_positive_ev": 1})

    def test_the_edge_floor_still_catches_that_fixture_at_the_real_margin(self):
        """The control for the test above: at the shipped EDGE_MARGIN the SAME
        fixture stops one stage earlier. Without this, the patch could be hiding
        a fixture that never reached the edge floor in the first place."""
        opts = _opts_from(510.0, {510.0: 2.50, 505.0: 1.50})
        opts[510.0]["delta"] = -0.25
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=5.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 reasons=reasons) is None
        assert reasons == Counter({"edge_floor": 1})


class TestIncrementOverCap:
    def test_an_increment_wider_than_the_cap_is_named_honestly(self):
        """A $250 strike increment means the NARROWEST width is already past
        MAX_WIDTH_DOLLARS, so the loop breaks having constructed no width at all.
        Reporting ``long_leg_missing`` there sent the reader hunting for absent
        strikes that are all present; the honest answer names the increment."""
        opts = _opts_from(510.0, {510.0: 2.50, 260.0: 1.50})
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=250.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 reasons=reasons) is None
        assert reasons == Counter({"increment_over_cap": 1})

    def test_a_workable_increment_never_reports_the_cap(self):
        """The control: same shape, an increment the cap admits, so the real
        first stage is reached instead."""
        opts = _opts_from(510.0, {510.0: 2.50})
        reasons = Counter()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 reasons=reasons) is None
        assert reasons == Counter({"long_leg_missing": 1})


class TestReasonsAcceptsAPlainMapping:
    """``reasons`` is a public parameter and the funnel exists to be PUBLISHED,
    so a ``width_reasons`` that has been through ``json.loads`` comes back a
    plain dict. ``Counter.__missing__`` is the only thing that made ``+= 1``
    work on an absent key, so a plain dict used to raise KeyError straight out
    of the scan."""

    @pytest.mark.parametrize("empty", [dict, Counter, lambda: defaultdict(int)])
    def test_every_mapping_shape_counts_without_raising(self, empty):
        opts = _opts_from(510.0, {510.0: 2.50})
        reasons = empty()
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10,
                                 reasons=reasons) is None
        assert reasons["long_leg_missing"] == 1

    def test_a_plain_dict_accumulates_rather_than_resetting(self):
        opts = _opts_from(510.0, {510.0: 2.50})
        reasons = {"long_leg_missing": 4}
        for _ in range(2):
            select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                              trade_type="0-DTE", min_cr_pct=0.10, reasons=reasons)
        assert reasons == {"long_leg_missing": 6}

    def test_a_json_round_tripped_funnel_does_not_abort_the_scan(self):
        """End to end: publish the funnel, read it back, keep scanning."""
        chain, _ = _spreads_chain()
        funnel = {}
        _screen(chain, funnel=funnel, max_risk_dollars=50.0)
        revived = json.loads(json.dumps(funnel))
        assert isinstance(revived["width_reasons"], dict)
        assert not isinstance(revived["width_reasons"], Counter)
        _screen(chain, funnel=revived, max_risk_dollars=50.0)   # must not raise
        assert revived["width_reasons"]["over_trade_cap"] == 28
        assert revived["delta_pass"] == 32

    def test_a_revived_funnel_is_coerced_so_the_counter_api_is_available(self):
        """Coerced at the setdefault rather than trusted: everything downstream
        reads ``most_common`` off this, and a plain dict has none."""
        chain, _ = _spreads_chain()
        funnel = {"width_reasons": {"edge_floor": 2}}
        _screen(chain, funnel=funnel, max_risk_dollars=50.0)
        assert isinstance(funnel["width_reasons"], Counter)
        assert funnel["width_reasons"]["edge_floor"] == 2      # preserved
        assert funnel["width_reasons"]["over_trade_cap"] == 14


class TestTheLogLineStaysPerCall:
    """⚠ The funnel ACCUMULATES and the log line describes ONE call. Mixing the
    two is the same defect the scalars were fixed for, one layer down: a
    per-call ``no_width`` printed beside the funnel's running Counter made the
    second symbol's line read ``no_width=14 | widths: over_trade_cap=28``, which
    is self-contradictory on its face."""

    def _lines(self, caplog, funnel, calls=2, **over):
        caplog.clear()
        chain, _ = _spreads_chain()
        with caplog.at_level("INFO", logger="scanner"):
            for _ in range(calls):
                assert _screen(chain, funnel=funnel, **over) == []
        return [r.getMessage() for r in caplog.records if "NO SPREADS" in r.getMessage()]

    def test_a_reused_funnel_does_not_inflate_the_log_line(self, caplog):
        funnel = {}
        lines = self._lines(caplog, funnel, **{"max_risk_dollars": 50.0})
        assert len(lines) == 2
        for line in lines:
            assert "no_width=14" in line
            assert "over_trade_cap=14" in line       # this call, not the running total
            assert "over_trade_cap=28" not in line
        # ...while the funnel itself still accumulates, which is its job.
        assert funnel["width_reasons"]["over_trade_cap"] == 28

    def test_the_log_is_identical_with_and_without_a_funnel(self, caplog):
        """The line must not depend on whether anyone asked for a funnel."""
        with_funnel = self._lines(caplog, {}, calls=1, **{"max_risk_dollars": 50.0})
        without = self._lines(caplog, None, calls=1, **{"max_risk_dollars": 50.0})
        assert with_funnel == without


class TestCountersOutsideThePartition:
    """A chain the increment filter empties produces a funnel of zeroes — no
    delta_pass, no delta_reject, no width reasons — which is precisely the shape
    where the page most needs a reason. These two say what happened BEFORE the
    strike loop, and are deliberately outside the partition equation."""

    def _mangle(self, chain, mutate):
        key = next(iter(chain["putExpDateMap"]))
        mutate(chain["putExpDateMap"][key])
        return chain

    def test_contracts_with_no_delta_are_counted(self):
        def drop_delta(strikes):
            for leg in strikes.values():
                leg[0]["delta"] = None
        chain, _ = _spreads_chain()
        self._mangle(chain, drop_delta)
        funnel = {}
        _screen(chain, funnel=funnel)
        assert funnel["strikes_dropped_no_delta"] == 61     # the whole put ladder
        # Outside the partition: they never entered the strike loop at all.
        assert funnel["delta_pass"] + funnel["delta_reject"] == 61   # calls only

    def test_a_strike_with_no_contract_row_counts_as_no_delta(self):
        """An empty contract list is the same absence by a different route, and
        was silently dropped before. A strike with no contract has no delta."""
        def empty_them(strikes):
            for k in list(strikes):
                strikes[k] = []
        chain, _ = _spreads_chain()
        self._mangle(chain, empty_them)
        funnel = {}
        _screen(chain, funnel=funnel)
        assert funnel["strikes_dropped_no_delta"] == 61

    def test_off_increment_strikes_are_counted(self):
        """Half strikes on a $1 ladder: the increment filter drops them, and
        without this counter that drop left no trace anywhere."""
        chain, _ = _spreads_chain()
        key = next(iter(chain["putExpDateMap"]))
        strikes = chain["putExpDateMap"][key]
        template = strikes[next(iter(strikes))][0]
        for half in (495.5, 496.5, 497.5):
            strikes[str(half)] = [dict(template, delta=-0.20, mark=3.2,
                                       bid=3.18, ask=3.22)]
        funnel = {}
        _screen(chain, funnel=funnel)
        assert funnel["strikes_dropped_off_increment"] == 3

    def test_a_clean_chain_drops_nothing(self):
        """Both counters are zero on the fixture every other test uses, so a
        non-zero reading always means something really happened."""
        chain, _ = _spreads_chain()
        funnel = {}
        _screen(chain, funnel=funnel)
        assert funnel["strikes_dropped_no_delta"] == 0
        assert funnel["strikes_dropped_off_increment"] == 0

    def test_both_counters_accumulate_like_every_other_key(self):
        chain, _ = _spreads_chain()
        key = next(iter(chain["putExpDateMap"]))
        for leg in chain["putExpDateMap"][key].values():
            leg[0]["delta"] = None
        funnel = {}
        _screen(chain, funnel=funnel)
        _screen(chain, funnel=funnel)
        assert funnel["strikes_dropped_no_delta"] == 122

    def test_an_emptied_chain_still_reports_a_reason(self):
        """The whole point: every partition counter is zero, and the funnel is
        still not silent."""
        def drop_delta(strikes):
            for leg in strikes.values():
                leg[0]["delta"] = None
        chain, _ = _spreads_chain()
        self._mangle(chain, drop_delta)
        # Kill the call side too, so NOTHING reaches the strike loop.
        ckey = next(iter(chain["callExpDateMap"]))
        for leg in chain["callExpDateMap"][ckey].values():
            leg[0]["delta"] = None
        funnel = {}
        assert _screen(chain, funnel=funnel) == []
        assert funnel["delta_pass"] == 0
        assert funnel["delta_reject"] == 0
        assert funnel["width_reasons"] == Counter()
        assert funnel["strikes_dropped_no_delta"] == 122    # the reason
        assert funnel["expiration_sides_in_window"] == 2

    def test_the_new_counters_never_change_the_signals(self):
        chain, _ = _spreads_chain()
        key = next(iter(chain["putExpDateMap"]))
        strikes = chain["putExpDateMap"][key]
        template = strikes[next(iter(strikes))][0]
        strikes["495.5"] = [dict(template, delta=-0.20, mark=3.2, bid=3.18, ask=3.22)]
        other, _ = _spreads_chain()
        okey = next(iter(other["putExpDateMap"]))
        ostrikes = other["putExpDateMap"][okey]
        otemplate = ostrikes[next(iter(ostrikes))][0]
        ostrikes["495.5"] = [dict(otemplate, delta=-0.20, mark=3.2, bid=3.18, ask=3.22)]
        assert _comparable(_screen(chain)) == _comparable(_screen(other, funnel={}))
