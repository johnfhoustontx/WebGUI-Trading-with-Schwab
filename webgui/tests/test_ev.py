"""Tests for the Trade detail panel's two expected-value rows.

Two numbers that answer different questions, and the difference is the whole
design:

* **breakeven win rate** -- structural. What this trade's own price DEMANDS.
  Derived from credit and max loss, which the panel already shows.
* **calibrated EV** -- the recommendation. What signals in the same family and
  score band actually RETURNED, from `cache:options:calibration`.

The priced EV (`pop_pct` against `rr_pct`) is deliberately absent: measured on
prod it is ~0 in the median and, where large, is measuring a broken mark -- the
top three live signals by priced EV had relative bid-ask spreads of 225%, 239%
and 395%. See docs/plans/2026-08-25-ev-in-trade-detail-design.md.

Run from webgui:
    ..\\.venv\\Scripts\\python -m pytest tests\\test_ev.py -v
"""
import pytest

from pages.options import ev


def _spread(**kw):
    """A 0-DTE credit spread as the panel receives it."""
    s = {"symbol": "SPY", "type": "PCS", "trade_type": "0-DTE",
         "credit": 0.56, "max_loss": 1.44, "width": 2.0,
         "pop_pct": 77.9, "short_delta": -0.221, "composite_score": 63.1}
    s.update(kw)
    return s


def _directional(**kw):
    """A Strategy Finder signal -- a DIFFERENT vocabulary: max_profit/max_loss in
    dollars, no credit, no short_delta, no trade_type."""
    s = {"symbol": "AAPL", "strategy_label": "Long Put", "max_loss": 556.3,
         "max_profit": 70343.7, "pop_pct": 34.3, "composite_score": 70.0,
         "net_debit": 556.3}
    s.update(kw)
    return s


def _payload(**buckets):
    return {"buckets": buckets or {}, "min_n": 15, "t_gate": 2.0, "rows": 793}


def _bucket(ev_r=0.524, n=108, days=36, t_day=4.76, speaks=True):
    return {"ev_r": ev_r, "n": n, "days": days, "t_day": t_day,
            "speaks": speaks, "realized_p": 0.843, "b": 3.73, "t_stat": 3.8}


class TestBreakevenWinRate:
    def test_it_is_max_loss_over_the_width(self):
        f = ev.breakeven_facts(_spread())
        assert f["breakeven_pct"] == pytest.approx(72.0, abs=1e-6)

    def test_it_reports_the_margin_over_what_the_trade_requires(self):
        """The number the reader actually wants: 77.9% against a required 72.0%
        is +5.9 points of cushion."""
        assert ev.breakeven_facts(_spread())["margin_pp"] == pytest.approx(5.9, abs=1e-6)

    def test_a_trade_priced_below_its_breakeven_reports_a_negative_margin(self):
        f = ev.breakeven_facts(_spread(pop_pct=65.0))
        assert f["margin_pp"] < 0

    def test_the_tone_comes_from_a_finite_class_set_never_a_runtime_colour(self):
        """Tailwind-first: a data-driven colour maps from its known finite set to
        a static class, never an f-string arbitrary value."""
        tones = {ev.breakeven_facts(_spread(pop_pct=p))["tone"]
                 for p in (95.0, 77.9, 72.5, 65.0)}
        assert tones <= set(ev.TONE_CLASSES)

    def test_a_signal_with_no_credit_yields_nothing_at_all(self):
        """Directional/Strategy Finder. Decided 2026-08-25: show NOTHING, not a
        dash and not a flagged number."""
        assert ev.breakeven_facts(_directional()) is None

    def test_a_max_profit_shaped_signal_is_never_given_a_breakeven(self):
        """max_profit is a TAIL outcome while pop_pct is P(any profit). Treating
        them as the same event prints +2137R for a long put."""
        assert ev.breakeven_facts(_directional(credit=5.563)) is None

    def test_a_zero_or_missing_max_loss_yields_nothing(self):
        assert ev.breakeven_facts(_spread(max_loss=0)) is None
        assert ev.breakeven_facts(_spread(max_loss=None)) is None

    def test_a_missing_probability_still_yields_the_requirement(self):
        """The breakeven is structural -- it does not need pop_pct. Without one
        there is simply no margin to report."""
        f = ev.breakeven_facts(_spread(pop_pct=None))
        assert f["breakeven_pct"] == pytest.approx(72.0) and f["margin_pp"] is None

    def test_an_iron_condor_still_gets_one(self):
        """The IC trap is the delta-derived probability, not the breakeven --
        credit and max loss are perfectly well defined for a condor."""
        assert ev.breakeven_facts(_spread(type="IC")) is not None


