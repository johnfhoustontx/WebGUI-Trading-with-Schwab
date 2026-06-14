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
