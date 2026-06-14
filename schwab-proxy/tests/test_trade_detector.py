import trade_detector as td


def _state():
    return {
        "trade_id": "t1", "strategy": "PCS", "entry_credit": 1.50,
        "quantity": 1, "short_strike": 5200, "long_strike": 5150,
        "target_mid": 0.75, "stop_mid": 4.50,
        "fired": set(),
    }


def test_target_fires_when_mid_at_or_below_target():
    st = _state()
    legs = {"put_short": {"bid": 0.70, "ask": 0.80},   # mid 0.75
            "put_long":  {"bid": 0.00, "ask": 0.02}}   # mid 0.01 -> spread 0.74
    ev = td.evaluate(st, legs, underlying=5210.0, ts="2026-05-30T10:47:23-05:00")
    assert ev is not None
    assert ev["event_type"] == "target_hit"
    assert ev["terminal"] is True
    # Event must carry its trade_id so perf_writer's NOT NULL trade_id column
    # is satisfied (regression: events were dropped with a NULL trade_id).
    assert ev["trade_id"] == "t1"


def test_target_fires_only_once():
    st = _state()
    legs = {"put_short": {"bid": 0.70, "ask": 0.78}, "put_long": {"bid": 0.0, "ask": 0.02}}
    first = td.evaluate(st, legs, 5210.0, "t0")
    st["fired"].add(first["event_type"])
    second = td.evaluate(st, legs, 5210.0, "t1")
    assert second is None


def test_stop_fires_when_mid_at_or_above_stop():
    st = _state()
    legs = {"put_short": {"bid": 5.00, "ask": 5.10}, "put_long": {"bid": 0.50, "ask": 0.60}}
    # short mid 5.05 - long mid 0.55 = 4.50 >= stop_mid
    ev = td.evaluate(st, legs, 5180.0, "t0")
    assert ev["event_type"] == "stop_hit"
    assert ev["terminal"] is True


def test_strike_test_non_terminal():
    st = _state()
    legs = {"put_short": {"bid": 1.40, "ask": 1.50}, "put_long": {"bid": 0.40, "ask": 0.50}}
    ev = td.evaluate(st, legs, underlying=5200.0, ts="t0")
    assert ev["event_type"] == "strike_test"
    assert ev["terminal"] is False


def test_unquoted_leg_returns_none():
    st = _state()
    legs = {"put_short": {"bid": None, "ask": None}, "put_long": {"bid": 0.4, "ask": 0.5}}
    assert td.evaluate(st, legs, 5200.0, "t0") is None
