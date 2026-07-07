"""Tests for scoring/order_flow.py — pure aggressor-flow classifier (quote rule + CVD)."""
from scoring.order_flow import (
    classify_tick,
    aggregate_flow,
    flow_aggression_component,
    osi_option_type,
    aggregate_option_flow,
    option_flow_component,
    MIN_TICKS_FULL,
)


# ---------- classify_tick ----------

def test_last_at_ask_is_buyer():
    assert classify_tick(100.0, 99.5, 100.0) == 1


def test_last_above_ask_is_buyer():
    assert classify_tick(100.5, 99.5, 100.0) == 1


def test_last_at_bid_is_seller():
    assert classify_tick(99.5, 99.5, 100.0) == -1


def test_last_below_bid_is_seller():
    assert classify_tick(99.0, 99.5, 100.0) == -1


def test_between_uptick_is_buyer():
    # 99.75 strictly inside [99.5, 100.0]; prev_last 99.6 -> uptick
    assert classify_tick(99.75, 99.5, 100.0, prev_last=99.6) == 1


def test_between_downtick_is_seller():
    assert classify_tick(99.75, 99.5, 100.0, prev_last=99.9) == -1


def test_between_flat_is_zero():
    assert classify_tick(99.75, 99.5, 100.0, prev_last=99.75) == 0


def test_between_no_prev_is_zero():
    assert classify_tick(99.75, 99.5, 100.0, prev_last=None) == 0


def test_missing_last_is_zero():
    assert classify_tick(None, 99.5, 100.0, prev_last=99.6) == 0
    assert classify_tick("x", 99.5, 100.0, prev_last=99.6) == 0


def test_missing_quotes_falls_back_to_tick_test():
    # bid/ask both missing but last & prev present -> tick test
    assert classify_tick(101.0, None, None, prev_last=100.0) == 1
    assert classify_tick(99.0, None, None, prev_last=100.0) == -1
    assert classify_tick(100.0, None, None, prev_last=100.0) == 0


def test_never_raises_on_garbage():
    assert classify_tick("a", "b", "c", prev_last="d") == 0


# ---------- aggregate_flow ----------

def test_mostly_buys():
    ticks = [
        {"last": 100.0, "size": 10, "bid": 99.5, "ask": 100.0},   # +1
        {"last": 100.2, "size": 5, "bid": 99.6, "ask": 100.1},    # +1
        {"last": 99.6, "size": 3, "bid": 99.6, "ask": 100.2},     # -1
    ]
    r = aggregate_flow(ticks)
    assert r["buy_vol"] == 15.0
    assert r["sell_vol"] == 3.0
    assert r["cvd"] == 12.0
    assert r["aggressor_ratio"] > 0
    assert r["n"] == 3


def test_mostly_sells():
    ticks = [
        {"last": 99.5, "size": 10, "bid": 99.5, "ask": 100.0},    # -1
        {"last": 99.4, "size": 8, "bid": 99.4, "ask": 100.0},     # -1
        {"last": 100.0, "size": 2, "bid": 99.4, "ask": 100.0},    # +1
    ]
    r = aggregate_flow(ticks)
    assert r["sell_vol"] == 18.0
    assert r["buy_vol"] == 2.0
    assert r["cvd"] == -16.0
    assert r["aggressor_ratio"] < 0
    assert r["n"] == 3


def test_balanced():
    ticks = [
        {"last": 100.0, "size": 5, "bid": 99.5, "ask": 100.0},    # +1
        {"last": 99.5, "size": 5, "bid": 99.5, "ask": 100.0},     # -1
    ]
    r = aggregate_flow(ticks)
    assert r["aggressor_ratio"] == 0.0
    assert r["cvd"] == 0.0


def test_empty():
    r = aggregate_flow([])
    assert r == {"buy_vol": 0.0, "sell_vol": 0.0, "cvd": 0.0,
                 "aggressor_ratio": None, "n": 0}


def test_missing_sizes_default_to_one():
    ticks = [
        {"last": 100.0, "bid": 99.5, "ask": 100.0},   # +1, size 1.0
        {"last": 100.1, "bid": 99.6, "ask": 100.1},   # +1, size 1.0
        {"last": 99.6, "size": None, "bid": 99.6, "ask": 100.2},  # -1, size 1.0
    ]
    r = aggregate_flow(ticks)
    assert r["buy_vol"] == 2.0
    assert r["sell_vol"] == 1.0
    assert r["cvd"] == 1.0
    assert r["n"] == 3


