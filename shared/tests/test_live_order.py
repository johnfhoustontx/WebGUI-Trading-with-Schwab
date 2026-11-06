"""shared/live_order.py: legs -> the order JSON Schwab's Trader API takes.

Phase 0 sends these only to ``previewOrder``, which places nothing. What Schwab
answers is recorded in the design doc; these tests pin what the builder writes.
"""
import ast
import pathlib

import pytest

from shared import live_order as lo

TICKS = {"under_3": 0.05, "from_3": 0.10, "penny_under_3": 0.01,
         "penny_from_3": 0.05, "penny": ["SPY", "QQQ", "IWM"]}
DEBIT_PCTS = {"target_pct": 100.0, "stop_pct": 50.0}
CREDIT_PCTS = {"target_pct": 50.0, "stop_pct": 100.0}
EXP = "2026-11-06"


def sym(root, right, strike, exp="261106"):
    return f"{root:<6}{exp}{right}{int(round(strike * 1000)):08d}"


def leg(kind, side, strike, qty=1, root="INTC"):
    return {"kind": kind, "side": side, "strike": strike, "qty": qty,
            "expiration": EXP, "symbol": sym(root, kind[0].upper(), strike)}


LONG_CALL = [leg("call", "long", 100)]
SHORT_PUT = [leg("put", "short", 90)]
DEBIT_CALL_VERTICAL = [leg("call", "long", 95), leg("call", "short", 100)]
CREDIT_PUT_VERTICAL = [leg("put", "short", 90), leg("put", "long", 85)]
IRON_CONDOR = [leg("put", "long", 80), leg("put", "short", 85),
               leg("call", "short", 105), leg("call", "long", 110)]
REVERSE_IC = [leg("put", "short", 80), leg("put", "long", 85),
              leg("call", "long", 105), leg("call", "short", 110)]
BACKSPREAD = [leg("call", "short", 95), leg("call", "long", 100, qty=2)]


def test_option_symbol_shape():
    assert sym("SPXW", "C", 5800) == "SPXW  261106C05800000"
    assert lo.OPTION_SYMBOL_RE.match("SPXW  261106C05800000")
    assert lo.OPTION_SYMBOL_RE.match("SPY   261106P00452500")
    for bad in ("SPXW 261106C05800000", "spy   261106P00452500", "SPY261106P00452500",
                "SPY   261106X00452500", ""):
        assert not lo.OPTION_SYMBOL_RE.match(bad), bad


@pytest.mark.parametrize("legs, ctype, debit", [
    (LONG_CALL, "NONE", True),
    (SHORT_PUT, "NONE", False),
    (DEBIT_CALL_VERTICAL, "VERTICAL", True),
    (CREDIT_PUT_VERTICAL, "VERTICAL", False),
    ([leg("put", "long", 100), leg("put", "short", 95)], "VERTICAL", True),
    ([leg("call", "short", 95), leg("call", "long", 100)], "VERTICAL", False),
    (IRON_CONDOR, "IRON_CONDOR", False),
    (REVERSE_IC, "IRON_CONDOR", True),
    (BACKSPREAD, "CUSTOM", None),
    ([leg("call", "long", 95), leg("put", "long", 95)], "CUSTOM", True),
])
def test_structure_and_side_come_from_the_legs(legs, ctype, debit):
    assert lo.complex_type(legs) == ctype
    assert lo.is_debit(legs) is debit


@pytest.mark.parametrize("price, root, want", [
    (1.234, "INTC", 1.25), (1.22, "INTC", 1.20), (3.04, "INTC", 3.00),
    (3.06, "INTC", 3.10), (1.234, "SPY", 1.23), (3.02, "SPY", 3.00),
    (0.01, "INTC", 0.05), (1.234, "$SPX", 1.25),
])
def test_prices_land_on_their_step(price, root, want):
    assert lo.round_to_tick(price, root, TICKS) == want


def test_exits_are_priced_off_the_limit():
    assert lo.exit_prices(1.50, True, DEBIT_PCTS, "INTC", TICKS) == (3.00, 0.75)
    assert lo.exit_prices(1.00, False, CREDIT_PCTS, "INTC", TICKS) == (0.50, 2.00)
    # never below one step
    assert lo.exit_prices(0.05, False, CREDIT_PCTS, "INTC", TICKS)[0] == 0.05


def _build(legs, variant="bracket", **kw):
    pcts = kw.pop("pcts", None)
    if pcts is None:
        d = lo.is_debit(legs) if lo.is_debit(legs) is not None else kw.get("debit")
        pcts = DEBIT_PCTS if d else CREDIT_PCTS
    return lo.schwab_order(legs, contracts=kw.pop("contracts", 3),
                           limit=kw.pop("limit", 1.50), variant=variant,
                           pcts=pcts, root="INTC", ticks=TICKS, **kw)


