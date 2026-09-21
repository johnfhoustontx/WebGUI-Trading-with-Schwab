"""The PUBLIC Rescue form (pages/options/rescue_live.py).

The page is built for real, the service's side is played by writing the
answer and result keys straight to the test bus, and the page's own poll timer
is driven by hand - the same harness ``test_rescue_adhoc_chain.py`` uses for
the private form.

⚠ Collecting the timers pokes NiceGUI internals. If an upgrade moves them this
SKIPS with the reason rather than failing.
"""
import ast
import asyncio
import datetime as dt
import inspect
import pathlib

import pytest
from nicegui import ui

import bus_client
from pages.options import rescue, rescue_live
from shared import public_rescue as pr

_TODAY = dt.date.today()
NEAR = (_TODAY + dt.timedelta(days=5)).isoformat()
MID = (_TODAY + dt.timedelta(days=12)).isoformat()
FAR = (_TODAY + dt.timedelta(days=120)).isoformat()
LISTED = [NEAR, MID, FAR]
STRIKES = [490.0, 495.0, 500.0, 505.0, 510.0]
SRC = pathlib.Path(rescue_live.__file__)


def _ladder(loaded=(NEAR, MID), symbol="SPY"):
    return {"symbol": symbol, "api": symbol, "spot": 502.0,
            "expirations": list(LISTED),
            "strikes": {e: {"call": list(STRIKES), "put": list(STRIKES)}
                        for e in loaded},
            "loaded_at": dt.datetime.now(dt.timezone.utc).isoformat()}


def _answer(key, outcome):
    bus_client.bus().cache_set(pr.cache_key(pr.answer_view(key)),
                               {"outcome": outcome,
                                "at": dt.datetime.now(dt.timezone.utc).isoformat()})


def _publish_ladder(ladder, key, outcome="done"):
    bus_client.bus().cache_set(pr.cache_key(pr.ladder_view(ladder["symbol"])), ladder)
    _answer(key, outcome)


# ── harness ─────────────────────────────────────────────────────────────────

def _walk(root):
    """Document order, so the Nth select found is the Nth on screen."""
    yield root
    for slot in getattr(root, "slots", {}).values():
        for child in slot.children:
            yield from _walk(child)


def _texts(root):
    return [el.text for el in _walk(root) if isinstance(getattr(el, "text", None), str)]


def _joined(root):
    return " | ".join(_texts(root))


def _timer(root, name):
    timers = [el for el in _walk(root) if isinstance(el, ui.timer)]
    if not timers:
        pytest.skip("cannot collect any ui.timer from the page")
    by_name = {getattr(t.callback, "__name__", ""): t for t in timers}
    assert name in by_name, f"no {name} timer; found {sorted(by_name)}"
    return by_name[name]


def _run(root, name):
    with root:
        result = _timer(root, name).callback()
        if asyncio.iscoroutine(result):
            asyncio.new_event_loop().run_until_complete(result)


def _click(root, label):
    btn = [el for el in _walk(root)
           if isinstance(el, ui.button) and getattr(el, "text", "") == label]
    assert len(btn) == 1, f"expected one {label!r} button, found {len(btn)}"
    with root:
        for listener in list(getattr(btn[0], "_event_listeners", {}).values()):
            if listener.type == "click":
                arity = len(inspect.signature(listener.handler).parameters)
                listener.handler(*((None,) if arity else ()))


def _labelled(root, label, kind):
    found = []
    for el in _walk(root):
        if getattr(el, "text", None) != label:
            continue
        slot = getattr(el, "parent_slot", None)
        found += [s for s in (slot.children if slot else []) if isinstance(s, kind)]
    assert len(found) == 1, f"expected one {label} {kind.__name__}, found {len(found)}"
    return found[0]


def _expiry_select(root):
    return _labelled(root, "Expiry", ui.select)


def _leg_expiry_selects(root):
    top = _expiry_select(root)
    return [el for el in _walk(root) if isinstance(el, ui.select) and el is not top
            and NEAR in list(el.options)]


_HANDLES = {}


def _editor():
    return _HANDLES["editor"]


def _sent():
    return [c for _id, c in bus_client.bus().consume_commands(
        pr.STREAM, group="t", consumer="c", block_ms=50)]


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    bus_client.reset()
    _HANDLES.clear()
    rescue_live.LADDERS._seen.clear()
    rescue_live.COMPUTES._seen.clear()
    from pages.options import leg_editor
    real = leg_editor.build_leg_editor

    def _capture(*a, **kw):
        handle = real(*a, **kw)
        _HANDLES["editor"] = handle
        return handle

    monkeypatch.setattr(leg_editor, "build_leg_editor", _capture)
    with ui.card() as root:
        rescue.render(public=True)
    return root


