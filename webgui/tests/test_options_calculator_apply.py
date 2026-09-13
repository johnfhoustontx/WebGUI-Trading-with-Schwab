"""End-to-end cover for the Calculator's three cache-apply paths.

``render()``'s smoke test only proves the page BUILDS. Everything the rebuilt
screen actually shows — the leg cards' deltas, the six metric cards, the matrix,
the status pill, the ③ LEGS strip — is painted later, by ``_apply_chain`` /
``_apply_result`` / ``_apply_iv``, which are locals inside ``render()`` and
reachable only through the three version-poll timers.

So this drives the timers: build the page, publish a payload, run the polls,
then read the rendered label texts back out of the element tree. It is the only
automated check that a rename or a signature change inside those three
functions has not left a blank screen behind a green suite.

⚠ Collecting the timers pokes NiceGUI internals. If a NiceGUI upgrade moves
them this SKIPS with the reason rather than failing — a harness that cannot
collect is not a page that is broken.
"""
import asyncio
import inspect
import time

import pytest
from nicegui import ui

import bus_client
from pages.options import calculator as calc
from pages.options import handoff
from pages.options import shared_position

_EXPIRY = "2026-08-28"


def _chain_payload():
    # Marks rise with the strike (650 -> 1.00 ... 670 -> 3.00), so a priced
    # two-leg structure has a real, non-zero net — a flat mark would net to
    # exactly $0 and read like the unpriced-template trap.
    strikes = {f"{k}": [{"mark": round((k - 640.0) / 10.0, 2),
                         "bid": round((k - 640.0) / 10.0 - 0.05, 2),
                         "ask": round((k - 640.0) / 10.0 + 0.05, 2),
                         "delta": -0.31, "volatility": 14.2}]
               for k in (650.0, 655.0, 660.0, 665.0, 670.0)}
    return {"symbol": "SPY", "price": 668.41,
            "chain": {"callExpDateMap": {f"{_EXPIRY}:9": strikes},
                      "putExpDateMap": {f"{_EXPIRY}:9": strikes}}}


def _result_payload(summary):
    return {"summary": summary,
            "eval_labels": ["Now", "08/22", "08/28"],
            "pnl_data": [{"price": 660.0, "pnl": [10, -5, 30], "pnl_pct": [1, -1, 3]},
                         {"price": 668.0, "pnl": [50, 60, 180], "pnl_pct": [5, 6, 18]}]}


def _walk(root):
    stack, out = [root], []
    while stack:
        el = stack.pop()
        out.append(el)
        for slot in getattr(el, "slots", {}).values():
            stack.extend(slot.children)
    return out


def _visible(el):
    """False when the element or any ancestor carries NiceGUI's `hidden` class.

    ``set_visibility(False)`` hides a container without clearing the labels
    inside it, so a plain text sweep still sees the placeholder copy sitting
    behind the results.
    """
    while el is not None:
        if "hidden" in getattr(el, "_classes", []):
            return False
        slot = getattr(el, "parent_slot", None)
        el = slot.parent if slot is not None else None
    return True


def _texts(root):
    return [el.text for el in _walk(root)
            if isinstance(getattr(el, "text", None), str) and _visible(el)]


# The three version polls, BY NAME. Identity, not arithmetic: an earlier form
# asserted the page held exactly three timers, so adding any fourth timer at
# render time would have turned all nine tests in this file into silent skips —
# the failure mode CLAUDE.md records from the options-scanner suite, where two
# real regressions hid behind two tests flipping to skipped. ``@guard`` is
# ``functools.wraps``-based, so the callback keeps the closure's ``__name__``.
_POLL_NAMES = ("_poll_chain", "_poll_result", "_poll_iv")


def _polls(root):
    timers = [el for el in _walk(root) if isinstance(el, ui.timer)]
    if not timers:
        # No timer at all reachable through the element tree is a HARNESS
        # failure (NiceGUI moved its internals), not a broken page.
        pytest.skip("cannot collect any ui.timer from the page")
    by_name = {getattr(t.callback, "__name__", ""): t for t in timers}
    missing = [n for n in _POLL_NAMES if n not in by_name]
    assert not missing, (
        f"render() no longer registers {missing} as ui.timer callbacks — "
        f"the page paints nothing. Found: {sorted(by_name)}")
    return [by_name[n] for n in _POLL_NAMES]


def _drive(root, polls):
    """Run each version poll once, inside the page's slot so ui.notify works."""
    with root:
        for timer in polls:
            result = timer.callback()
            if asyncio.iscoroutine(result):
                asyncio.new_event_loop().run_until_complete(result)


