"""Tests for the pure realized-outcome statistics.

Hoisted from ``tools/tests/`` with the module itself on 2026-08-25 -- see
``shared/calibration.py`` for why the math moved. The CLI's own tests (SQL,
rendering, argument wiring) stay in ``tools/tests/test_signal_calibration.py``.

Run from the repo root:
    .venv\Scripts\python -m pytest shared\tests\test_calibration.py -v
"""
import pytest

from shared import calibration as C


def _row(pnl, *, max_loss=1.0, credit=1.0, delta=-0.20, grade="Good", score=60.0,
         **kw):
    """One joined signals x signal_outcomes row, in the shape load_rows emits."""
    row = {"realized_pnl": pnl, "entry_max_loss": max_loss, "entry_credit": credit,
           "entry_short_delta": delta, "entry_grade": grade, "entry_score": score,
           "scanner_type": "0DTE", "strategy": "PCS", "symbol": "SPY",
           "exit_reason": "EXPIRED"}
    row.update(kw)
    return row



class TestRMultiple:
    def test_it_expresses_dollars_as_a_multiple_of_the_dollars_at_risk(self):
        # entry_max_loss is PER SHARE; one contract risks 100x that.
        assert C.r_multiple(56.0, 1.44) == pytest.approx(56.0 / 144.0, abs=1e-9)

    def test_a_full_loss_is_minus_one_r(self):
        assert C.r_multiple(-144.0, 1.44) == pytest.approx(-1.0, abs=1e-9)

    def test_a_max_loss_of_zero_yields_None_rather_than_infinity(self):
        assert C.r_multiple(50.0, 0.0) is None

    def test_a_non_finite_input_yields_None(self):
        # A NaN clamps to the HIGH bound elsewhere in this repo and renders as a
        # confident extreme reading. Refuse it at the door.
        assert C.r_multiple(float("nan"), 1.44) is None
        assert C.r_multiple(50.0, float("nan")) is None


class TestWhatTheEntryPriceImplied:
    def test_breakeven_win_rate_is_max_loss_over_width(self):
        # p* = 1/(1+b) with b = credit/max_loss  =>  max_loss/(credit+max_loss).
        assert C.breakeven_win_rate(0.56, 1.44) == pytest.approx(0.72, abs=1e-9)

    def test_priced_win_rate_is_one_minus_the_absolute_short_delta(self):
        assert C.priced_win_rate(-0.221) == pytest.approx(0.779, abs=1e-9)
        assert C.priced_win_rate(0.221) == pytest.approx(0.779, abs=1e-9)

    def test_a_missing_delta_yields_None_rather_than_a_confident_one(self):
        assert C.priced_win_rate(None) is None

    def test_an_iron_condors_summed_delta_cannot_price_a_win_rate(self):
        """signal_db stores ONE entry_short_delta, and scanner_engine writes the
        SUM of the two shorts into it -- which for a symmetric condor is ~0, so
        1-|d| would report a confident 100% win rate on the one strategy that
        can lose on either side. Refuse rather than invent."""
        assert C.priced_win_rate(0.001, strategy="IC") is None
        assert C.priced_win_rate(0.001, strategy="ic") is None
        assert C.priced_win_rate(-0.20, strategy="PCS") == pytest.approx(0.80)

    def test_an_iron_condor_is_excluded_from_a_buckets_priced_win_rate(self):
        rows = [_row(50.0, delta=-0.20, strategy="PCS"),
                _row(50.0, delta=0.001, strategy="IC")]
        # 0.80 alone -- not averaged with a fabricated 0.999.
        assert C.bucket_stats(rows)["priced_p"] == pytest.approx(0.80)