def test_malformed_tick_does_not_crash():
    ticks = [
        {"last": 100.0, "size": 5, "bid": 99.5, "ask": 100.0},   # +1
        "not a dict",
        {"last": None, "size": 3, "bid": 99.5, "ask": 100.0},    # 0
        {"last": 99.5, "size": "bad", "bid": 99.5, "ask": 100.0},  # -1, size 0
    ]
    r = aggregate_flow(ticks)
    # never raises; buy side has 5, the -1 tick had a bad size -> 0 volume
    assert r["buy_vol"] == 5.0
    assert isinstance(r["n"], int)


def test_carries_prev_last_across_sequence():
    # all inside the spread -> pure tick test using the running prev_last
    ticks = [
        {"last": 99.75, "size": 1, "bid": 99.5, "ask": 100.0},   # prev None -> 0
        {"last": 99.80, "size": 1, "bid": 99.5, "ask": 100.0},   # uptick -> +1
        {"last": 99.70, "size": 1, "bid": 99.5, "ask": 100.0},   # downtick -> -1
    ]
    r = aggregate_flow(ticks)
    assert r["buy_vol"] == 1.0
    assert r["sell_vol"] == 1.0
    assert r["cvd"] == 0.0


# ---------- flow_aggression_component ----------

def test_strong_buy_positive_score():
    agg = aggregate_flow([{"last": 100.0, "size": 10, "bid": 99.5, "ask": 100.0}] * 30)
    score, conf = flow_aggression_component(agg)
    assert score > 0
    assert conf == 1.0


def test_confidence_rises_with_n():
    small = flow_aggression_component({"aggressor_ratio": 1.0, "n": 5})
    big = flow_aggression_component({"aggressor_ratio": 1.0, "n": MIN_TICKS_FULL})
    assert big[1] > small[1]
    assert big[1] == 1.0


def test_ratio_none_returns_zeros():
    assert flow_aggression_component({"aggressor_ratio": None, "n": 0}) == (0.0, 0.0)
    assert flow_aggression_component(aggregate_flow([])) == (0.0, 0.0)


def test_n_zero_returns_zeros():
    assert flow_aggression_component({"aggressor_ratio": 0.8, "n": 0}) == (0.0, 0.0)


def test_score_clamped():
    score, _ = flow_aggression_component({"aggressor_ratio": 5.0, "n": 30})
    assert score == 1.0
    score, _ = flow_aggression_component({"aggressor_ratio": -5.0, "n": 30})
    assert score == -1.0


def test_never_raises_on_garbage_component():
    assert flow_aggression_component({}) == (0.0, 0.0)
    assert flow_aggression_component({"aggressor_ratio": "x", "n": "y"}) == (0.0, 0.0)


# ---------- osi_option_type ----------

def test_osi_type_call():
    # Schwab OSI: 6-char space-padded root + YYMMDD + C/P + 8-digit strike.
    assert osi_option_type("SPY   260717C00500000") == "C"


def test_osi_type_put():
    assert osi_option_type("QQQ   260717P00450000") == "P"


def test_osi_type_no_padding():
    # A compact OSI with no root padding still parses off the date+type+strike tail.
    assert osi_option_type("A260717C00500000") == "C"


def test_osi_type_lowercase_normalized():
    assert osi_option_type("spy   260717p00500000") == "P"


def test_osi_type_garbage_is_none():
    assert osi_option_type("SPY") is None
    assert osi_option_type("") is None
    assert osi_option_type("not an osi") is None
    assert osi_option_type(None) is None
    assert osi_option_type(12345) is None
    # equity-style symbol (no date/type/strike tail) -> None
    assert osi_option_type("SPY   260717X00500000") is None


# ---------- aggregate_option_flow ----------

_CALL = "SPY   260717C00500000"
_PUT = "SPY   260717P00500000"


def test_option_flow_put_buying_is_bearish():
    """Puts lifted at the ask (protection buying) → signal < 0 (BEARISH)."""
    ticks = [{"osi": _PUT, "last": 5.0, "size": 10, "bid": 4.9, "ask": 5.0}
             for _ in range(6)]
    r = aggregate_option_flow(ticks)
    assert r["put_buy"] == 60.0
    assert r["call_buy"] == 0.0
    assert r["signal"] is not None and r["signal"] < 0
    assert r["n"] == 6


def test_option_flow_call_buying_is_bullish():
    """Calls lifted at the ask → signal > 0 (BULLISH)."""
    ticks = [{"osi": _CALL, "last": 5.0, "size": 10, "bid": 4.9, "ask": 5.0}
             for _ in range(6)]
    r = aggregate_option_flow(ticks)
    assert r["call_buy"] == 60.0
    assert r["signal"] > 0
    assert r["n"] == 6


def test_option_flow_balanced_near_zero():
    """Equal call-buying and put-buying nets ~0."""
    ticks = ([{"osi": _CALL, "last": 5.0, "size": 5, "bid": 4.9, "ask": 5.0}] * 4
             + [{"osi": _PUT, "last": 5.0, "size": 5, "bid": 4.9, "ask": 5.0}] * 4)
    r = aggregate_option_flow(ticks)
    assert r["signal"] == 0.0


