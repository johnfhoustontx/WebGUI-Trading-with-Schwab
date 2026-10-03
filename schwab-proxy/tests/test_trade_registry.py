"""
SchwabProxy - Tests for trade registry + OSI resolution
Version: 1.0.0
Last Updated: 2026-05-30

Version 1.0.0 Changes:
- Initial implementation
"""
import trade_registry as tr

CHAIN = {
    "putExpDateMap": {
        "2026-05-30:0": {
            "5200.0": [{"symbol": "SPXW  260530P05200000", "putCall": "PUT", "strikePrice": 5200.0}],
            "5150.0": [{"symbol": "SPXW  260530P05150000", "putCall": "PUT", "strikePrice": 5150.0}],
        }
    },
    "callExpDateMap": {
        "2026-05-30:0": {
            "5300.0": [{"symbol": "SPXW  260530C05300000", "putCall": "CALL", "strikePrice": 5300.0}],
            "5350.0": [{"symbol": "SPXW  260530C05350000", "putCall": "CALL", "strikePrice": 5350.0}],
        }
    },
}


def test_resolve_legs_pcs():
    legs = tr.resolve_legs(CHAIN, "PCS", short_strike=5200, long_strike=5150)
    assert legs == {"put_short": "SPXW  260530P05200000",
                    "put_long": "SPXW  260530P05150000"}


def test_resolve_legs_ic():
    legs = tr.resolve_legs(CHAIN, "IC", short_strike=5200, long_strike=5150,
                           call_short=5300, call_long=5350)
    assert legs["put_short"] == "SPXW  260530P05200000"
    assert legs["call_short"] == "SPXW  260530C05300000"
    assert legs["call_long"] == "SPXW  260530C05350000"


def test_resolve_legs_missing_strike_raises():
    import pytest
    with pytest.raises(KeyError):
        tr.resolve_legs(CHAIN, "PCS", short_strike=9999, long_strike=5150)


def test_registry_add_union_for_osi_remove():
    reg = tr.TradeRegistry()
    reg.add({"trade_id": "t1", "strategy": "PCS",
             "legs": {"put_short": "A", "put_long": "B"}, "fired": set()})
    reg.add({"trade_id": "t2", "strategy": "PCS",
             "legs": {"put_short": "A", "put_long": "C"}, "fired": set()})  # shares A
    assert reg.legs_union() == {"A", "B", "C"}
    assert set(reg.for_osi("A")) == {("t1", "put_short"), ("t2", "put_short")}
    assert "t1" in reg
    reg.remove("t1")
    assert "t1" not in reg
    assert reg.legs_union() == {"A", "C"}
    reg.remove("nope")  # safe when absent


#############################################
# WHAT THE TRACKER FOLLOWS, DECIDED BEFORE ANY CHAIN IS FETCHED
#############################################
import math  # noqa: E402

import pytest  # noqa: E402


def spread(strategy="PCS", **over):
    body = {"symbol": "AAA", "expiration": "2026-10-09", "strategy": strategy,
            "short_strike": 100.0, "long_strike": 95.0, "entry_credit": 1.2}
    body.update(over)
    return body


@pytest.mark.parametrize("strategy", ["PCS", "CCS", "pcs", " ccs "])
def test_a_credit_spread_with_both_strikes_is_followed(strategy):
    assert tr.track_refusal(spread(strategy)) is None


def test_whole_number_strikes_and_credit_are_usable():
    # JSON and SQLite both hand back ints for whole numbers.
    assert tr.track_refusal(spread(short_strike=100, long_strike=95, entry_credit=1)) is None


@pytest.mark.parametrize("strategy", ["IC", "IRON_CONDOR"])
def test_an_iron_condor_with_four_strikes_is_followed_under_either_name(strategy):
    body = spread(strategy, call_short=110.0, call_long=115.0)
    assert tr.track_refusal(body) is None
    assert tr.tracked_strategy(strategy) == "IC"


@pytest.mark.parametrize("strategy", [
    "LONG_CALL", "LONG_PUT", "BULL_CALL", "BEAR_PUT", "BUTTERFLY_CALL",
    "BUTTERFLY_PUT", "CONDOR_CALL", "CONDOR_PUT", "SHORT_PUT", "COVERED_CALL",
    "", None, 5, True])
def test_any_other_structure_is_refused_by_name(strategy):
    reason = tr.track_refusal(spread(strategy))
    assert reason is not None and "PCS, CCS, IC" in reason
    assert tr.tracked_strategy(strategy) is None


@pytest.mark.parametrize("bad", [None, 0, -5.0, float("nan"), float("inf"), True, "100"])
def test_a_spread_without_a_usable_strike_is_refused(bad):
    assert "short_strike" in tr.track_refusal(spread(short_strike=bad))
    assert "long_strike" in tr.track_refusal(spread(long_strike=bad))


@pytest.mark.parametrize("bad", [None, 0, -0.48, float("nan"), True, "1.2"])
def test_a_spread_without_a_positive_credit_is_refused(bad):
    # The target and stop are fractions of the credit. A DEBIT is stored as a
    # negative credit, which is how prod's two rows looked.
    assert "entry_credit" in tr.track_refusal(spread(entry_credit=bad))


