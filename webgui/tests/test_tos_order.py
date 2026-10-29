"""The Trade detail panel's ThinkorSwim bracket (``pages/options/tos_order.py``).

The expected strings are ThinkorSwim's own export format, the one the operator
pastes with Order Entry Tools > Paste order from clipboard.
"""
import datetime as dt
import json

import pytest

import config_schema
from pages.options import tos_order

DEBIT_CALL = {
    "symbol": "$SPX", "type": "BULL_CALL", "net_cost": -1.5,
    "legs": [
        {"kind": "call", "side": "long", "strike": 5800.0, "qty": 1,
         "expiration": "2026-10-30"},
        {"kind": "call", "side": "short", "strike": 5810.0, "qty": 1,
         "expiration": "2026-10-30"},
    ],
}

# A scanner put credit spread: strike keys, no legs, a per-share credit.
CREDIT_PUT = {"symbol": "SPY", "type": "PCS", "credit": 1.2,
              "short_strike": 580.0, "long_strike": 575.0,
              "expiration": "2026-11-06"}


@pytest.fixture(autouse=True)
def _shipped(monkeypatch):
    monkeypatch.setattr(tos_order, "load", lambda: tos_order.DEFAULTS)


def test_a_debit_vertical_is_the_operators_example_exactly():
    got = tos_order.order(DEBIT_CALL)["text"]
    assert got == "\n".join([
        "BUY +1 VERTICAL SPX 100 (Weeklys) 30 OCT 26 5800/5810 CALL @1.50 LMT [TO OPEN/TO OPEN]",
        "SELL -1 VERTICAL SPX 100 (Weeklys) 30 OCT 26 5800/5810 CALL @TRG+100.00% LMT TRG BY #1 OCO [TO CLOSE/TO CLOSE]",
        "SELL -1 VERTICAL SPX 100 (Weeklys) 30 OCT 26 5800/5810 CALL STP TRG-50.00% MARK TRG BY #1 OCO [TO CLOSE/TO CLOSE]",
    ])


def test_a_credit_vertical_sells_short_first_and_mirrors_the_exits():
    """Sold to open, so the exits BUY: the target is a fall in the spread's
    price and the stop a rise. "A/B" is long A per +1, so the short strike
    leads a SELL."""
    got = tos_order.order(CREDIT_PUT)["text"]
    assert got == "\n".join([
        "SELL -1 VERTICAL SPY 100 (Weeklys) 6 NOV 26 580/575 PUT @1.20 LMT [TO OPEN/TO OPEN]",
        "BUY +1 VERTICAL SPY 100 (Weeklys) 6 NOV 26 580/575 PUT @TRG-50.00% LMT TRG BY #1 OCO [TO CLOSE/TO CLOSE]",
        "BUY +1 VERTICAL SPY 100 (Weeklys) 6 NOV 26 580/575 PUT STP TRG+100.00% MARK TRG BY #1 OCO [TO CLOSE/TO CLOSE]",
    ])


def test_direction_comes_from_the_strikes_not_the_price_sign():
    """A captured debit row may carry a POSITIVE price; the strikes still say
    it was bought."""
    sig = {**DEBIT_CALL, "net_cost": 1.5}
    assert tos_order.order(sig)["text"].startswith("BUY +1 ")
    bear_put = {"symbol": "QQQ", "type": "BEAR_PUT", "credit": -2.0,
                "legs": [{"kind": "put", "side": "long", "strike": 500, "qty": 1},
                         {"kind": "put", "side": "short", "strike": 490, "qty": 1}],
                "expiration": "2026-11-20"}
    first = tos_order.order(bear_put)["text"].splitlines()[0]
    assert first == ("BUY +1 VERTICAL QQQ 100 20 NOV 26 500/490 PUT @2.00 LMT "
                     "[TO OPEN/TO OPEN]")