@pytest.fixture
def page(monkeypatch):
    # The polls run in their own task here, where NiceGUI cannot resolve a
    # client for a toast. Nothing under test depends on the notification.
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    bus_client.reset()
    # ⚠ Both of these are MODULE-level and survive a render, by design — the
    # single-user snapshot restores the last inputs across navigation, and the
    # handoff stash carries a signal in from another page. Left alone they also
    # leak BETWEEN TESTS: a test that types a symbol writes it into _LAST_CALC,
    # and the next render restores it. Under random ordering that is a failure
    # that moves around.
    calc._LAST_CALC.clear()
    shared_position.reset()        # the position shared with the Simulator, too
    for key in handoff._pending:
        handoff._pending[key] = None
    with ui.card() as root:
        calc.render()
    return root, _polls(root)


def test_a_landed_chain_paints_the_legs_the_strip_and_the_pill(page):
    root, polls = page
    # Before any chain the template is unpriced, so NET is an em-dash and NOT
    # "$0" — the trap this readout exists to avoid.
    assert "NET —" in _texts(root)
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    texts = _texts(root)

    # the status pill + the ② SYMBOL hint follow the chain
    assert "CHAIN LOADED · SPY" in texts
    assert "LIVE" in texts
    assert "5 strikes · 1 expiries" in texts

    # the legs resolved onto the real ladder and read their delta off the chain
    assert "-0.31" in texts or "+0.31" in texts, "no leg delta rendered"

    # the legs strip: a landed chain PRICES the template (2026-09-12 — there is
    # no Fetch premiums button any more), so NET is a real signed figure now
    assert "2 LEGS" in texts
    assert "NET —" not in texts
    assert [t for t in texts if t.startswith("NET +$")], "priced template has no net"
    assert "MAX LOSS —" not in texts

    # no result yet -> the placeholder names the SECOND wait, not the first
    assert "AWAITING CALCULATION" in texts
    assert "AWAITING CHAIN" not in texts


def test_a_landed_result_paints_the_six_cards_and_the_matrix(page):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    bus_client.bus().cache_set("cache:options:calc_result", _result_payload(
        {"entry_credit": 180.0, "max_loss": 320.0, "max_profit": 180.0,
         "return_on_risk": 56.3, "breakevens": [658.2], "pop": 71.4}))
    _drive(root, polls)
    texts = _texts(root)

    for label in ("ENTRY CREDIT", "MAX RISK", "MAX RETURN", "RETURN ON RISK",
                  "BREAKEVEN(S)", "PROB OF PROFIT"):
        assert label in texts, f"metric card {label!r} missing"
    assert "$320" in texts and "56.3%" in texts and "658.20" in texts
    assert "PRICE × DATE · 2 ROWS · % OF MAX RETURN" in texts
    assert not [t for t in texts if t.startswith("AWAITING")]


def test_the_uncapped_sentinel_never_reaches_the_screen_as_a_number(page):
    # A LONG_CALL's max_profit and a NAKED_CALL's max_loss arrive as 999999.
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    bus_client.bus().cache_set("cache:options:calc_result", _result_payload(
        {"entry_credit": -400.0, "max_loss": calc.UNLIMITED,
         "max_profit": calc.UNLIMITED, "return_on_risk": 0.0,
         "breakevens": [], "pop": 0.0}))
    _drive(root, polls)
    texts = _texts(root)

    assert "$999,999" not in texts
    assert texts.count("Unlimited") == 2          # MAX RISK and MAX RETURN
    assert "ENTRY DEBIT" in texts
    # no capped return -> the matrix falls back to % of the debit paid
    assert "PRICE × DATE · 2 ROWS · % OF COST" in texts


def test_an_empty_summary_renders_em_dashes_not_zeroes(page):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    bus_client.bus().cache_set("cache:options:calc_result", _result_payload({}))
    _drive(root, polls)
    texts = _texts(root)

    assert "$0" not in texts and "0.0%" not in texts
    assert "PRICE × DATE · 2 ROWS · NO PERCENTAGE BASIS" in texts


def test_an_implied_iv_result_fills_the_field(page):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_iv",
                               {"iv": 14.2, "strike": 660.0, "option_type": "put"})
    _drive(root, polls)
    # the IV number input is the only widget carrying that value
    assert 14.2 in [getattr(el, "value", None) for el in _walk(root)]


def _wearing(root, token):
    """Elements carrying every class of a theme token string."""
    want = set(token.split())
    return [el for el in _walk(root) if want <= set(getattr(el, "_classes", []))]


