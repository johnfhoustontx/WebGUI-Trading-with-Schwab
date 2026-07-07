"""Tests for scoring/order_flow.py — pure aggressor-flow classifier (quote rule + CVD)."""
from scoring.order_flow import (
    classify_tick,
    aggregate_flow,
    flow_aggression_component,
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
