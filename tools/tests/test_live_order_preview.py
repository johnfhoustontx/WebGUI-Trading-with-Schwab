"""``tools/live_order_preview.py``: Phase 0's preview matrix.

The chain-reading and answer-reading parts are pure and tested here on a
synthetic chain. Nothing here reaches the proxy or Schwab.
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from shared import live_order  # noqa: E402
from tools import live_order_preview as tool  # noqa: E402

TODAY = dt.date(2026, 11, 5)


def _c(right, strike, bid, ask, exp="261106"):
    return [{"symbol": f"INTC  {exp}{right}{int(strike * 1000):08d}",
             "bid": bid, "ask": ask, "strikePrice": strike}]


def _chain(spot=100.0, key="2026-11-06:1"):
    calls = {f"{k:.1f}": _c("C", k, b, b + 0.1) for k, b in
             [(95, 5.5), (100, 2.0), (101, 1.5), (102, 1.0), (103, 0.6), (104, 0.3)]}
    puts = {f"{k:.1f}": _c("P", k, b, b + 0.1) for k, b in
            [(96, 0.3), (97, 0.6), (98, 1.0), (99, 1.5), (100, 2.0), (105, 5.5)]}
    return {"underlyingPrice": spot, "callExpDateMap": {key: calls},
            "putExpDateMap": {key: puts}}


def test_the_nearest_expiry_in_the_window():
    chain = _chain()
    chain["callExpDateMap"]["2026-11-05:0"] = {}
    chain["putExpDateMap"]["2026-11-05:0"] = {}
    assert tool.nearest_expiry(chain, TODAY) == "2026-11-06:1"
    far = _chain(key="2026-11-20:15")
    assert tool.nearest_expiry(far, TODAY) is None


def test_structures_are_picked_out_of_the_money_on_both_sides():
    got = tool.pick_structures(_chain(), TODAY)
    assert set(got) == set(tool.STRUCTURES)
    strikes = {name: [(l["kind"], l["side"], l["strike"]) for l in legs]
               for name, legs in got.items()}
    assert strikes["long_call"] == [("call", "long", 101.0)]
    assert strikes["debit_call_vertical"] == [("call", "long", 101.0),
                                              ("call", "short", 102.0)]
    assert strikes["credit_put_vertical"] == [("put", "short", 99.0),
                                              ("put", "long", 98.0)]
    assert strikes["iron_condor"] == [("put", "long", 97.0), ("put", "short", 98.0),
                                      ("call", "short", 102.0), ("call", "long", 103.0)]


def test_every_picked_structure_builds_and_reads_its_side():
    expected = {"long_call": True, "debit_call_vertical": True,
                "credit_put_vertical": False, "iron_condor": False}
    for name, legs in tool.pick_structures(_chain(), TODAY).items():
        clean = [{k: v for k, v in l.items() if k != "_mid"} for l in legs]
        assert live_order.is_debit(clean) is expected[name], name
        price = tool.net_price(legs)
        assert price and price > 0, name


def test_net_prices():
    legs = tool.pick_structures(_chain(), TODAY)
    assert round(tool.net_price(legs["debit_call_vertical"]), 2) == 0.50
    assert round(tool.net_price(legs["iron_condor"]), 2) == 0.80


def test_a_one_sided_market_is_not_picked():
    chain = _chain()
    chain["callExpDateMap"]["2026-11-06:1"]["101.0"][0]["bid"] = 0
    got = tool.pick_structures(chain, TODAY)
    assert got["long_call"][0]["strike"] == 102.0


def test_no_spot_no_structures():
    chain = _chain()
    chain["underlyingPrice"] = None
    assert tool.pick_structures(chain, TODAY) == {}


def test_reading_schwabs_answers():
    accepted = {"schwab": {"orderValidationResult": {
        "accepts": [{"message": "ok"}], "warns": [{"message": "wide market"}]}}}
    assert tool.summarize(200, accepted) == ("accepted", ["warn: wide market"])
    rejected = {"schwab": {"orderValidationResult": {
        "rejects": [{"activityMessage": "stop not allowed on a spread"}]}}}
    assert tool.summarize(200, rejected) == ("rejected",
                                             ["reject: stop not allowed on a spread"])
    assert tool.summarize(403, {"detail": "live orders are off"}) == (
        "error 403", ["live orders are off"])
    assert tool.summarize(404, "Not Found") == ("error 404", ["Not Found"])


def test_account_numbers_are_scrubbed_from_schwabs_answer():
    """Schwab's preview echoes the full account number (found on the first
    run, 2026-11-05); nothing the tool prints or saves may carry it."""
    answer = {"schwab": {"orderStrategy": {"accountNumber": "12345678",
                                           "orderLegs": [{"accountId": 87654321}]},
                         "orderValidationResult": {"accepts": []}},
              "account_hash": "ABCDEF0123456789ABCDEF"}
    out = tool.scrub(answer)
    text = str(out)
    assert "12345678" not in text and "87654321" not in text
    assert "ABCDEF0123456789ABCDEF" not in text
    assert out["schwab"]["orderStrategy"]["accountNumber"] == "…5678"
    assert out["schwab"]["orderValidationResult"] == {"accepts": []}


def test_matrix_scrubs_before_it_keeps_an_answer():
    import inspect
    src = inspect.getsource(tool.cmd_matrix)
    assert "answer = scrub(resp.json())" in src