def test_the_chain_grid_is_empty_until_a_chain_lands(page):
    # Replaces the old frame-dimming test: the numbered frames went with the
    # entry panel. What must still hold is that nothing chain-shaped shows
    # before a chain exists.
    root, polls = page
    texts = _texts(root)
    assert "Load a symbol to see its chain." in texts
    assert _grid_strikes(root) == []

    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)

    assert "Load a symbol to see its chain." not in _texts(root)
    assert _grid_strikes(root) == [650.0, 655.0, 660.0, 665.0, 670.0]


def _click(root, label):
    """Invoke a button's click handler by its label, inside the page's slot.

    `load_symbol` creates a timeout timer and `do_calc` notifies, both of which
    need a slot context."""
    for el in _walk(root):
        if getattr(el, "text", None) == label:
            for listener in getattr(el, "_event_listeners", {}).values():
                if listener.type == "click":
                    # NiceGUI wraps an on_click in a one-arg lambda; a handler
                    # attached with .on() keeps its own arity.
                    arity = len(inspect.signature(listener.handler).parameters)
                    with root:
                        listener.handler(*((None,) if arity else ()))
                    return
    raise AssertionError(f"no button labelled {label!r}")


def _hooked(root, cls):
    return [el for el in _walk(root) if cls in getattr(el, "_classes", [])]


def _grid_body(root):
    return _hooked(root, "entry-gridbody")[0]


def _grid_strikes(root):
    """Strikes the chain grid lists — its rows are one html block now."""
    import re
    return [float(k) for k in re.findall(r'class="entry-grow[^"]*" data-strike="([^"]+)"',
                                         _grid_body(root).content)]


def _click_grid(root, pick, side, strike):
    """A delegated grid click, as the browser's js_handler emits it."""
    from types import SimpleNamespace
    body = _grid_body(root)
    for listener in list(body._event_listeners.values()):
        if listener.type == "click":
            with root:
                listener.handler(SimpleNamespace(
                    args={"pick": pick, "side": side, "strike": f"{strike:g}"}))


def _symbol_input(root):
    """The TICKER field — exactly one on the page (the strike boxes are inputs too)."""
    inputs = _hooked(root, "entry-ticker")
    assert len(inputs) == 1, f"expected one ticker input, found {len(inputs)}"
    return inputs[0]


def _fire(root, el, event):
    for listener in list(getattr(el, "_event_listeners", {}).values()):
        if listener.type == event:
            arity = len(inspect.signature(listener.handler).parameters)
            with root:
                listener.handler(*((None,) if arity else ()))


def _recalc(root):
    """Run the recalculation debounce past its delay, once."""
    timers = [el for el in _walk(root)
              if isinstance(el, ui.timer)
              and getattr(el.callback, "__name__", "") == "_recalc_tick"]
    assert len(timers) == 1, "render() no longer registers _recalc_tick"
    real = time.monotonic
    time.monotonic = lambda: real() + 60.0
    try:
        with root:
            timers[0].callback()
    finally:
        time.monotonic = real


@pytest.fixture
def sent_commands(monkeypatch):
    sent = []
    real = bus_client.request

    def _record(domain, command):
        sent.append(command)
        return real(domain, command)

    monkeypatch.setattr(bus_client, "request", _record)
    return sent


def _calculate_spy(root, polls):
    """Chain -> the debounce fires -> a result, through the real handlers."""
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    _recalc(root)                      # attributes the result to SPY
    bus_client.bus().cache_set("cache:options:calc_result", _result_payload(
        {"entry_credit": 180.0, "max_loss": 320.0, "max_profit": 180.0,
         "return_on_risk": 56.3, "breakevens": [658.2], "pop": 71.4}))
    _drive(root, polls)
    assert "ENTRY CREDIT" in _texts(root)


def test_a_two_leg_template_can_still_be_edited_down_to_one(page):
    # The mock locks removal at two legs because its own buildLegs PADS a
    # single-leg spec with a synthetic opposite leg. This app does not pad — it
    # ships four real single-leg templates — so a two-leg floor would make those
    # unreachable by hand. The floor itself stays: nothing to price at zero.
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)

    removes = [el for el in _walk(root) if "leg-remove" in getattr(el, "_classes", [])]
    assert len(removes) == 2, "the PCS default template is two legs"
    assert all(el.enabled for el in removes)


def test_loading_a_different_symbol_drops_the_previous_symbols_numbers(page):
    # Old cards + an old matrix under a pill reading LOADING CHAIN is the page
    # stating one symbol's numbers while announcing another's.
    root, polls = page
    _calculate_spy(root, polls)

    _symbol_input(root).value = "QQQ"
    _fire(root, _symbol_input(root), "keydown.enter")
    texts = _texts(root)

    assert "ENTRY CREDIT" not in texts
    assert "658.20" not in texts
    assert "AWAITING CHAIN" in texts
    assert "LOADING CHAIN" in texts


