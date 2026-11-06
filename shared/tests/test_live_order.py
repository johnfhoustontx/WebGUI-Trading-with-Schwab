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
    ([leg("call", "long", 95), leg("put", "long", 95)], "STRADDLE", True),
    ([leg("call", "short", 105), leg("put", "short", 95)], "STRANGLE", False),
    ([leg("call", "long", 95), leg("put", "long", 105)], "CUSTOM", True),     # guts
    ([leg("call", "long", 95), leg("call", "short", 100, qty=2),
      leg("call", "long", 105)], "BUTTERFLY", True),
    ([leg("put", "short", 90), leg("put", "long", 95, qty=2),
      leg("put", "short", 100)], "BUTTERFLY", False),
    ([leg("call", "long", 95), leg("call", "short", 100, qty=2),
      leg("call", "long", 110)], "CUSTOM", None),                              # broken wing: the mids decide
    ([leg("call", "long", 90), leg("call", "short", 95), leg("call", "short", 100),
      leg("call", "long", 105)], "CONDOR", True),
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
    assert names == {"datetime", "math", "re", "shared"}
    assert "from shared import symbols as _symbols" in pathlib.Path(lo.__file__).read_text(encoding="utf-8")


# ── Phase 1 ──────────────────────────────────────────────────────────────────

def test_exits_by_structure():
    """Phase 0: a stop passes on a single option and is refused on any spread."""
    assert lo.exit_variant(LONG_CALL) == "bracket"
    assert lo.exit_variant(SHORT_PUT) == "bracket"
    assert lo.exit_variant(DEBIT_CALL_VERTICAL) == "target"
    assert lo.exit_variant(IRON_CONDOR) == "target"


@pytest.mark.parametrize("legs, limit, debit, want", [
    (LONG_CALL, 1.80, True, 180.0),
    (DEBIT_CALL_VERTICAL, 1.50, True, 150.0),
    (CREDIT_PUT_VERTICAL, 0.90, False, 410.0),        # 5 wide - 0.90
    (IRON_CONDOR, 1.40, False, 360.0),                # 5 wide wings - 1.40
    (REVERSE_IC, 1.00, True, 100.0),
    (SHORT_PUT, 1.20, False, 8880.0),                 # 90 strike to zero
])
def test_max_loss_per_contract(legs, limit, debit, want):
    assert lo.max_loss_per_contract(legs, limit, debit) == want


def test_a_naked_short_call_has_no_bounded_loss():
    assert lo.max_loss_per_contract([leg("call", "short", 105)], 1.0, False) is None


@pytest.mark.parametrize("risk, want", [
    (230.0, 4), (1000.0, 1), (1400.0, 1), (999.99, 1), (10.0, 50),
    (None, 1), (0, 1), (float("inf"), 1), (float("nan"), 1)])
def test_default_contracts(risk, want):
    """The operator's rule: up to $1,000 of risk, else 1; never past the ceiling."""
    assert lo.default_contracts(risk, 1000.0, 50) == want


def test_max_profit_per_contract():
    assert lo.max_profit_per_contract(DEBIT_CALL_VERTICAL, 1.50, True) == 350.0
    assert lo.max_profit_per_contract(CREDIT_PUT_VERTICAL, 0.90, False) == 90.0
    assert lo.max_profit_per_contract(IRON_CONDOR, 1.40, False) == 140.0
    assert lo.max_profit_per_contract(LONG_CALL, 1.80, True) is None


# ── the names both tiers share ───────────────────────────────────────────────

def test_view_keys_take_only_their_own_ids():
    assert lo.ticket_key("a" * 32) == "cache:live_order:ticket:" + "a" * 32
    assert lo.quote_key("ab12cd34") == "cache:live_order:quote:ab12cd34"
    assert lo.STREAM == "cmd:live_order"
    for bad in ("A" * 32, "a" * 31, "a" * 32 + ":x", None, ""):
        with pytest.raises(ValueError):
            lo.ticket_key(bad)
    for bad in ("ab:cd", "a b", "", None, "x" * 41):
        with pytest.raises(ValueError):
            lo.quote_key(bad)


# ── reading Schwab's answers ─────────────────────────────────────────────────

def test_scrub_cuts_every_account_key_to_four_digits():
    answer = {"orderStrategy": {"accountNumber": "12345678", "legs": [
        {"accountId": 99991191}, {"hashValue": "ABCDEF", "x": 1}]}, "accountHash": None}
    out = lo.scrub(answer)
    assert out["orderStrategy"]["accountNumber"] == "…5678"
    assert out["orderStrategy"]["legs"] == [{"accountId": "…1191"},
                                           {"hashValue": "…CDEF", "x": 1}]
    assert out["accountHash"] is None


def test_preview_verdict():
    ok = {"schwab": {"orderValidationResult": {"warns": [{"message": "wide"}]}}}
    assert lo.preview_verdict(200, ok) == ("accepted", ["warn: wide"])
    no = {"schwab": {"orderValidationResult": {"rejects": [{"activityMessage": "level"}]}}}
    assert lo.preview_verdict(200, no) == ("rejected", ["reject: level"])
    assert lo.preview_verdict(403, {"detail": "off"}) == ("error 403", ["off"])


CHAIN = {"callExpDateMap": {"2026-11-06:1": {"100.0": [{"symbol": "C100", "bid": 1.0, "ask": 1.2}]}},
         "putExpDateMap": {"2026-11-06:1": {"452.5": [{"symbol": "P452", "bid": 0, "ask": 0.1}]}}}