def test_option_flow_prev_last_carried_per_osi():
    """prev_last is tracked PER OSI — and this test DISCRIMINATES a shared global
    prev_last from the per-OSI map. The CALL trade interleaved between the PUT's
    two trades is priced ABOVE the PUT's first, so:

      * per-OSI:  PUT#2 (4.96) tick-tests vs the PUT's OWN prior 4.95 -> UP  -> +1
                  (put_buy),  net signal -1 (bearish).
      * global:   PUT#2 (4.96) tick-tests vs the shared prior 4.99 (the CALL) ->
                  DOWN -> -1 (put_sell), net signal +1 (bullish).

    All trades are strictly mid-spread (bid<last<ask) so the tick test — not the
    quote rule — drives every classification. A regression to a single global
    prev_last would flip these assertions.
    """
    b, a = 4.90, 5.00   # strictly inside the spread -> tick test decides sign
    ticks = [
        {"osi": _PUT,  "last": 4.95, "size": 1, "bid": b, "ask": a},  # prev None -> 0
        {"osi": _CALL, "last": 4.99, "size": 1, "bid": b, "ask": a},  # prev None -> 0 (global prev now 4.99)
        {"osi": _PUT,  "last": 4.96, "size": 1, "bid": b, "ask": a},  # per-OSI 4.96>4.95 UP; global 4.96<4.99 DOWN
    ]
    r = aggregate_option_flow(ticks)
    # ONLY the per-OSI implementation produces these (global -> put_sell=1, signal +1).
    assert r["put_buy"] == 1.0
    assert r["put_sell"] == 0.0
    assert r["call_buy"] == 0.0 and r["call_sell"] == 0.0
    assert r["signal"] == -1.0


def test_option_flow_untaggable_osi_skipped():
    """A tick whose OSI can't be typed is skipped (not counted, no volume)."""
    ticks = [
        {"osi": _CALL, "last": 5.0, "size": 3, "bid": 4.9, "ask": 5.0},   # +1 call buy
        {"osi": "GARBAGE", "last": 5.0, "size": 9, "bid": 4.9, "ask": 5.0},
    ]
    r = aggregate_option_flow(ticks)
    assert r["n"] == 1
    assert r["call_buy"] == 3.0


def test_option_flow_missing_size_defaults_one():
    ticks = [{"osi": _CALL, "last": 5.0, "bid": 4.9, "ask": 5.0} for _ in range(3)]
    r = aggregate_option_flow(ticks)
    assert r["call_buy"] == 3.0     # 3 × 1.0


def test_option_flow_empty_signal_none():
    r = aggregate_option_flow([])
    assert r == {"call_buy": 0.0, "call_sell": 0.0, "put_buy": 0.0,
                 "put_sell": 0.0, "n": 0, "signal": None}


def test_option_flow_never_raises_on_garbage():
    r = aggregate_option_flow(["x", {"osi": _CALL, "last": None, "bid": 4.9, "ask": 5.0},
                               42])
    assert isinstance(r["n"], int)
    # the sole taggable tick had last None -> indeterminate -> no volume, signal None
    assert r["signal"] is None


# ---------- option_flow_component ----------

def test_option_flow_component_bullish_positive():
    agg = aggregate_option_flow(
        [{"osi": _CALL, "last": 5.0, "size": 10, "bid": 4.9, "ask": 5.0}] * MIN_TICKS_FULL)
    score, conf = option_flow_component(agg)
    assert score > 0        # net call-buying -> positive, NO flip
    assert conf == 1.0


def test_option_flow_component_bearish_negative():
    agg = aggregate_option_flow(
        [{"osi": _PUT, "last": 5.0, "size": 10, "bid": 4.9, "ask": 5.0}] * 10)
    score, _ = option_flow_component(agg)
    assert score < 0        # net put-buying -> negative (bearish)


def test_option_flow_component_confidence_rises_with_n():
    small = option_flow_component({"signal": 1.0, "n": 5})
    big = option_flow_component({"signal": 1.0, "n": MIN_TICKS_FULL})
    assert big[1] > small[1]
    assert big[1] == 1.0


def test_option_flow_component_none_returns_zeros():
    assert option_flow_component({"signal": None, "n": 0}) == (0.0, 0.0)
    assert option_flow_component({"signal": 0.8, "n": 0}) == (0.0, 0.0)
    assert option_flow_component({}) == (0.0, 0.0)
    assert option_flow_component("x") == (0.0, 0.0)


def test_option_flow_component_clamped():
    assert option_flow_component({"signal": 5.0, "n": 30})[0] == 1.0
    assert option_flow_component({"signal": -5.0, "n": 30})[0] == -1.0