def test_reloading_the_same_symbol_keeps_the_result_on_screen(page):
    # A refresh, not a new subject — and the restore-on-navigation path does
    # exactly this, so wiping here would blank the screen on every return visit.
    root, polls = page
    _calculate_spy(root, polls)

    _click(root, "REFRESH")
    texts = _texts(root)

    assert "ENTRY CREDIT" in texts
    assert "658.20" in texts
    assert not [t for t in texts if t.startswith("AWAITING")]


def test_the_page_does_not_caption_the_strategy_picker_twice(page):
    # The picker's own "Strategy" caption is switched off: the panel's trigger
    # names the strategy it holds, and a caption over it is noise.
    root, _polls = page
    assert "Strategy" not in _texts(root)


def test_a_landed_chain_enqueues_one_compute_after_the_debounce(page, sent_commands):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    assert not [c for c in sent_commands if c["type"] == "calc_compute"], \
        "priced before the user paused"
    _recalc(root)
    computes = [c for c in sent_commands if c["type"] == "calc_compute"]
    assert len(computes) == 1
    legs = computes[0]["args"]["legs"]
    assert len(legs) == 2 and all(l["premium"] > 0 for l in legs)
    _recalc(root)                      # nothing new poked: no second compute
    assert len([c for c in sent_commands if c["type"] == "calc_compute"]) == 1


def test_a_landed_chain_asks_the_service_to_imply_iv(page, sent_commands):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    assert [c for c in sent_commands if c["type"] == "calc_iv"]


def test_clicking_a_call_bid_adds_a_short_call_priced_at_the_mark(page, sent_commands):
    # The put credit spread holds no short call, so the click adds a leg.
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    assert 655.0 in _grid_strikes(root)
    _click_grid(root, "bid", "call", 655.0)
    _recalc(root)
    legs = [c for c in sent_commands if c["type"] == "calc_compute"][-1]["args"]["legs"]
    assert len(legs) == 3
    assert legs[-1] == {"strike": 655.0, "premium": 1.5, "option_type": "call",
                        "side": "short", "qty": 1, "expiry": _EXPIRY}
    assert "3 LEGS" in _texts(root)


def test_clicking_a_put_bid_moves_the_short_put_instead_of_adding_a_row(page, sent_commands):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    _click_grid(root, "bid", "put", 655.0)
    _recalc(root)
    legs = [c for c in sent_commands if c["type"] == "calc_compute"][-1]["args"]["legs"]
    assert len(legs) == 2
    short = [l for l in legs if l["side"] == "short"]
    assert short == [{"strike": 655.0, "premium": 1.5, "option_type": "put",
                      "side": "short", "qty": 1, "expiry": _EXPIRY}]
    assert "2 LEGS" in _texts(root)


def test_the_action_buttons_are_gone(page):
    root, _polls = page
    labels = {getattr(el, "text", None) for el in _walk(root) if isinstance(el, ui.button)}
    for gone in ("LOAD CHAIN", "IV UPDATE", "FETCH PREMIUMS", "CALCULATE"):
        assert gone not in labels, gone


# ── every expiration listed, strikes on demand (2026-09-12) ─────────────────
_FAR = "2026-10-30"


def _lazy_payload(**extra):
    cc = _chain_payload()
    cc["expirations"] = [_EXPIRY, "2026-09-04", _FAR]
    cc["expirations"].sort()
    cc.update(extra)
    return cc


def _with_far(cc):
    far = {f"{k}": [dict(v[0], mark=v[0]["mark"] + 5)] for k, v in
           cc["chain"]["putExpDateMap"][f"{_EXPIRY}:9"].items()}
    out = dict(cc, added=_FAR)
    out["chain"] = {mk: dict(cc["chain"][mk], **{f"{_FAR}:72": far})
                    for mk in ("callExpDateMap", "putExpDateMap")}
    return out


def test_the_load_asks_the_service_for_a_lazy_chain(page, sent_commands):
    root, _polls = page
    _symbol_input(root).value = "TSLA"
    _fire(root, _symbol_input(root), "keydown.enter")
    load = [c for c in sent_commands if c["type"] == "calc_load"][-1]
    assert load["args"]["symbol"] == "TSLA" and load["args"]["lazy"] is True
    assert isinstance(load["args"]["expiries"], list)


def test_every_listed_expiration_gets_a_pill(page):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, polls)
    pills = [el.text for el in _hooked(root, "entry-expiry")]
    assert len(pills) == 3 and any(t.startswith("Oct 30") for t in pills)