def test_find_contract_and_its_mid():
    c = lo.find_contract(CHAIN, "call", "2026-11-06", 100)
    assert c["symbol"] == "C100" and lo.contract_mid(c) == pytest.approx(1.1)
    p = lo.find_contract(CHAIN, "put", "2026-11-06", 452.5)
    assert p["symbol"] == "P452" and lo.contract_mid(p) is None      # no bid
    assert lo.find_contract(CHAIN, "call", "2026-11-13", 100) is None
    assert lo.find_contract(CHAIN, "call", "2026-11-06", 101) is None
    assert lo.find_contract(CHAIN, "stock", "2026-11-06", 100) is None
    assert lo.contract_mid({"bid": float("nan"), "ask": 1}) is None
    assert lo.contract_mid({"bid": True, "ask": 1}) is None


def test_net_mid_reads_the_side_off_the_legs():
    assert lo.net_mid(DEBIT_CALL_VERTICAL, [2.5, 1.0]) == (1.5, True)
    assert lo.net_mid(CREDIT_PUT_VERTICAL, [1.2, 0.3]) == (pytest.approx(0.9), False)
    assert lo.net_mid(DEBIT_CALL_VERTICAL, [1.0, None]) is None
    assert lo.net_mid(DEBIT_CALL_VERTICAL, [1.0, 1.0]) is None
    assert lo.net_mid(DEBIT_CALL_VERTICAL, [1.0]) is None


# ── Phase 2 ──────────────────────────────────────────────────────────────────

FAR = "2026-12-18"


def far(kind, side, strike, qty=1):
    return {**leg(kind, side, strike, qty), "expiration": FAR,
            "symbol": sym("INTC", kind[0].upper(), strike, "261218")}


def test_calendars_and_diagonals_bound_their_loss_at_the_near_expiry():
    cal = [leg("call", "short", 100), far("call", "long", 100)]
    assert lo.complex_type(cal) == "CALENDAR" and lo.is_debit(cal) is True
    assert lo.max_loss_per_contract(cal, 1.20, True) == 120.0      # the debit
    assert lo.max_profit_per_contract(cal, 1.20, True) is None     # volatility decides
    # Long the WORSE strike: the width can be lost on top of the debit.
    diag = [leg("call", "short", 100), far("call", "long", 105)]
    assert lo.complex_type(diag) == "DIAGONAL"
    assert lo.max_loss_per_contract(diag, 0.50, True) == 550.0
    # The sold leg outlives the bought one: never sent.
    with pytest.raises(ValueError, match="outlives"):
        lo.max_loss_per_contract([leg("call", "long", 100), far("call", "short", 100)],
                                 1.0, False)


def test_merge_legs_makes_the_unit_schwab_takes():
    legs, lots = lo.merge_legs([
        {"kind": "call", "side": "long", "strike": 95, "qty": 3, "expiration": EXP},
        {"kind": "call", "side": "short", "strike": 100, "qty": 3, "expiration": EXP},
        {"kind": "call", "side": "short", "strike": 100.0, "qty": 3, "expiration": EXP},
        {"kind": "call", "side": "long", "strike": 105, "qty": 3, "expiration": EXP}])
    assert lots == 3
    assert sorted((l["strike"], l["side"], l["qty"]) for l in legs) == \
        [(95, "long", 1), (100, "short", 2), (105, "long", 1)]


TODAY = __import__("datetime").date(2026, 11, 5)


def _spec(**kw):
    s = {"symbol": "intc", "label": "INTC Bull Call Spread <b>",
         "price": 1.5, "legs": [
             {"kind": "Call", "side": "LONG", "strike": 95, "qty": 2, "expiration": EXP},
             {"kind": "call", "side": "short", "strike": 100, "qty": 2.0,
              "expiration": EXP + "T00:00:00"}]}
    s.update(kw)
    return s


def test_read_spec_checks_and_normalises_a_panels_trade():
    got = lo.read_spec(_spec(), TODAY)
    assert got["symbol"] == "INTC" and got["price"] == 1.5
    assert got["label"] == "INTC Bull Call Spread b"            # no markup survives
    assert got["legs"] == [
        {"kind": "call", "side": "long", "strike": 95, "qty": 1, "expiration": EXP},
        {"kind": "call", "side": "short", "strike": 100, "qty": 1, "expiration": EXP}]
    assert lo.read_spec(_spec(price=-1), TODAY)["price"] is None


@pytest.mark.parametrize("bad, why", [
    ({"symbol": "BAD:KEY"}, "ticker"),
    ({"symbol": "/ES"}, "ticker"),
    ({"legs": []}, "legs"),
    ({"legs": [{"kind": "stock", "side": "long", "strike": 1, "expiration": EXP}]}, "kind"),
    ({"legs": [{"kind": "call", "side": "long", "strike": 95, "expiration": "2026-11-04"}]},
     "expired"),
    ({"legs": [{"kind": "call", "side": "short", "strike": 95, "expiration": EXP}]},
     "no limit"),
    ({"legs": [{"kind": "call", "side": "long", "strike": 95, "qty": 0.5,
                "expiration": EXP}]}, "qty"),
    ("not a dict", "cannot be read"),
])
def test_read_spec_refuses(bad, why):
    spec = bad if isinstance(bad, str) else _spec(**bad)
    with pytest.raises(ValueError, match=why):
        lo.read_spec(spec, TODAY)


def test_a_sold_put_is_sendable_and_its_risk_is_the_strike():
    got = lo.read_spec(_spec(legs=[{"kind": "put", "side": "short", "strike": 90,
                                    "expiration": EXP}]), TODAY)
    assert lo.max_loss_per_contract(got["legs"], 1.0, False) == 8900.0