def test_a_call_credit_spread_from_the_strike_keys():
    sig = {"symbol": "IWM", "type": "CCS", "credit": 0.85, "short_strike": 230,
           "long_strike": 235, "expiration": "2026-11-13"}
    assert tos_order.order(sig)["text"].splitlines()[0] == (
        "SELL -1 VERTICAL IWM 100 (Weeklys) 13 NOV 26 230/235 CALL @0.85 LMT "
        "[TO OPEN/TO OPEN]")


@pytest.mark.parametrize("d, monthly", [
    (dt.date(2026, 11, 20), True),     # third Friday
    (dt.date(2026, 11, 13), False),
    (dt.date(2026, 11, 19), False),    # the Thursday before a normal monthly
    (dt.date(2026, 6, 18), True),      # Juneteenth closes the 19th: Thursday
    (dt.date(2026, 6, 19), False),
])
def test_only_a_non_monthly_expiry_is_tagged_weeklys(d, monthly):
    assert tos_order.is_monthly(d) is monthly


def test_strikes_and_dates_are_written_as_thinkorswim_writes_them():
    assert tos_order.strike_text(5800.0) == "5800"
    assert tos_order.strike_text(452.5) == "452.5"
    assert tos_order.strike_text(12.75) == "12.75"
    assert tos_order.expiry_text(dt.date(2026, 11, 6)) == "6 NOV 26"


# ── every structure ─────────────────────────────────────────────────────────
# The operator pasted each of these opening lines into ThinkorSwim on
# 2026-10-28 and every one loaded as the position named (INTC, Nov 6 and Dec 4
# weeklies, Nov 20 monthly).
W, M, D = "2026-11-06", "2026-11-20", "2026-12-04"


def _leg(kind, side, strike, exp=W, qty=1):
    return {"kind": kind, "side": side, "strike": strike, "qty": qty,
            "expiration": exp}