@pytest.mark.parametrize("field", ["symbol", "expiration"])
@pytest.mark.parametrize("bad", [None, "", "   ", 5])
def test_a_spread_with_no_symbol_or_expiration_is_refused(field, bad):
    # There is no chain to ask for; before this it was retried as an error.
    assert field in tr.track_refusal(spread(**{field: bad}))


def test_an_iron_condor_without_its_call_strikes_is_refused():
    reason = tr.track_refusal(spread("IC", call_short=None))
    assert "call_short" in reason and "call_long" in reason


def test_a_body_that_is_not_a_mapping_is_refused_not_raised():
    assert tr.track_refusal(None) is not None
    assert tr.track_refusal([]) is not None


#############################################
# WHAT THE RECONCILE LOOP REMEMBERS ABOUT A TRADE IT COULD NOT TRACK
#############################################

ERR = {"status": "error", "detail": "x"}


def gaps_of(a, n, **record_kw):
    """The waits, in whole 30-second steps, after each of ``n`` failures."""
    now, gaps = 0.0, []
    for _ in range(n):
        a.record("t", ERR, now=now, **record_kw)
        gap = 0
        while not a.due("t", now=now + gap):
            gap += 30
        gaps.append(gap)
        now += gap
    return gaps


def test_an_unseen_trade_is_due():
    a = tr.TrackAttempts()
    assert a.due("t", now=0.0) and a.last_key("t") is None


def test_a_refused_trade_is_never_due_again():
    a = tr.TrackAttempts()
    a.record("t", {"status": "skipped", "detail": "not followed"}, now=0.0)
    assert not a.due("t", now=10 ** 9) and a.last_key("t") == "not followed"


def test_a_failed_trade_waits_longer_each_time_up_to_the_cap():
    assert gaps_of(tr.TrackAttempts(base_sec=30, cap_sec=1800), 10) == [
        30, 60, 120, 240, 480, 960, 1800, 1800, 1800, 1800]


def test_one_failure_can_be_given_a_shorter_cap():
    assert gaps_of(tr.TrackAttempts(), 7, cap_sec=300) == [30, 60, 120, 240, 300, 300, 300]


@pytest.mark.parametrize("bad", [None, 0, -1, math.nan, math.inf, True, "300"])
def test_an_unusable_cap_for_one_failure_means_the_instances_own(bad):
    assert gaps_of(tr.TrackAttempts(), 8, cap_sec=bad)[-2:] == [1800, 1800]


def test_a_cap_below_one_interval_is_one_interval():
    assert gaps_of(tr.TrackAttempts(), 4, cap_sec=5) == [30, 30, 30, 30]


def test_a_very_long_run_of_failures_does_not_overflow():
    a = tr.TrackAttempts(base_sec=30, cap_sec=1800)
    for i in range(5000):
        a.record("t", ERR, now=float(i))
    assert not a.due("t", now=4999.0 + 1799) and a.due("t", now=4999.0 + 1800)


def test_the_stable_key_is_remembered_not_the_varying_text():
    a = tr.TrackAttempts()
    a.record("t", {"status": "error", "detail": "failed: id-123", "key": "failed"}, now=0.0)
    assert a.last_key("t") == "failed"
    a.record("u", {"status": "error", "detail": "plain"}, now=0.0)
    assert a.last_key("u") == "plain"                  # no key: the detail is the key


def test_success_forgets_and_a_later_failure_starts_over():
    a = tr.TrackAttempts(base_sec=30, cap_sec=1800)
    for now in (0.0, 30.0, 90.0):
        a.record("t", ERR, now=now)
    a.record("t", {"status": "ok"}, now=210.0)
    assert a.due("t", now=210.0) and a.last_key("t") is None
    a.record("t", ERR, now=300.0)
    assert a.due("t", now=330.0)                       # back to the first gap


def test_prune_keeps_only_the_trades_still_waiting():
    a = tr.TrackAttempts()
    for t in ("a", "b"):
        a.record(t, {"status": "skipped", "detail": "x"}, now=0.0)
    a.prune({"b", "c"})
    assert a.last_key("a") is None and a.last_key("b") == "x"


def test_counts_separate_the_refused_from_the_retried():
    a = tr.TrackAttempts()
    assert a.counts() == {"not_followed": 0, "failing": 0}
    a.record("a", {"status": "skipped", "detail": "x"}, now=0.0)
    a.record("b", ERR, now=0.0)
    a.record("c", ERR, now=0.0)
    assert a.counts() == {"not_followed": 1, "failing": 2}


@pytest.mark.parametrize("base, cap", [(0, 1800), (-1, 1800), (math.nan, 1800),
                                       (30, 10), (30, math.inf), ("x", 1800)])
def test_unusable_timings_fall_back_to_the_defaults(base, cap):
    assert gaps_of(tr.TrackAttempts(base_sec=base, cap_sec=cap), 8) == [
        30, 60, 120, 240, 480, 960, 1800, 1800]
