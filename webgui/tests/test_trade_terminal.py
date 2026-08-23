"""Tests for the Signal Desk terminal builders.

The four Trade Analyzer screens share one command bar and one bar language, and
both are pure. What is worth pinning is where the design could quietly lie:

  * the command bar's symbol is a DRAFT until committed, and an unknown ticker
    has to say it is unknown rather than showing a blank company line;
  * every bar reads against a CENTRED axis, so a bar's meaning must not change
    between the percentile rail, the investor factors and the contributions;
  * a missing number renders as absent, never as zero — this whole app's
    documented failure mode is a confident number over no data.
"""
import pytest

from pages import trade_terminal as tt
from pages import terminal_theme as T


class TestTheCommandBar:
    def test_it_carries_price_and_change_with_a_direction_class(self):
        bar = tt.command_bar({"symbol": "AAPL", "price": 309.69,
                              "change_pct": -0.36})
        assert bar["price"] == "309.69"
        assert "0.36" in bar["change"]
        assert bar["change_class"] == T.NEG

    def test_a_rising_price_reads_green(self):
        bar = tt.command_bar({"symbol": "X", "price": 10.0,
                              "change_pct": 1.2})
        assert bar["change_class"] == T.POS

    def test_an_unknown_change_is_a_dash_not_a_zero(self):
        bar = tt.command_bar({"symbol": "X", "price": 10.0})
        assert bar["change"] == "—"
        assert bar["change_class"] == T.OFF

    def test_the_company_line_comes_from_the_payload(self):
        bar = tt.command_bar({"symbol": "AAPL", "description": "Apple Inc",
                              "sector": {"name": "Technology", "etf": "XLK"}})
        assert "Apple Inc" in bar["name"]
        assert "Technology" in bar["name"]

    def test_a_symbol_with_no_analysis_says_it_is_not_in_the_cross_section(self):
        bar = tt.command_bar({"symbol": "ZZZZ"})
        assert "cross-section" in bar["name"]

    def test_the_bias_chip_maps_to_a_finite_class_set(self):
        for bias, cls in (("BULLISH", T.POS), ("BEARISH", T.NEG),
                          ("NEUTRAL", T.DIM), ("", T.DIM)):
            assert tt.command_bar({"symbol": "X", "bias": bias})["bias_class"] == cls

    def test_the_model_stamp_names_the_artifact(self):
        bar = tt.command_bar({"symbol": "X",
                              "swing_model": {"model_version": "2026-08-22"}})
        assert "2026-08-22" in bar["model_stamp"]

    def test_no_artifact_still_produces_a_stamp(self):
        assert tt.command_bar({"symbol": "X"})["model_stamp"]


class TestTheSharedBarLanguage:
    def test_a_positive_value_grows_RIGHT_from_the_centre(self):
        left, width = T.centred(0.5, 1.0)
        assert left == pytest.approx(50.0)
        assert width == pytest.approx(25.0)

    def test_a_negative_value_grows_LEFT_from_the_centre(self):
        left, width = T.centred(-0.5, 1.0)
        assert left == pytest.approx(25.0)
        assert width == pytest.approx(25.0)

    def test_it_clamps_at_the_half_range_rather_than_overflowing(self):
        left, width = T.centred(99.0, 1.0)
        assert width == pytest.approx(50.0)
        assert left == pytest.approx(50.0)

    def test_a_degenerate_scale_does_not_divide_by_zero(self):
        assert T.centred(1.0, 0) == (50.0, 50.0)

    def test_a_non_numeric_value_is_a_zero_width_bar(self):
        assert T.centred(None, 1.0) == (50.0, 0.0)


class TestThePercentileRail:
    def test_the_marker_sits_at_the_percentile(self):
        rail = tt.percentile_rail({"percentile": 90, "score": 0.84,
                                   "expected_fwd": 0.016, "hit_rate": 0.53})
        assert rail["pos_pct"] == pytest.approx(90.0)
        assert rail["percentile"] == "90th"

    def test_it_states_the_calibrated_stats_beneath(self):
        rail = tt.percentile_rail({"percentile": 90, "score": 0.84,
                                   "expected_fwd": 0.016, "hit_rate": 0.53})
        assert "+1.6%" in rail["stats"] and "53%" in rail["stats"]

    def test_no_percentile_is_UNRANKED_and_parks_the_marker_centre(self):
        rail = tt.percentile_rail({})
        assert rail["percentile"] == "—"
        assert rail["pos_pct"] == pytest.approx(50.0)
        assert "unranked" in rail["note"].lower()

    def test_a_missing_expectation_does_not_print_a_zero(self):
        rail = tt.percentile_rail({"percentile": 50})
        assert "+0.0%" not in rail["stats"]