PASTED = [
    (-1.80, [_leg("call", "long", 100)],
     "BUY +1 INTC 100 (Weeklys) 6 NOV 26 100 CALL @1.80 LMT [TO OPEN]"),
    (1.20, [_leg("put", "short", 90)],
     "SELL -1 INTC 100 (Weeklys) 6 NOV 26 90 PUT @1.20 LMT [TO OPEN]"),
    (-1.50, [_leg("call", "long", 95), _leg("call", "short", 100)],
     "BUY +1 VERTICAL INTC 100 (Weeklys) 6 NOV 26 95/100 CALL @1.50 LMT [TO OPEN/TO OPEN]"),
    (0.90, [_leg("put", "short", 90), _leg("put", "long", 85)],
     "SELL -1 VERTICAL INTC 100 (Weeklys) 6 NOV 26 90/85 PUT @0.90 LMT [TO OPEN/TO OPEN]"),
    (-6.00, [_leg("call", "long", 95), _leg("put", "long", 95)],
     "BUY +1 STRADDLE INTC 100 (Weeklys) 6 NOV 26 95 @6.00 LMT [TO OPEN/TO OPEN]"),
    (2.10, [_leg("call", "short", 105), _leg("put", "short", 85)],
     "SELL -1 STRANGLE INTC 100 (Weeklys) 6 NOV 26 105/85 CALL/PUT @2.10 LMT "
     "[TO OPEN/TO OPEN]"),
    (-2.50, [_leg("call", "short", 95, W), _leg("call", "long", 95, D)],
     "BUY +1 CALENDAR INTC 100 (Weeklys) 4 DEC 26/6 NOV 26 95 CALL @2.50 LMT "
     "[TO OPEN/TO OPEN]"),
    (-3.00, [_leg("call", "short", 100, W), _leg("call", "long", 95, D)],
     "BUY +1 DIAGONAL INTC 100 (Weeklys) 4 DEC 26/6 NOV 26 95/100 CALL @3.00 LMT "
     "[TO OPEN/TO OPEN]"),
    (-1.00, [_leg("call", "long", 90), _leg("call", "short", 95, qty=2),
             _leg("call", "long", 100)],
     "BUY +1 BUTTERFLY INTC 100 (Weeklys) 6 NOV 26 90/95/100 CALL @1.00 LMT "
     "[TO OPEN/TO OPEN/TO OPEN]"),
    (-1.20, [_leg("call", "long", 85), _leg("call", "short", 90),
             _leg("call", "short", 100), _leg("call", "long", 105)],
     "BUY +1 CONDOR INTC 100 (Weeklys) 6 NOV 26 85/90/100/105 CALL @1.20 LMT "
     "[TO OPEN/TO OPEN/TO OPEN/TO OPEN]"),
    (1.40, [_leg("put", "long", 80), _leg("put", "short", 85),
            _leg("call", "short", 105), _leg("call", "long", 110)],
     "SELL -1 IRON CONDOR INTC 100 (Weeklys) 6 NOV 26 105/110/85/80 CALL/PUT @1.40 LMT "
     "[TO OPEN/TO OPEN/TO OPEN/TO OPEN]"),
    (3.50, [_leg("put", "long", 90), _leg("put", "short", 95),
            _leg("call", "short", 95), _leg("call", "long", 100)],
     "SELL -1 IRON CONDOR INTC 100 (Weeklys) 6 NOV 26 95/100/95/90 CALL/PUT @3.50 LMT "
     "[TO OPEN/TO OPEN/TO OPEN/TO OPEN]"),
    (-91.00, [{"kind": "stock", "side": "long", "strike": None, "qty": 1},
              _leg("call", "short", 100, M)],
     "BUY +100 COVERED INTC 100 20 NOV 26 100 CALL @91.00 LMT [TO OPEN/TO OPEN]"),
    (-0.40, [_leg("call", "short", 95), _leg("call", "long", 100, qty=2)],
     "BUY +1 -1/2 CUSTOM INTC 100 (Weeklys) 6 NOV 26/6 NOV 26 95/100 CALL/CALL "
     "@0.40 LMT [TO OPEN/TO OPEN]"),
    (0.30, [_leg("put", "long", 80), _leg("put", "short", 90, qty=2),
            _leg("put", "long", 95)],
     "SELL -1 -1/2/-1 CUSTOM INTC 100 (Weeklys) 6 NOV 26/6 NOV 26/6 NOV 26 80/90/95 "
     "PUT/PUT/PUT @0.30 LMT [TO OPEN/TO OPEN/TO OPEN]"),
]


@pytest.mark.parametrize("credit, legs, opening", PASTED)
def test_every_structure_opens_as_thinkorswim_pasted_it(credit, legs, opening):
    text = tos_order.order({"symbol": "INTC", "credit": credit, "legs": legs})["text"]
    assert text.splitlines()[0] == opening


@pytest.mark.parametrize("credit, legs, opening", PASTED)
def test_the_exits_reverse_the_opening_line(credit, legs, opening):
    """Same body, side reversed, one TO CLOSE per leg; a buy is a debit."""
    lines = tos_order.order({"symbol": "INTC", "credit": credit, "legs": legs})["text"] \
        .splitlines()
    if "COVERED" in opening:
        assert len(lines) == 1               # shares: the opening order only
        return
    bought = opening.startswith("BUY +1 ")
    body = opening.split(" ", 2)[2].rsplit(" @", 1)[0]
    close = "SELL -1" if bought else "BUY +1"
    effects = opening.rsplit("[", 1)[1].replace("OPEN", "CLOSE")
    tgt, stp = ("+100.00", "-50.00") if bought else ("-50.00", "+100.00")
    assert lines[1] == f"{close} {body} @TRG{tgt}% LMT TRG BY #1 OCO [{effects}"
    assert lines[2] == f"{close} {body} STP TRG{stp}% MARK TRG BY #1 OCO [{effects}"