def test_an_unloaded_expiry_is_fetched_then_the_legs_move_when_it_lands(page, sent_commands):
    root, polls = page
    cc = _lazy_payload()
    bus_client.bus().cache_set("cache:options:calc_chain", cc)
    _drive(root, polls)
    far_pill = [el for el in _hooked(root, "entry-expiry") if el.text.startswith("Oct 30")][0]
    _fire(root, far_pill, "click")

    req = [c for c in sent_commands if c["type"] == "calc_load_expiry"]
    assert req and req[-1]["args"] == {"symbol": "SPY", "expiry": _FAR}
    assert "Loading strikes for Oct 30…" in _texts(root)

    bus_client.bus().cache_set("cache:options:calc_chain", _with_far(cc))
    _drive(root, polls)
    _recalc(root)
    legs = [c for c in sent_commands if c["type"] == "calc_compute"][-1]["args"]["legs"]
    assert {l["expiry"] for l in legs} == {_FAR}
    assert all(l["premium"] >= 5 for l in legs), "legs not re-priced at the far expiry"


def test_a_merge_does_not_reseed_or_drop_the_users_legs(page):
    root, polls = page
    cc = _lazy_payload()
    bus_client.bus().cache_set("cache:options:calc_chain", cc)
    _drive(root, polls)
    _click_grid(root, "bid", "call", 655.0)         # the legs are now the user's
    bus_client.bus().cache_set("cache:options:calc_chain", _with_far(cc))
    _drive(root, polls)
    assert "3 LEGS" in _texts(root)


def test_a_failed_expiry_fetch_stops_waiting_and_says_so(page):
    root, polls = page
    cc = _lazy_payload()
    bus_client.bus().cache_set("cache:options:calc_chain", cc)
    _drive(root, polls)
    far_pill = [el for el in _hooked(root, "entry-expiry") if el.text.startswith("Oct 30")][0]
    _fire(root, far_pill, "click")
    bus_client.bus().cache_set("cache:options:calc_chain", dict(cc, added=_FAR, failed=True))
    _drive(root, polls)
    texts = _texts(root)
    assert "Loading strikes for Oct 30…" not in texts
    assert [t for t in texts if "could not load strikes for Oct 30" in t]


# ── one position shared with the Simulator (2026-09-12) ─────────────────────

def test_the_copy_to_simulator_button_is_gone(page):
    root, _polls = page
    labels = {getattr(el, "text", None) for el in _walk(root) if isinstance(el, ui.button)}
    assert "COPY TO SIMULATOR" not in labels


def test_the_calculator_publishes_its_position_as_it_changes(page):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    _click_grid(root, "bid", "call", 655.0)
    pos = shared_position.current()
    assert pos["symbol"] == "SPY" and len(pos["legs"]) == 3
    assert pos["legs"][-1] == {"option_type": "call", "side": "short", "strike": 655.0,
                               "expiry": _EXPIRY, "qty": 1, "premium": 1.5}


def test_a_landed_chain_publishes_the_legs_it_laid(monkeypatch, sent_commands):
    # A scanner hand-off or a fresh template is a position the Simulator must see
    # even before the user touches anything here.
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    bus_client.reset()
    calc._LAST_CALC.clear()
    shared_position.reset()
    with ui.card() as root:
        calc.render()
    polls = _polls(root)
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    pos = shared_position.current()
    assert pos and len(pos["legs"]) == 2 and all(l["strike"] for l in pos["legs"])


def test_the_calculator_opens_with_the_shared_position(monkeypatch, sent_commands):
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    bus_client.reset()
    calc._LAST_CALC.clear()
    for key in handoff._pending:
        handoff._pending[key] = None
    legs = [{"option_type": "put", "side": "short", "strike": 660.0, "expiry": _EXPIRY,
             "qty": 1, "premium": 9.99},                    # a price typed on this page earlier
            {"option_type": "put", "side": "long", "strike": 650.0, "expiry": _EXPIRY,
             "qty": 1, "premium": None}]                    # a leg the Simulator added
    shared_position.publish("SPY", "PCS", legs, _EXPIRY)
    with ui.card() as root:
        calc.render()
    polls = _polls(root)
    load = [c for c in sent_commands if c["type"] == "calc_load"][-1]
    assert load["args"]["symbol"] == "SPY" and _EXPIRY in load["args"]["expiries"]

    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    _recalc(root)
    got = [c for c in sent_commands if c["type"] == "calc_compute"][-1]["args"]["legs"]
    assert [(l["strike"], l["premium"]) for l in got] == [(660.0, 9.99), (650.0, 1.0)]