class TestBucketStats:
    def test_it_reports_the_realized_win_rate(self):
        rows = [_row(50.0), _row(50.0), _row(50.0), _row(-100.0)]
        assert C.bucket_stats(rows)["realized_p"] == pytest.approx(0.75)

    def test_the_ev_formula_agrees_with_the_mean_r(self):
        """p*b - (1-p) is stated in units of the AVERAGE LOSS, so scaling it by
        that average loss must reproduce the arithmetic mean R exactly. If these
        two ever disagree the report is lying about one of them."""
        rows = [_row(60.0), _row(40.0), _row(80.0), _row(-100.0), _row(-50.0)]
        s = C.bucket_stats(rows)
        assert s["ev_units"] * abs(s["avg_loss_r"]) == pytest.approx(s["ev_r"], abs=1e-9)

    def test_ev_is_positive_exactly_when_the_win_rate_beats_its_breakeven(self):
        # b = 0.5 => breakeven p = 1/1.5 = 0.667. Three wins in four clears it.
        rows = [_row(50.0), _row(50.0), _row(50.0), _row(-100.0)]
        s = C.bucket_stats(rows)
        assert s["b"] == pytest.approx(0.5)
        assert s["breakeven_p"] == pytest.approx(2 / 3, abs=1e-9)
        assert s["realized_p"] > s["breakeven_p"]
        assert s["ev_units"] > 0 and s["ev_r"] > 0

    def test_a_losing_bucket_reports_a_negative_ev(self):
        rows = [_row(50.0), _row(-100.0), _row(-100.0)]
        assert C.bucket_stats(rows)["ev_r"] < 0

    def test_a_scratch_counts_against_the_win_rate_and_is_reported_separately(self):
        """A zero-P&L close is neither a win nor a loss. It must dilute the win
        rate (driver_perf.build_scorecard already treats it that way) and it must
        be VISIBLE, because a bucket that is half scratches is not a bucket whose
        win rate means anything."""
        s = C.bucket_stats([_row(50.0), _row(-100.0), _row(0.0)])
        assert s["scratches"] == 1
        assert s["realized_p"] == pytest.approx(1 / 3)

    def test_the_identity_still_holds_with_a_scratch_present(self):
        rows = [_row(50.0), _row(50.0), _row(-100.0), _row(0.0)]
        s = C.bucket_stats(rows)
        assert s["ev_units"] * abs(s["avg_loss_r"]) == pytest.approx(s["ev_r"], abs=1e-9)

    def test_a_bucket_with_no_losses_reports_no_b_rather_than_infinity(self):
        s = C.bucket_stats([_row(50.0), _row(50.0)])
        assert s["b"] is None and s["breakeven_p"] is None
        assert s["ev_r"] == pytest.approx(0.5)   # the mean R is still knowable

    def test_an_empty_bucket_reports_nothing_rather_than_raising(self):
        s = C.bucket_stats([])
        assert s["n"] == 0 and s["ev_r"] is None and s["realized_p"] is None

    def test_it_reports_the_gap_between_the_priced_and_the_realized_win_rate(self):
        """The whole point. priced_p comes from the entry delta -- the market's
        own number -- so a positive gap is the only evidence of real edge."""
        rows = [_row(50.0, delta=-0.20)] * 9 + [_row(-100.0, delta=-0.20)]
        s = C.bucket_stats(rows)
        assert s["priced_p"] == pytest.approx(0.80)
        assert s["realized_p"] == pytest.approx(0.90)
        assert s["edge_pp"] == pytest.approx(10.0, abs=1e-9)

    def test_it_reports_a_t_stat_so_a_thin_bucket_cannot_masquerade_as_edge(self):
        rows = [_row(50.0), _row(50.0), _row(-100.0)]
        assert C.bucket_stats(rows)["t_stat"] is not None

    def test_a_single_trade_has_no_t_stat(self):
        assert C.bucket_stats([_row(50.0)])["t_stat"] is None


class TestDayClustering:
    """One scan emits many signals at once and they all ride the same tape, so
    793 rows are nowhere near 793 independent bets -- prod's are 49 trading days.
    A naive t-stat treats them as independent and overstates significance by
    roughly sqrt(rows/days). The clustered t is the one to read."""

    def test_it_counts_the_distinct_days_a_bucket_spans(self):
        rows = [_row(50.0, first_seen_date="2026-06-01"),
                _row(50.0, first_seen_date="2026-06-01"),
                _row(-100.0, first_seen_date="2026-06-02")]
        assert C.bucket_stats(rows)["days"] == 2

    def test_the_naive_t_cannot_tell_six_days_from_two(self):
        """The bug being guarded against, stated as a test: the same six outcomes
        crammed into two days must look identical to the naive t."""
        spread = ([_row(50.0, first_seen_date=f"2026-06-0{i}") for i in range(1, 6)]
                  + [_row(-100.0, first_seen_date="2026-06-06")])
        crammed = ([_row(50.0, first_seen_date="2026-06-01")] * 5
                   + [_row(-100.0, first_seen_date="2026-06-02")])
        assert (C.bucket_stats(spread)["t_stat"]
                == pytest.approx(C.bucket_stats(crammed)["t_stat"]))

    def test_the_clustered_t_does_tell_them_apart(self):
        spread = ([_row(50.0, first_seen_date=f"2026-06-0{i}") for i in range(1, 6)]
                  + [_row(-100.0, first_seen_date="2026-06-06")])
        crammed = ([_row(50.0, first_seen_date="2026-06-01")] * 5
                   + [_row(-100.0, first_seen_date="2026-06-02")])
        assert C.bucket_stats(crammed)["t_day"] < C.bucket_stats(spread)["t_day"]

    def test_a_single_day_has_no_clustered_t(self):
        rows = [_row(50.0, first_seen_date="2026-06-01"),
                _row(-100.0, first_seen_date="2026-06-01")]
        assert C.bucket_stats(rows)["t_day"] is None

    def test_undated_rows_do_not_invent_a_day(self):
        assert C.bucket_stats([_row(50.0), _row(-100.0)])["days"] == 0
        assert C.bucket_stats([_row(50.0), _row(-100.0)])["t_day"] is None