def test_the_side_of_a_named_spread_comes_from_its_legs_not_its_price():
    """A short condor sells, a reversed iron condor buys, whatever sign the
    row's price carries."""
    short_condor = [_leg("call", "short", 85), _leg("call", "long", 90),
                    _leg("call", "long", 100), _leg("call", "short", 105)]
    o = tos_order.order({"symbol": "INTC", "credit": -1.0, "legs": short_condor})
    assert o["text"].startswith("SELL -1 CONDOR INTC 100 (Weeklys) 6 NOV 26 85/90/100/105 CALL")
    reverse_ic = [_leg("put", "short", 80), _leg("put", "long", 85),
                  _leg("call", "long", 105), _leg("call", "short", 110)]
    o = tos_order.order({"symbol": "INTC", "credit": 1.0, "legs": reverse_ic})
    assert o["text"].startswith("BUY +1 IRON CONDOR INTC 100 (Weeklys) 6 NOV 26 105/110/85/80")


def test_an_iron_condor_from_the_scanners_keys():
    sig = {**CREDIT_PUT, "type": "IC", "call_short": 600, "call_long": 605}
    assert tos_order.order(sig)["text"].splitlines()[0] == (
        "SELL -1 IRON CONDOR SPY 100 (Weeklys) 6 NOV 26 600/605/580/575 CALL/PUT "
        "@1.20 LMT [TO OPEN/TO OPEN/TO OPEN/TO OPEN]")


def test_a_short_put_from_the_scanners_keys():
    sig = {"symbol": "SPY", "type": "NAKED_PUT", "credit": 2.0, "short_strike": 560,
           "long_strike": None, "expiration": "2026-11-20"}
    assert tos_order.order(sig)["text"].splitlines()[0] == (
        "SELL -1 SPY 100 20 NOV 26 560 PUT @2.00 LMT [TO OPEN]")


def test_a_position_several_lots_deep_orders_its_smallest_ratio():
    legs = [_leg("call", "long", 95, qty=3), _leg("call", "short", 100, qty=3)]
    o = tos_order.order({"symbol": "INTC", "credit": -1.5, "legs": legs})
    assert o["text"].startswith("BUY +1 VERTICAL INTC 100 (Weeklys) 6 NOV 26 95/100 CALL")


def test_mixed_expiries_are_tagged_weeklys_once_any_is():
    legs = [_leg("call", "short", 95, M), _leg("call", "long", 95, D)]
    o = tos_order.order({"symbol": "INTC", "credit": -2.0, "legs": legs})
    assert "CALENDAR INTC 100 (Weeklys) 4 DEC 26/20 NOV 26 95 CALL" in o["text"]


@pytest.mark.parametrize("sig", [
    # a protective put: shares beside a long put have no order text
    {"symbol": "SPY", "credit": -580.0, "expiration": M,
     "legs": [{"kind": "stock", "side": "long", "strike": None, "qty": 1},
              _leg("put", "long", 550, M)]},
    # a collar: shares, a short call and a long put
    {"symbol": "SPY", "credit": -575.0, "expiration": M,
     "legs": [{"kind": "stock", "side": "long", "strike": None, "qty": 1},
              _leg("call", "short", 600, M), _leg("put", "long", 550, M)]},
    # a leg with no strike
    {**DEBIT_CALL, "legs": [{**DEBIT_CALL["legs"][0], "strike": None},
                            DEBIT_CALL["legs"][1]]},
    # a type whose keys are not known here
    {**CREDIT_PUT, "type": ""},
    # no price
    {**CREDIT_PUT, "credit": None},
    # no expiry
    {**CREDIT_PUT, "expiration": ""},
    # no symbol
    {**CREDIT_PUT, "symbol": ""},
])
def test_what_cannot_be_written_gets_no_order(sig):
    assert tos_order.order(sig) is None


def test_the_percentages_and_quantity_come_from_config(monkeypatch):
    monkeypatch.setattr(tos_order, "load", lambda: {
        "quantity": 3, "debit": {"target_pct": 80, "stop_pct": 40},
        "credit": {"target_pct": 60, "stop_pct": 150}})
    lines = tos_order.order(DEBIT_CALL)["text"].splitlines()
    assert lines[0].startswith("BUY +3 ")
    assert "@TRG+80.00% LMT" in lines[1] and "STP TRG-40.00% MARK" in lines[2]
    lines = tos_order.order(CREDIT_PUT)["text"].splitlines()
    assert lines[0].startswith("SELL -3 ")
    assert "@TRG-60.00% LMT" in lines[1] and "STP TRG+150.00% MARK" in lines[2]