class TestGateChips:
    def test_a_cleared_side_reads_positive(self):
        chips = tt.gate_chips({"long": {"state": "cleared", "reasons": []},
                               "short": {"state": "cleared", "reasons": []}})
        assert all(c["chip_class"] == T.CHIP_POS for c in chips)

    def test_a_relative_only_side_reads_as_caution_not_failure(self):
        chips = tt.gate_chips({"long": {"state": "cleared", "reasons": []},
                               "short": {"state": "relative_only",
                                         "reasons": ["SPY above a rising 200-DMA"]}})
        short = next(c for c in chips if c["side"] == "short")
        assert short["chip_class"] == T.CHIP_WARN
        assert "RELATIVE" in short["label"]

    def test_a_blocked_side_reads_negative(self):
        chips = tt.gate_chips({"short": {"state": "blocked", "reasons": ["x"]}})
        assert any(c["chip_class"] == T.CHIP_NEG for c in chips)

    def test_no_clearance_yields_no_chips_rather_than_green_ones(self):
        assert tt.gate_chips(None) == []
        assert tt.gate_chips({}) == []


class TestInvestorFactorBars:
    def test_each_factor_gets_a_centred_bar_and_a_signed_value(self):
        bars = tt.investor_bars({"breakdown": [
            {"factor": "growth_quality", "contribution": 35.0},
            {"factor": "valuation", "contribution": -5.0}]})
        assert bars[0]["value"] == "+35"
        assert bars[0]["bar_class"] == T.BAR_POS
        assert bars[1]["value"] == "−5"
        assert bars[1]["bar_class"] == T.BAR_NEG

    def test_an_absent_factor_renders_n_a_with_no_bar(self):
        bars = tt.investor_bars({"breakdown": [
            {"factor": "earnings_traj", "contribution": None}]})
        assert bars[0]["value"] == "n/a"
        assert bars[0]["width_pct"] == 0.0

    def test_the_label_is_humanized_not_a_snake_case_key(self):
        bars = tt.investor_bars({"breakdown": [
            {"factor": "growth_quality", "contribution": 1.0}]})
        assert "_" not in bars[0]["label"]

    def test_no_breakdown_is_an_empty_list(self):
        assert tt.investor_bars({}) == []
        assert tt.investor_bars(None) == []


class TestTheDealerLadder:
    def test_the_four_marks_are_ordered_by_price(self):
        marks = tt.dealer_ladder({"collected": True, "stale": False,
                                  "put_wall": 300.0, "flip": 306.5,
                                  "call_wall": 315.0}, spot=309.69)
        assert [m["kind"] for m in marks] == ["put_wall", "flip", "spot", "call_wall"]

    def test_positions_span_the_rail_without_clipping(self):
        marks = tt.dealer_ladder({"collected": True, "stale": False,
                                  "put_wall": 300.0, "flip": 306.5,
                                  "call_wall": 315.0}, spot=309.69)
        assert all(0.0 <= m["pos_pct"] <= 100.0 for m in marks)

    def test_spot_is_the_emphasised_mark(self):
        marks = tt.dealer_ladder({"collected": True, "stale": False,
                                  "put_wall": 300.0, "call_wall": 315.0},
                                 spot=309.0)
        spot = next(m for m in marks if m["kind"] == "spot")
        assert spot["emphasis"] is True

    def test_uncollected_dealer_data_yields_NO_ladder(self):
        """Withheld levels must not become a drawn ladder — the off-hours case
        turns on that distinction."""
        assert tt.dealer_ladder({"collected": False}, spot=100.0) == []
        assert tt.dealer_ladder({"collected": True, "stale": True}, spot=100.0) == []
        assert tt.dealer_ladder(None, spot=100.0) == []

    def test_a_ladder_with_no_spot_is_refused(self):
        assert tt.dealer_ladder({"collected": True, "put_wall": 1.0}, spot=None) == []


