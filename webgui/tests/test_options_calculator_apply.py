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

import pytest
from nicegui import ui

import bus_client
from pages.options import calculator as calc

_EXPIRY = "2026-08-28"


def _chain_payload():
    strikes = {f"{k}": [{"mark": 2.4, "bid": 2.3, "ask": 2.5, "delta": -0.31,
                         "volatility": 14.2}]
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


def _polls(root):
    timers = [el for el in _walk(root) if isinstance(el, ui.timer)]
    if len(timers) != 3:
        pytest.skip(f"cannot collect the three version polls (found {len(timers)})")
    return timers


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
    with ui.card() as root:
        calc.render()
    return root, _polls(root)


def test_a_landed_chain_paints_the_legs_the_strip_and_the_pill(page):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    texts = _texts(root)

    # the status pill + the ② SYMBOL hint follow the chain
    assert "CHAIN LOADED · SPY" in texts
    assert "LIVE" in texts
    assert "5 strikes · 1 expiries" in texts

    # the legs resolved onto the real ladder and read their delta off the chain
    assert "-0.31" in texts or "+0.31" in texts, "no leg delta rendered"

    # the ③ LEGS strip: a fresh template is unpriced, so NET is an em-dash and
    # NOT "$0" — the trap this readout exists to avoid
    assert "2 LEGS" in texts
    assert "NET —" in texts
    assert "MAX LOSS —" in texts

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


def test_the_two_chain_dependent_frames_light_up_only_once_it_lands(page):
    from pages.options import theme as T

    root, polls = page
    # The muted colour on its own is also the eyebrow colour, worn by dozens of
    # labels — it is the CHIP wearing it that identifies a dimmed frame.
    off_chip = f"{T.CALC_CHIP} {calc._CHIP_TEXT['off']}"
    # ② SYMBOL and ③ LEGS start muted: neither means anything without a chain.
    # ① STRATEGY and P&L MATRIX never dim, so the count is exactly two.
    assert len(_wearing(root, T.CALC_FRAME_IDLE)) == 2
    assert len(_wearing(root, off_chip)) == 2

    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)

    assert _wearing(root, T.CALC_FRAME_IDLE) == []
    assert _wearing(root, off_chip) == []
    assert len(_wearing(root, T.CALC_FRAME)) == 4