@pytest.mark.parametrize("bad", [
    {"quantity": 0}, {"quantity": 1.5}, {"quantity": True},
    {"debit": {"stop_pct": 100}}, {"credit": {"target_pct": 100}},
    {"debit": {"target_pct": -5}}, {"credit": {"stop_pct": float("nan")}},
    {"debit": {"target_pct": "lots"}},
])
def test_a_bad_setting_falls_back_to_the_default(monkeypatch, bad):
    monkeypatch.setattr(tos_order, "load", lambda: bad)
    s = tos_order.settings()
    assert s["quantity"] == 1
    assert s["debit"] == tos_order.DEFAULTS["debit"]
    assert s["credit"] == tos_order.DEFAULTS["credit"]


def test_the_shipped_file_matches_the_defaults():
    tos_order.reset_cache()
    from shared.config_toml import toml_loader
    load, _reset = toml_loader(tos_order.TOS_ORDER_TOML, {}, label="t")
    assert load() == tos_order.DEFAULTS


def test_every_key_is_in_the_settings_catalogue():
    cfg = config_schema.BY_NAME["tos_order.toml"]
    keys = {f.key for sec in cfg.sections for f in sec.fields}
    assert keys == {"quantity", "debit.target_pct", "debit.stop_pct",
                    "credit.target_pct", "credit.stop_pct"}


# ── the panel's button ───────────────────────────────────────────────────────

def _panel_buttons(signal):
    from nicegui import ui

    from pages.options import detail
    with ui.column() as box:
        detail._build_cards(signal)
    return [b for b in box.descendants() if isinstance(b, ui.button)]


def _tos_buttons(signal):
    return [b for b in _panel_buttons(signal) if b.text == "Copy ThinkorSwim order"]


def test_the_panel_offers_the_copy_for_a_vertical():
    (btn,) = _tos_buttons(CREDIT_PUT)
    (listener,) = [l for l in btn._event_listeners.values() if l.type == "click"]
    text = tos_order.order(CREDIT_PUT)["text"]
    assert json.dumps(text) in listener.js_handler
    assert "navigator.clipboard" in listener.js_handler


def test_the_panel_offers_the_copy_for_an_iron_condor_too():
    assert len(_tos_buttons({**CREDIT_PUT, "type": "IC", "call_short": 600,
                             "call_long": 605})) == 1


def test_the_panel_offers_no_copy_for_what_cannot_be_written():
    protective_put = {"symbol": "SPY", "type": "PROTECTIVE_PUT", "credit": -580.0,
                      "expiration": M,
                      "legs": [{"kind": "stock", "side": "long", "strike": None, "qty": 1},
                               _leg("put", "long", 550, M)]}
    assert _tos_buttons(protective_put) == []


def test_the_public_origin_draws_no_copy():
    import live_screens
    import shell
    shell.publish(live_screens.PUBLIC_ROUTES)
    try:
        assert _tos_buttons(CREDIT_PUT) == []
    finally:
        shell.unpublish()


def test_the_toast_follows_what_the_browser_reported():
    from pages.options import detail
    assert detail.tos_copied({"ok": True}) is True
    for refused in ({"ok": False}, {}, None, {"ok": "yes"}):
        assert detail.tos_copied(refused) is False


def test_the_copy_handler_escapes_the_order_text():
    """A newline in the order must reach the browser as an escape inside a JS
    string, never as a raw line break that ends the handler."""
    from pages.options import detail
    js = detail.tos_copy_js('a\nb "c"')
    assert "\n" not in js
    assert r'c.writeText("a\nb \"c\"")' in js
