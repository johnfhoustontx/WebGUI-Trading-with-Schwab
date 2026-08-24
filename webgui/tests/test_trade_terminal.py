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

    def test_the_caption_does_not_claim_a_rank_among_todays_names(self):
        """The number is a calibration band cut from the model's own history,
        NOT a percentile of today's cross-section. Measured live on 2026-08-22:
        78 names landed 20/20/12/14/12 across the five bands — a true
        cross-sectional percentile could not do that."""
        note = tt.percentile_rail({"percentile": 90})["note"].lower()
        assert "today" not in note
        assert "cross-section" not in note

    def test_it_offers_the_longer_explanation_on_hover(self):
        tip = tt.percentile_rail({"percentile": 90})["tip"].lower()
        assert "band" in tip
        # The specific misreading the caption exists to prevent.
        assert "top 10%" in tip

    def test_an_unranked_reading_carries_no_explanation_to_give(self):
        rail = tt.percentile_rail({})
        assert "unranked" in rail["note"].lower()
        assert rail["tip"] == ""


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

    def test_it_carries_the_raw_factor_key_for_the_help_lookup(self):
        """`name` is humanized for display, so it cannot key the per-tile
        explanations — a renamed label would silently drop the tooltip."""
        bars = tt.investor_bars({"breakdown": [
            {"factor": "growth_quality", "contribution": 1.0}]})
        assert bars[0]["key"] == "growth_quality"

    def test_no_breakdown_is_an_empty_list(self):
        assert tt.investor_bars({}) == []
        assert tt.investor_bars(None) == []

    def test_earnings_trajectory_is_now_an_ORDINARY_row(self):
        """It used to read "not published by Schwab", because Schwab carries no
        surprises and the component could never score. Alpha Vantage's EARNINGS
        endpoint supplies that history now, so a 0 here means what a 0 means on
        every other row — a mixed record — and claiming the data does not exist
        would be the false statement."""
        bars = tt.investor_bars({"breakdown": [
            {"factor": "earnings_traj", "raw_score": 0, "contribution": 0.0}]})
        assert bars[0]["track_text"] == ""
        assert bars[0]["value"] == "0"
        assert bars[0]["unpublished"] is False

    def test_a_scored_earnings_trajectory_reads_as_a_normal_row(self):
        """Alpha Vantage supplies the surprise history Schwab does not, so this
        row scores now. The "not published" treatment must fall away the moment
        a real number arrives — it was never about the factor name."""
        bars = tt.investor_bars({"breakdown": [
            {"factor": "earnings_traj", "raw_score": 80, "contribution": 12.0}]})
        assert bars[0]["unpublished"] is False
        assert bars[0]["track_text"] == ""
        assert bars[0]["value"] == "+12"

    def test_a_scored_earnings_trajectory_is_left_alone(self):
        """Self-correcting: the flag is keyed off the score being 0, not off
        the factor name, so a fundamentals source that DOES carry surprises
        renders normally instead of being libelled as missing."""
        bars = tt.investor_bars({"breakdown": [
            {"factor": "earnings_traj", "raw_score": 40, "contribution": 6.0}]})
        assert bars[0]["unpublished"] is False
        assert bars[0]["value"] == "+6"

    def test_another_factor_scoring_zero_is_a_real_zero(self):
        bars = tt.investor_bars({"breakdown": [
            {"factor": "valuation", "raw_score": 0, "contribution": 0.0}]})
        assert bars[0]["unpublished"] is False
        assert bars[0]["value"] == "0"


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

    def test_it_carries_the_raw_factor_key_for_the_help_lookup(self):
        """Same reason as the investor bars: the displayed name is humanized,
        so only the engine key can look up the factor's explanation."""
        rows = tt.evidence_rows(self._SM)
        assert rows[0]["key"] == "mom_6_1"

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


class TestNegativeZeroNeverRenders:
    """A value that rounds to zero must not carry a sign.

    "−0.00" reads as a small negative number at a glance, which is exactly the
    wrong impression for a factor contributing nothing — and "−0" next to "+0"
    in the investor bars implies a distinction that does not exist."""

    def test_a_contribution_rounding_to_zero_is_unsigned(self):
        rows = tt.evidence_rows({"contributions": [
            {"factor": "mom_12_1", "z": -0.0004, "weight": 0.117,
             "contribution": -0.00002, "ic": 0.018}]})
        assert rows[0]["z"] == "0.00"
        assert rows[0]["contribution"] == "0.000"

    def test_a_real_negative_keeps_its_sign(self):
        rows = tt.evidence_rows({"contributions": [
            {"factor": "low_vol", "z": -0.20, "weight": -0.391,
             "contribution": -0.08, "ic": -0.061}]})
        assert rows[0]["z"] == "−0.20"
        assert rows[0]["weight"] == "−0.391"

    def test_an_investor_factor_rounding_to_zero_is_unsigned(self):
        bars = tt.investor_bars({"breakdown": [
            {"factor": "rs_vs_sector", "contribution": -0.4}]})
        assert bars[0]["value"] == "0"


class TestOneSignedFormatterForTheWholeDesk:
    """Evidence used a true minus and the Rank board a hyphen, so the same
    number rendered two ways one tab apart. At mono sizes a hyphen reads as a
    dash and a negative number stops looking negative — which is the whole
    reason the true minus was chosen."""

    def test_the_formatter_is_public(self):
        assert callable(tt.signed)

    def test_it_uses_a_TRUE_minus_not_a_hyphen(self):
        assert tt.signed(-0.49, 2) == "−0.49"
        assert "-" not in tt.signed(-0.49, 2)

    def test_a_percentage_helper_shares_the_same_sign(self):
        assert tt.signed_pct(-0.008, 1) == "−0.8%"
        assert tt.signed_pct(0.016, 1) == "+1.6%"

    def test_absent_is_a_dash_in_both(self):
        assert tt.signed(None) == "—"
        assert tt.signed_pct(None) == "—"