def test_the_opening_order_alone():
    o = _build(DEBIT_CALL_VERTICAL, "open")
    assert o == {
        "orderStrategyType": "SINGLE", "orderType": "NET_DEBIT", "price": "1.50",
        "duration": "DAY", "session": "NORMAL",
        "complexOrderStrategyType": "VERTICAL",
        "orderLegCollection": [
            {"instruction": "BUY_TO_OPEN", "quantity": 3,
             "instrument": {"assetType": "OPTION", "symbol": "INTC  261106C00095000"}},
            {"instruction": "SELL_TO_OPEN", "quantity": 3,
             "instrument": {"assetType": "OPTION", "symbol": "INTC  261106C00100000"}},
        ]}


def test_a_single_leg_is_a_limit_order_with_no_complex_type():
    o = _build(LONG_CALL, "open", contracts=1)
    assert o["orderType"] == "LIMIT"
    assert "complexOrderStrategyType" not in o
    assert o["orderLegCollection"][0]["instruction"] == "BUY_TO_OPEN"


def test_the_bracket_is_trigger_then_oco_target_and_stop():
    o = _build(DEBIT_CALL_VERTICAL, "bracket")
    assert o["orderStrategyType"] == "TRIGGER"
    (oco,) = o["childOrderStrategies"]
    assert oco["orderStrategyType"] == "OCO"
    target, stop = oco["childOrderStrategies"]
    assert target["orderType"] == "NET_CREDIT" and target["price"] == "3.00"
    assert stop["orderType"] == "STOP" and stop["stopPrice"] == "0.75"
    for child in (target, stop):
        assert child["duration"] == "GOOD_TILL_CANCEL"
        assert child["complexOrderStrategyType"] == "VERTICAL"
        assert [l["instruction"] for l in child["orderLegCollection"]] == [
            "SELL_TO_CLOSE", "BUY_TO_CLOSE"]
        assert [l["quantity"] for l in child["orderLegCollection"]] == [3, 3]


def test_a_credit_bracket_closes_for_a_debit():
    o = _build(IRON_CONDOR, "bracket", limit=1.40)
    assert o["orderType"] == "NET_CREDIT" and o["price"] == "1.40"
    target, stop = o["childOrderStrategies"][0]["childOrderStrategies"]
    assert target["orderType"] == "NET_DEBIT" and target["price"] == "0.70"
    assert stop["stopPrice"] == "2.80"
    assert [l["instruction"] for l in o["orderLegCollection"]] == [
        "BUY_TO_OPEN", "SELL_TO_OPEN", "SELL_TO_OPEN", "BUY_TO_OPEN"]


def test_the_target_variant_has_one_child_and_no_stop():
    o = _build(CREDIT_PUT_VERTICAL, "target", limit=0.90)
    (child,) = o["childOrderStrategies"]
    assert child["orderStrategyType"] == "SINGLE"
    assert child["price"] == "0.45"


def test_day_exits():
    o = _build(LONG_CALL, "bracket", exit_tif="DAY")
    target, stop = o["childOrderStrategies"][0]["childOrderStrategies"]
    assert target["duration"] == stop["duration"] == "DAY"


def test_a_ratio_leg_scales_with_the_contracts():
    o = _build(BACKSPREAD, "open", debit=True, contracts=2, limit=0.40)
    assert [l["quantity"] for l in o["orderLegCollection"]] == [2, 4]
    assert o["complexOrderStrategyType"] == "CUSTOM"


def test_a_mixed_custom_needs_its_side_said():
    with pytest.raises(ValueError, match="debit"):
        lo.schwab_order(BACKSPREAD, contracts=1, limit=0.4, variant="open",
                        root="INTC", ticks=TICKS)


def test_a_side_that_contradicts_the_legs_is_refused():
    with pytest.raises(ValueError, match="contradicts"):
        _build(DEBIT_CALL_VERTICAL, "open", debit=False)


@pytest.mark.parametrize("contracts", [0, -1, 1.0, True, 1001, "2"])
def test_bad_contracts_are_refused(contracts):
    with pytest.raises(ValueError):
        _build(LONG_CALL, "open", contracts=contracts)


@pytest.mark.parametrize("limit", [0, -1.0, float("nan"), float("inf"), "1.50", None])
def test_bad_limits_are_refused(limit):
    with pytest.raises(ValueError):
        _build(LONG_CALL, "open", limit=limit)


@pytest.mark.parametrize("legs", [
    [], [leg("call", "long", 100)] * 5,
    [{**leg("call", "long", 100), "symbol": "INTC 100 C"}],
    [{**leg("call", "long", 100), "kind": "stock"}],
    [{**leg("call", "long", 100), "side": "buy"}],
    [{**leg("call", "long", 100), "qty": 0}],
    [{**leg("call", "long", 100), "strike": float("nan")}],
])
def test_unreadable_legs_are_refused(legs):
    with pytest.raises(ValueError):
        _build(legs, "open", debit=True)


def test_an_unknown_variant_is_refused():
    with pytest.raises(ValueError):
        _build(LONG_CALL, "market")


def test_the_module_is_pure():
    tree = ast.parse(pathlib.Path(lo.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module)
    assert names == {"math", "re"}