def _loaded(root):
    """Load SPY and land its strikes list."""
    _click(root, "Load")
    _publish_ladder(_ladder(), pr.ladder_key("SPY"))
    _run(root, "_poll")


def _priced(root):
    """Load SPY and type the entry prices a visitor would: the seeded legs
    carry none, and the form reads that as no credit received."""
    _loaded(root)
    legs = _editor().get_legs()
    for leg in legs:
        leg["premium"] = 1.5 if leg["side"] == "short" else 0.5
    with root:
        _editor().set_legs(legs)
    _sent()                                           # drain the load


def test_unpriced_legs_are_refused_before_anything_is_sent(page):
    _loaded(page)
    _sent()
    _click(page, "Compute rescue options")
    assert _sent() == []


# ── pure helpers ────────────────────────────────────────────────────────────

def test_the_strikes_list_helpers():
    ladder = _ladder()
    assert rescue_live.listed_expirations(ladder) == LISTED
    assert rescue_live.loaded_expirations(ladder) == [NEAR, MID]
    assert rescue_live.ladder_strikes(ladder, NEAR, "put") == STRIKES
    assert rescue_live.ladder_strikes(ladder, FAR, "call") == []
    assert rescue_live.ladder_strikes(ladder, None, "call") == STRIKES
    for bad in (None, {}, {"expirations": "x", "strikes": []}):
        assert rescue_live.listed_expirations(bad) == []
        assert rescue_live.loaded_expirations(bad) == []
        assert rescue_live.ladder_strikes(bad, NEAR, "put") == []


def test_an_answer_older_than_the_request_is_not_its_answer():
    since = dt.datetime.now(dt.timezone.utc)
    old = {"outcome": "done", "at": (since - dt.timedelta(minutes=5)).isoformat()}
    new = {"outcome": "closed", "at": since.isoformat()}
    assert rescue_live.answer_state(old, since) == (False, None)
    assert rescue_live.answer_state(new, since) == (True, "closed")
    assert rescue_live.answer_state(None, since) == (False, None)


def test_the_closed_line_names_the_hours_and_is_silent_while_open():
    window = {"start": "08:40", "end": "15:00"}
    assert rescue_live.closed_line(True, window) == ""
    assert "08:40–15:00 CT" in rescue_live.closed_line(False, window)


# ── what the page is, and is not ────────────────────────────────────────────

def test_the_public_page_builds_no_owner_board_and_reads_no_owner_key(monkeypatch):
    reads = []
    for name in ("read", "read_version", "read_full"):
        real = getattr(bus_client, name)
        monkeypatch.setattr(bus_client, name,
                            lambda view, *a, _r=real, **k: (reads.append(view),
                                                             _r(view, *a, **k))[1])
    with ui.card() as root:
        rescue.render(public=True)
        for t in (el for el in _walk(root) if isinstance(el, ui.timer)):
            r = t.callback()
            if asyncio.iscoroutine(r):
                asyncio.new_event_loop().run_until_complete(r)
    owner = {"options:paper_account", "options:captured", "options:calc_chain",
             "options:rescue:adhoc"}
    assert not owner & set(reads), f"the public page read {owner & set(reads)}"
    text = _joined(root)
    assert "At-Risk Board" not in text and "Apply" not in text


def test_the_page_module_enqueues_only_through_the_public_requests():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ast.unparse(n.func).startswith("bus_client.")}
    writes = {c for c in calls if c.startswith("bus_client.request")}
    assert writes == {"bus_client.request_public_ladder",
                      "bus_client.request_public_rescue"}


def test_the_page_module_names_no_owner_view():
    text = SRC.read_text(encoding="utf-8")
    for view in ("options:paper_account", "options:captured", "options:calc_chain",
                 "options:rescue:adhoc", "rescue_apply", "cmd:options"):
        assert view not in text, view


# ── loading a symbol ────────────────────────────────────────────────────────

def test_a_page_load_spends_nothing():
    with ui.card() as root:
        rescue.render(public=True)
    assert _sent() == []


def test_load_asks_for_the_strikes_list_on_the_public_stream(page):
    _click(page, "Load")
    cmds = _sent()
    assert [(c.type, c.args) for c in cmds] == [(pr.LADDER_TYPE, {"symbol": "SPY"})]
    assert bus_client.bus().consume_commands(
        "cmd:options", group="t", consumer="c", block_ms=50) == []