class TestCalibrate:
    def test_it_groups_by_the_requested_key(self):
        rows = [_row(50.0, entry_grade="Good"), _row(-100.0, entry_grade="Marginal")]
        got = {b["bucket"]: b["n"] for b in C.calibrate(rows, "entry_grade")}
        assert got == {"Good": 1, "Marginal": 1}

    def test_it_drops_buckets_thinner_than_min_n(self):
        rows = [_row(50.0, entry_grade="Good")] * 5 + [_row(50.0, entry_grade="Strong")]
        got = [b["bucket"] for b in C.calibrate(rows, "entry_grade", min_n=5)]
        assert got == ["Good"]

    def test_it_orders_buckets_by_name_so_score_bins_read_in_order(self):
        rows = ([_row(50.0, entry_grade="Marginal")] * 2
                + [_row(50.0, entry_grade="Good")] * 2)
        assert [b["bucket"] for b in C.calibrate(rows, "entry_grade")] == ["Good", "Marginal"]

    def test_a_row_with_an_unusable_r_is_excluded_not_counted_as_a_loss(self):
        rows = [_row(50.0), _row(50.0), _row(50.0, max_loss=0.0)]
        assert C.calibrate(rows, "entry_grade")[0]["n"] == 2


class TestScoreBin:
    def test_it_bins_a_continuous_score_into_fixed_width_labels(self):
        assert C.score_bin(63.1, width=5) == "60-65"
        assert C.score_bin(65.0, width=5) == "65-70"

    def test_an_unusable_score_bins_to_a_named_bucket_not_a_crash(self):
        assert C.score_bin(None) == "?"
        assert C.score_bin(float("nan")) == "?"


class TestSplit:
    """0-DTE and swing are different games -- a 0-DTE spread has hours of gamma
    risk and no recovery time, a 14-DTE one has neither. Pooling them reports the
    average of two populations that may not share a gate."""

    def test_it_reports_the_full_breakdown_once_per_split_value(self):
        rows = [_row(50.0, entry_grade="Good", scanner_type="0DTE"),
                _row(50.0, entry_grade="Good", scanner_type="SWING"),
                _row(-100.0, entry_grade="Marginal", scanner_type="SWING")]
        got = C.split_calibrate(rows, "entry_grade", "scanner_type")
        assert [name for name, _ in got] == ["0DTE", "SWING"]
        assert [b["bucket"] for b in dict(got)["SWING"]] == ["Good", "Marginal"]

    def test_min_n_applies_within_a_split_not_across_it(self):
        """The trap: 6 Good trades pooled clears min_n=5, but split 3-and-3
        neither side does. Reporting the pooled number under a per-side heading
        would be a lie."""
        rows = ([_row(50.0, entry_grade="Good", scanner_type="0DTE")] * 3
                + [_row(50.0, entry_grade="Good", scanner_type="SWING")] * 3)
        assert C.calibrate(rows, "entry_grade", min_n=5)[0]["n"] == 6
        assert C.split_calibrate(rows, "entry_grade", "scanner_type", min_n=5) == []

    def test_a_split_value_left_with_no_buckets_is_dropped_entirely(self):
        rows = ([_row(50.0, entry_grade="Good", scanner_type="0DTE")] * 5
                + [_row(50.0, entry_grade="Good", scanner_type="SWING")])
        got = C.split_calibrate(rows, "entry_grade", "scanner_type", min_n=5)
        assert [name for name, _ in got] == ["0DTE"]

    def test_a_missing_split_value_is_named_rather_than_dropped(self):
        got = C.split_calibrate([_row(50.0, scanner_type=None)], "entry_grade",
                                "scanner_type")
        assert [name for name, _ in got] == ["?"]


class TestFamilyKey:
    """The two tiers spell the family differently and nothing forced them to
    agree: `signals.db` stores scanner_type '0DTE', while a live signal on the
    page carries trade_type '0-DTE' and a scanner_type of None. Keyed on the raw
    value, the 0-DTE bucket would never match page-side -- silently, and for the
    family with the most data. One normalizer, used by BOTH sides."""

    def test_both_spellings_of_zero_dte_reach_the_same_key(self):
        assert C.family_key("0DTE") == C.family_key("0-DTE") == "0DTE"

    def test_swing_is_stable_across_tiers(self):
        assert C.family_key("SWING") == C.family_key("swing") == "SWING"

    def test_a_family_we_do_not_record_has_no_key(self):
        """Directional/Strategy-Finder signals carry no trade_type at all and are
        not recorded in signals.db. No key means no bucket means no display."""
        assert C.family_key(None) is None
        assert C.family_key("") is None

    def test_an_unknown_family_is_passed_through_not_guessed(self):
        assert C.family_key("MONTHLY") == "MONTHLY"

    def test_the_bucket_key_pairs_family_with_score_bin(self):
        assert C.bucket_key("0-DTE", 63.1) == "0DTE|60-65"
        assert C.bucket_key("0DTE", 63.1) == "0DTE|60-65"

    def test_no_family_or_no_score_yields_no_bucket_key(self):
        assert C.bucket_key(None, 63.1) is None
        assert C.bucket_key("0DTE", None) is None
        assert C.bucket_key("0DTE", float("nan")) is None