class TestTheCompanyNameIsShown:
    def test_a_real_company_name_leads_the_line(self):
        bar = tt.command_bar({"symbol": "MU", "company_name": "Micron Technology",
                              "description": "MU",
                              "sector": {"name": "Technology", "etf": "XLK"}})
        assert bar["name"] == "Micron Technology · Technology · XLK"

    def test_it_beats_the_ticker_masquerading_as_a_description(self):
        bar = tt.command_bar({"symbol": "MU", "company_name": "Micron Technology",
                              "description": "MU"})
        assert bar["name"] == "Micron Technology"

    def test_no_company_name_falls_back_to_the_sector_line(self):
        bar = tt.command_bar({"symbol": "MU", "description": "MU",
                              "sector": {"name": "Technology", "etf": "XLK"}})
        assert bar["name"] == "Technology · XLK"

    def test_nothing_at_all_still_says_it_is_not_in_the_cross_section(self):
        assert "cross-section" in tt.command_bar({"symbol": "ZZZZ"})["name"]


def test_the_command_bars_change_uses_the_desk_wide_minus():
    """The bar predated the shared formatter and formatted its own change, so
    the one number visible on every screen was the one rendering a hyphen."""
    bar = tt.command_bar({"symbol": "MU", "price": 963.68, "change_pct": -0.32})
    assert bar["change"] == "−0.32%"
    assert "-" not in bar["change"]


def test_a_rising_change_keeps_its_plus():
    bar = tt.command_bar({"symbol": "MU", "price": 963.68, "change_pct": 0.31})
    assert bar["change"] == "+0.31%"


# ── The plan's actions (found dead in a live read-through) ───────────────────
# Both buttons were drawn from the design and never wired. What they can
# honestly do is set by what the plan carries: a STRUCTURE and a tenor, never
# strikes. So "Open in calculator" pre-selects the structure and the Calculator
# supplies the strikes; the paper action goes via the Strategy Finder, whose
# rows are concrete multi-leg candidates that already carry Send-to-Paper.
# Wiring the plan straight to paper would mean inventing the strikes it
# deliberately declines to specify.

class TestThePlanMapsToACalculatorStrategy:
    def test_each_structure_maps_to_a_real_calculator_template(self):
        from pages.options.strategies import STRATEGY_TEMPLATES
        for structure in ("call debit spread", "put debit spread",
                          "call credit spread", "put credit spread"):
            key = tt.calculator_strategy(structure)
            assert key in STRATEGY_TEMPLATES, f"{structure} -> {key}"

    def test_the_debit_and_credit_sides_do_not_collapse_together(self):
        assert (tt.calculator_strategy("call debit spread")
                != tt.calculator_strategy("call credit spread"))
        assert (tt.calculator_strategy("call debit spread")
                != tt.calculator_strategy("put debit spread"))

    def test_a_relative_pair_has_NO_options_structure(self):
        """'pair vs a defensive name' is a stock pair, not a spread — there is
        nothing for the Calculator to pre-select."""
        assert tt.calculator_strategy("pair vs a defensive name") is None
        assert tt.calculator_strategy(None) is None

    def test_the_handoff_signal_carries_symbol_strategy_and_price(self):
        sig = tt.calculator_handoff({"symbol": "MU", "price": 963.68,
                                     "trade_plan": {"structure": "call debit spread"}})
        assert sig["symbol"] == "MU"
        assert sig["type"] == tt.calculator_strategy("call debit spread")
        assert sig["underlying_price"] == 963.68

    def test_no_plan_yields_no_handoff(self):
        assert tt.calculator_handoff({"symbol": "MU"}) is None
        assert tt.calculator_handoff(None) is None


class TestTheCommandBarDoesNotReanalyzeOnEveryBlur:
    """A regression the live journal caught: the bar enqueued an analyze on
    EVERY blur, and blur fires constantly — clicking any button, leaving the
    page, switching screens. Each analyze is a dozen proxy calls (five
    timeframes, SPY, the sector ETF, fundamentals, a chain), so a session of
    navigating re-analyzed the same name again and again.

    Enter stays an explicit refresh; blur and Tab only commit a CHANGE."""

    def test_blur_on_an_unchanged_symbol_does_not_request(self):
        assert tt.should_commit("MU", committed="MU", explicit=False) is False

    def test_blur_on_a_changed_symbol_does_request(self):
        assert tt.should_commit("NVDA", committed="MU", explicit=False) is True

    def test_ENTER_refreshes_the_same_symbol_deliberately(self):
        assert tt.should_commit("MU", committed="MU", explicit=True) is True

    def test_an_empty_draft_never_requests(self):
        assert tt.should_commit("", committed="MU", explicit=True) is False
        assert tt.should_commit("   ", committed="MU", explicit=False) is False

    def test_no_committed_symbol_yet_means_the_first_one_requests(self):
        assert tt.should_commit("MU", committed="", explicit=False) is True

    def test_case_and_padding_do_not_count_as_a_change(self):
        assert tt.should_commit(" mu ", committed="MU", explicit=False) is False