class TestEvidenceRows:
    _SM = {"contributions": [
        {"factor": "mom_6_1", "z": 0.86, "weight": 0.137,
         "contribution": 0.118, "ic": 0.021},
        {"factor": "low_vol", "z": 0.26, "weight": -0.391,
         "contribution": -0.101, "ic": -0.061}]}

    def test_each_row_carries_a_centred_contribution_bar(self):
        rows = tt.evidence_rows(self._SM)
        assert rows[0]["bar_class"] == T.BAR_POS
        assert rows[1]["bar_class"] == T.BAR_NEG
        assert rows[1]["left_pct"] < 50.0

    def test_values_are_signed_and_fixed_width(self):
        rows = tt.evidence_rows(self._SM)
        assert rows[0]["z"] == "+0.86"
        assert rows[1]["weight"] == "−0.391"

    def test_the_composite_is_the_weighted_sum(self):
        assert tt.evidence_composite(self._SM) == pytest.approx(0.017, abs=1e-6)

    def test_a_factor_the_page_can_name_is_humanized(self):
        assert "_" not in tt.evidence_rows(self._SM)[0]["name"]

    def test_no_contributions_is_an_empty_table(self):
        assert tt.evidence_rows({}) == []
        assert tt.evidence_composite({}) is None


# ── Row-builder shapes (found live, twice) ───────────────────────────────────
# `dealer_rows` and `plan_rows` both return DICTS. The Signal Desk screens
# consumed them as (label, value) tuples, which unpacks a dict's KEYS — the
# Trade plan screen 500-ed on it and the Overview screen would have rendered
# the strings "label" and "value" as its dealer stats.
#
# Nothing in the suite could catch that: the webgui tests exercise pure builders
# and assert `callable(render)`, which never executes a render body, and ruff's
# F82 sees no undefined name. So the shape is pinned HERE, at the boundary the
# screens actually cross.

class TestTheRowBuildersReturnDictsNotTuples:
    def test_plan_rows_are_label_value_note_dicts(self):
        from pages.trade import plan_rows
        rows = plan_rows({
            "action": "debit", "structure": "Call debit spread",
            "dte_min": 30, "dte_max": 45, "rationale": "IV is cheap",
            "entry_zone": "pull back", "stop": 172.1, "target": "+1.6%",
            "time_stop_trading_days": 20, "time_stop_date": "2026-09-21",
            "time_stop_note": "unmodelled past here", "events": "none"})
        assert rows, "fixture should produce rows"
        for r in rows:
            assert isinstance(r, dict), f"plan_rows yielded {type(r)}"
            assert {"label", "value"} <= set(r)

    def test_dealer_rows_are_label_value_dicts(self):
        from pages.trade import dealer_rows
        rows = dealer_rows({"collected": True, "regime_words": "Above flip",
                            "atm_iv": 31.0})
        assert rows, "fixture should produce rows"
        for r in rows:
            assert isinstance(r, dict), f"dealer_rows yielded {type(r)}"
            assert {"label", "value"} <= set(r)

    def test_gate_rows_are_plain_strings(self):
        """The other half of the same trap: these ARE flat, so a screen that
        treated them like the dict builders would be equally wrong."""
        from pages.trade import gate_rows, short_gate_rows
        v = {"gates_triggered": ["below its 200-EMA"],
             "short_gates": ["squeeze risk"]}
        assert all(isinstance(g, str) for g in gate_rows(v))
        assert all(isinstance(g, str) for g in short_gate_rows(v))


class TestTheCommandBarReadsTheRealPayloadShape:
    def test_the_change_comes_from_the_TOP_LEVEL_field(self):
        """`analyze` stores the quote's own `change_pct`; the momentum block
        carries indicators (RSI, ADX, MACD, VWAP) and never had a change at
        all. Reading the wrong one renders a permanent em dash over data that
        was fetched."""
        bar = tt.command_bar({"symbol": "MU", "price": 963.68,
                              "change_pct": -0.32065})
        assert "0.32" in bar["change"]
        assert bar["change_class"] == T.NEG

    def test_a_description_that_merely_repeats_the_ticker_is_dropped(self):
        """Schwab's quote has no company name — `description` is the SYMBOL. It
        must not render as 'MU · MU · Technology'."""
        bar = tt.command_bar({"symbol": "MU", "description": "MU",
                              "sector": {"name": "Technology", "etf": "XLK"}})
        assert bar["name"] == "Technology · XLK"

    def test_a_real_description_is_kept(self):
        bar = tt.command_bar({"symbol": "AAPL", "description": "Apple Inc",
                              "sector": {"name": "Technology"}})
        assert bar["name"].startswith("Apple Inc")