def test_a_landed_list_fills_the_dropdowns_and_seeds_the_legs(page):
    _loaded(page)
    assert list(_expiry_select(page).options) == LISTED
    legs = _editor().get_legs()
    assert legs and all(l["strike"] in STRIKES for l in legs)
    assert all(l["expiry"] == NEAR for l in legs)
    for sel in _leg_expiry_selects(page):
        assert list(sel.options) == LISTED


def test_a_refused_load_says_why(page):
    _click(page, "Load")
    _answer(pr.ladder_key("SPY"), "closed")
    _run(page, "_poll")
    assert pr.OUTCOME_TEXT["closed"] in _joined(page)


def test_a_far_leg_expiry_is_fetched_and_the_leg_lands_on_it(page):
    _loaded(page)
    _sent()                                           # drain the load
    before = _editor().get_legs()
    with page:
        _leg_expiry_selects(page)[0].value = FAR
    cmds = _sent()
    assert [(c.type, c.args) for c in cmds] == [
        (pr.LADDER_TYPE, {"symbol": "SPY", "expiry": FAR})]
    assert _editor().get_legs()[0]["expiry"] == FAR
    assert _editor().get_legs()[0]["strike"] == before[0]["strike"]
    _publish_ladder(_ladder(loaded=(NEAR, MID, FAR)), pr.ladder_key("SPY", FAR))
    _run(page, "_poll")
    leg = _editor().get_legs()[0]
    assert leg["expiry"] == FAR and leg["strike"] in STRIKES


def test_a_failed_far_expiry_puts_the_leg_back(page):
    _loaded(page)
    with page:
        _leg_expiry_selects(page)[0].value = FAR
    _answer(pr.ladder_key("SPY", FAR), "error")
    _run(page, "_poll")
    assert _editor().get_legs()[0]["expiry"] in (NEAR, MID)
    assert f"Could not load strikes for {FAR}" in _joined(page)


# ── computing ───────────────────────────────────────────────────────────────

def _advisory():
    return {"symbol": "SPY", "strategy": "PCS", "state": "TESTED", "heat": 60,
            "candidates": [{"action": "roll_out", "label": "Roll out a week",
                            "apply_kind": "advisory", "net_cash": 40.0,
                            "gross_cash": 42.6, "commission": 2.6,
                            "est_fill_legs": [{"side": "sell", "right": "put",
                                               "strike": 500, "price": 1.2}]}],
            "computed_at": dt.datetime.now(dt.timezone.utc).isoformat()}


def test_compute_sends_the_normalized_trade_and_draws_advisory_cards(page):
    _priced(page)
    _click(page, "Compute rescue options")
    cmds = _sent()
    assert [c.type for c in cmds] == [pr.COMPUTE_TYPE]
    spec = cmds[0].args["spec"]
    assert spec == pr.clean_spec(spec)                 # already normalized
    key = pr.spec_key(spec)
    bus_client.bus().cache_set(pr.cache_key(pr.result_view(key)), _advisory())
    _answer(key, "done")
    _run(page, "_poll")
    text = _joined(page)
    assert "Roll out a week" in text
    assert "Manual — you place this one yourself" in text
    assert "Apply" not in text


def test_per_leg_fill_prices_stay_hidden_while_quotes_are_off(page, monkeypatch):
    from shared import public_scan
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: False)
    _priced(page)
    _click(page, "Compute rescue options")
    key = pr.spec_key(_sent()[0].args["spec"])
    bus_client.bus().cache_set(pr.cache_key(pr.result_view(key)), _advisory())
    _answer(key, "done")
    _run(page, "_poll")
    legs = [t for t in _texts(page) if t.startswith("SELL PUT")]
    assert legs == ["SELL PUT 500"]


def test_compute_before_a_load_asks_for_the_load(page):
    _click(page, "Compute rescue options")
    assert _sent() == []
    assert rescue_live.LOAD_PROMPT in _joined(page)


def test_a_visitor_over_the_hourly_limit_sends_nothing(page, monkeypatch):
    _priced(page)
    monkeypatch.setattr(rescue_live.COMPUTES, "allow", lambda key: False)
    _click(page, "Compute rescue options")
    assert _sent() == []
    assert "limit" in _joined(page)


def test_a_refused_compute_says_why(page):
    _priced(page)
    _click(page, "Compute rescue options")
    key = pr.spec_key(_sent()[0].args["spec"])
    _answer(key, "budget")
    _run(page, "_poll")
    assert pr.OUTCOME_TEXT["budget"] in _joined(page)