class TestCalibratedEv:
    def test_it_finds_the_bucket_for_this_signals_family_and_score(self):
        f = ev.calibrated_facts(_spread(), _payload(**{"0DTE|60-65": _bucket()}))
        assert f["ev_r"] == pytest.approx(0.524)

    def test_the_sentence_carries_its_own_sample_size(self):
        """A bucket of three must not read like a bucket of three hundred."""
        f = ev.calibrated_facts(_spread(), _payload(**{"0DTE|60-65": _bucket()}))
        assert "108" in f["text"] and "36" in f["text"]

    def test_a_bucket_that_does_not_speak_is_withheld(self):
        """|tDay| < 2. An EV we cannot distinguish from zero is not a
        recommendation."""
        payload = _payload(**{"0DTE|60-65": _bucket(speaks=False)})
        assert ev.calibrated_facts(_spread(), payload) is None

    def test_an_absent_bucket_yields_nothing(self):
        assert ev.calibrated_facts(_spread(), _payload()) is None

    def test_a_cold_or_missing_cache_yields_nothing_rather_than_raising(self):
        for payload in (None, {}, {"buckets": None}, "junk"):
            assert ev.calibrated_facts(_spread(), payload) is None

    def test_a_signal_with_no_family_yields_nothing(self):
        """Directional carries no trade_type, and no directional signals are
        recorded in signals.db -- so there is no bucket to find."""
        assert ev.calibrated_facts(_directional(), _payload(**{"0DTE|60-65": _bucket()})) is None

    def test_the_page_side_family_spelling_matches_the_service_side_key(self):
        """The panel says trade_type '0-DTE'; signals.db says scanner_type
        '0DTE'. If these ever diverge the richest family silently shows nothing."""
        from shared.calibration import bucket_key
        assert bucket_key("0-DTE", 63.1) == "0DTE|60-65"
        f = ev.calibrated_facts(_spread(trade_type="0-DTE"),
                                _payload(**{"0DTE|60-65": _bucket()}))
        assert f is not None

    def test_a_negative_bucket_is_stated_plainly_not_hidden(self):
        payload = _payload(**{"0DTE|60-65": _bucket(ev_r=-0.31, t_day=-2.4)})
        f = ev.calibrated_facts(_spread(), payload)
        assert f["ev_r"] < 0 and f["tone"] in ev.TONE_CLASSES

    def test_the_tone_comes_from_the_finite_set(self):
        for r in (-0.5, 0.0, 0.52):
            payload = _payload(**{"0DTE|60-65": _bucket(ev_r=r, t_day=3.0)})
            assert ev.calibrated_facts(_spread(), payload)["tone"] in ev.TONE_CLASSES

    def test_a_swing_signal_reads_the_swing_bucket_not_the_zero_dte_one(self):
        payload = _payload(**{"0DTE|60-65": _bucket(ev_r=0.52),
                              "SWING|60-65": _bucket(ev_r=0.40)})
        f = ev.calibrated_facts(_spread(trade_type="SWING"), payload)
        assert f["ev_r"] == pytest.approx(0.40)
