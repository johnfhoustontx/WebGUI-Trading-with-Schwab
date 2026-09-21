"""The Ad-hoc Trade form's chain load — every listed expiration, strikes on demand.

``test_rescue.py`` covers this page's pure builders. Everything the ad-hoc form
actually OFFERS — the Expiry dropdown's contents, the fetch a far pick triggers,
the legs moving when it lands — is painted by ``_adhoc_apply_chain``, a local
inside ``render()`` reachable only through its version-poll timer. So this drives
the timer: build the page, publish a payload, run the poll, then read the widget
state back out of the element tree.

⚠ Collecting the timer pokes NiceGUI internals. If a NiceGUI upgrade moves them
this SKIPS with the reason rather than failing — a harness that cannot collect is
not a page that is broken.
"""
import asyncio
import inspect

import pytest
from nicegui import ui

import bus_client
from pages.options import rescue

_NEAR = "2026-10-02"
_MID = "2026-10-09"
_FAR = "2027-01-15"          # a LEAPS-distance expiry: outside any +60d window
_LISTED = [_NEAR, _MID, "2026-10-16", "2026-11-20", _FAR]
_STRIKES = (490.0, 495.0, 500.0, 505.0, 510.0)


def _strikes(base):
    return {f"{k}": [{"mark": round(base + (k - 500.0) / 10.0, 2),
                      "bid": round(base + (k - 500.0) / 10.0 - 0.05, 2),
                      "ask": round(base + (k - 500.0) / 10.0 + 0.05, 2),
                      "delta": -0.28, "volatility": 18.0}]
            for k in _STRIKES}


def _chain_for(expiries, base):
    maps = {f"{e}:9": _strikes(base) for e in expiries}
    return {"callExpDateMap": dict(maps), "putExpDateMap": dict(maps)}


def _lazy_payload(loaded=(_NEAR, _MID), base=1.0, **extra):
    """What a lazy ``calc_load`` publishes: every expiration listed, strikes for
    the nearest two."""
    cc = {"symbol": "SPY", "price": 502.5, "expirations": list(_LISTED),
          "chain": _chain_for(loaded, base)}
    cc.update(extra)
    return cc


def _merged_far(failed=False):
    """What ``calc_load_expiry`` publishes after the far pick — the whole merged
    payload, marked ``added``."""
    if failed:
        return _lazy_payload(added=_FAR, failed=True)
    return _lazy_payload(loaded=(_NEAR, _MID, _FAR), base=4.0, added=_FAR)


# ── harness ─────────────────────────────────────────────────────────────────

def _walk(root):
    stack, out = [root], []
    while stack:
        el = stack.pop()
        out.append(el)
        for slot in getattr(el, "slots", {}).values():
            stack.extend(slot.children)
    return out


def _texts(root):
    return [el.text for el in _walk(root)
            if isinstance(getattr(el, "text", None), str)]


def _joined(root):
    return " | ".join(_texts(root))


def _poll(root):
    """The ad-hoc chain poll, BY NAME — identity, not arithmetic, so a timer
    added at render time cannot turn this file into silent skips."""
    timers = [el for el in _walk(root) if isinstance(el, ui.timer)]
    if not timers:
        pytest.skip("cannot collect any ui.timer from the page")
    by_name = {getattr(t.callback, "__name__", ""): t for t in timers}
    assert "_adhoc_poll_chain" in by_name, (
        "render() no longer registers _adhoc_poll_chain as a ui.timer callback — "
        f"the ad-hoc form paints no chain. Found: {sorted(by_name)}")
    return by_name["_adhoc_poll_chain"]


def _drive(root, poll):
    with root:
        result = poll.callback()
        if asyncio.iscoroutine(result):
            asyncio.new_event_loop().run_until_complete(result)


def _expiry_select(root):
    """The form's top Expiry dropdown: the labelled field holding a select. The
    leg table's header row has an "Expiry" label too, but no select beside it."""
    found = []
    for el in _walk(root):
        if getattr(el, "text", None) != "Expiry":
            continue
        slot = getattr(el, "parent_slot", None)
        siblings = slot.children if slot is not None else []
        found += [s for s in siblings if isinstance(s, ui.select)]
    assert len(found) == 1, f"expected one Expiry select, found {len(found)}"
    return found[0]


def _set_expiry(root, value):
    """Pick an expiry the way the user does — the assignment fires the page's
    own change handler (NiceGUI's ValueElement), nothing is called directly."""
    with root:
        _expiry_select(root).value = value


def _symbol_input(root):
    """The Symbol field — the labelled slot holding an input (the leg table's
    number boxes are inputs too, but none sits under a "Symbol" label)."""
    found = []
    for el in _walk(root):
        if getattr(el, "text", None) != "Symbol":
            continue
        slot = getattr(el, "parent_slot", None)
        siblings = slot.children if slot is not None else []
        found += [s for s in siblings if isinstance(s, ui.input)]
    assert len(found) == 1, f"expected one Symbol input, found {len(found)}"
    return found[0]


def _click(root, label):
    btn = [el for el in _walk(root)
           if isinstance(el, ui.button) and getattr(el, "text", "") == label]
    assert len(btn) == 1, f"expected one {label!r} button, found {len(btn)}"
    with root:
        for listener in list(getattr(btn[0], "_event_listeners", {}).values()):
            if listener.type == "click":
                arity = len(inspect.signature(listener.handler).parameters)
                listener.handler(*((None,) if arity else ()))


_HANDLES = {}


def _editor(_root=None):
    """The ad-hoc leg editor's handle, captured as render() builds it — the legs
    are the page's own state and there is no other way in."""
    return _HANDLES["editor"]


def _leg_expiries(root):
    return {leg.get("expiry") for leg in _editor(root).get_legs()}


@pytest.fixture
def page(monkeypatch):
    # The poll runs in its own task here, where NiceGUI cannot resolve a client
    # for a toast. Nothing under test depends on the notification.
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    bus_client.reset()
    _HANDLES.clear()
    from pages.options import leg_editor
    real_build = leg_editor.build_leg_editor

    def _capture(*a, **kw):
        handle = real_build(*a, **kw)
        _HANDLES["editor"] = handle
        return handle

    monkeypatch.setattr(leg_editor, "build_leg_editor", _capture)
    with ui.card() as root:
        rescue.render()
    return root, _poll(root)


@pytest.fixture
def sent(monkeypatch):
    out = []
    real = bus_client.request

    def _record(domain, command):
        out.append(command)
        return real(domain, command)

    monkeypatch.setattr(bus_client, "request", _record)
    return out


# ── the load ────────────────────────────────────────────────────────────────

def test_load_asks_for_every_listed_expiration(page, sent):
    root, _ = page
    _symbol_input(root).value = "SPY"
    _click(root, "Load")
    load = [c for c in sent if c["type"] == "calc_load"][-1]
    assert load["args"]["symbol"] == "SPY"
    assert load["args"]["lazy"] is True, (
        "the eager today→+60d fetch offers a handful of expirations and times "
        "out at the proxy on a big chain")
    assert isinstance(load["args"]["expiries"], list)


def test_a_reload_brings_the_expiries_already_in_use(page, sent):
    """A leg parked on a far expiry must come back WITH the reload, or its
    strike is coerced away before the form can ask for the ladder again."""
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    _set_expiry(root, _FAR)                     # fetched…
    bus_client.bus().cache_set("cache:options:calc_chain", _merged_far())
    _drive(root, poll)                          # …landed, legs now on the far one
    assert _leg_expiries(root) == {_FAR}
    _symbol_input(root).value = "SPY"
    _click(root, "Load")
    load = [c for c in sent if c["type"] == "calc_load"][-1]
    assert _FAR in load["args"]["expiries"]


def test_the_expiry_dropdown_offers_every_listed_expiration(page):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    assert list(_expiry_select(root).options) == _LISTED, (
        "the dropdown shows only the expiries whose strikes arrived")


def test_the_status_line_says_the_rest_load_on_demand(page):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    line = [t for t in _texts(root) if "expirations" in t]
    assert line, "no chain status line"
    assert "5 expirations" in line[0] and "2" in line[0]


def test_a_fully_loaded_chain_does_not_promise_more(page):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain",
                               _lazy_payload(loaded=_LISTED))
    _drive(root, poll)
    line = [t for t in _texts(root) if "expirations" in t][0]
    assert "5 expirations" in line and "pick" not in line


def test_an_eager_payload_still_lists_what_it_brought(page):
    """A payload with no ``expirations`` — an older eager shape, or a symbol
    whose expiration list could not be fetched — falls back to the loaded set."""
    root, poll = page
    cc = _lazy_payload()
    cc.pop("expirations")
    bus_client.bus().cache_set("cache:options:calc_chain", cc)
    _drive(root, poll)
    assert list(_expiry_select(root).options) == [_NEAR, _MID]


def test_no_chain_says_so(page):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain",
                               {"symbol": "ZZZZ", "chain": None})
    _drive(root, poll)
    assert "No chain data for that symbol." in _texts(root)


# ── picking an expiry whose strikes are not here yet ────────────────────────

def test_an_unloaded_pick_is_fetched_then_the_legs_move_when_it_lands(page, sent):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    _set_expiry(root, _FAR)

    req = [c for c in sent if c["type"] == "calc_load_expiry"]
    assert req and req[-1]["args"] == {"symbol": "SPY", "expiry": _FAR}
    assert _leg_expiries(root) != {_FAR}, "legs moved before the strikes arrived"
    assert f"Loading strikes for {_FAR}" in _joined(root)

    bus_client.bus().cache_set("cache:options:calc_chain", _merged_far())
    _drive(root, poll)
    assert _leg_expiries(root) == {_FAR}
    assert all(leg.get("strike") in _STRIKES for leg in _editor(root).get_legs())


def test_a_loaded_pick_moves_the_legs_with_no_fetch(page, sent):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    sent.clear()
    _set_expiry(root, _MID)
    assert not [c for c in sent if c["type"] == "calc_load_expiry"]
    assert _leg_expiries(root) == {_MID}


def test_a_merge_keeps_the_users_legs(page):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    handle = _editor(root)
    handle.set_legs([{"option_type": "put", "side": "short", "strike": 500.0,
                      "expiry": _NEAR, "qty": 3, "premium": 1.2},
                     {"option_type": "put", "side": "long", "strike": 495.0,
                      "expiry": _NEAR, "qty": 3, "premium": 0.6},
                     {"option_type": "call", "side": "long", "strike": 510.0,
                      "expiry": _NEAR, "qty": 1, "premium": 0.4}])
    _set_expiry(root, _FAR)
    bus_client.bus().cache_set("cache:options:calc_chain", _merged_far())
    _drive(root, poll)
    assert len(_editor(root).get_legs()) == 3, "a merge re-seeded the template"


def test_a_failed_fetch_stops_waiting_and_falls_back(page):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    _set_expiry(root, _FAR)
    bus_client.bus().cache_set("cache:options:calc_chain", _merged_far(failed=True))
    _drive(root, poll)
    joined = _joined(root)
    assert "Loading strikes for" not in joined
    assert f"Could not load strikes for {_FAR}" in joined
    assert _expiry_select(root).value in (_NEAR, _MID), (
        "the form still offers an expiry whose ladder is never coming")


def test_applying_a_chain_does_not_refetch_the_expiry_it_selects(page, sent):
    """Setting the dropdown from code must not fire the change handler — it would
    enqueue a fetch for an expiry the load just brought, every load."""
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    assert not [c for c in sent if c["type"] == "calc_load_expiry"]


# ── a LEG's own expiry dropdown ─────────────────────────────────────────────

def _leg_expiry_selects(root):
    """Every leg row's Expiry select: the selects holding expiry dates, less the
    form's top dropdown."""
    top = _expiry_select(root)

    def ordered(el):                 # document order, so index i is leg i
        yield el
        for slot in getattr(el, "slots", {}).values():
            for child in slot.children:
                yield from ordered(child)
    return [el for el in ordered(root)
            if isinstance(el, ui.select) and el is not top
            and _NEAR in list(el.options)]


def _set_leg_expiry(root, index, value):
    with root:
        _leg_expiry_selects(root)[index].value = value


def test_each_leg_expiry_dropdown_offers_every_listed_expiration(page):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    selects = _leg_expiry_selects(root)
    assert selects, "no leg expiry dropdowns found"
    for sel in selects:
        assert list(sel.options) == _LISTED, (
            "a leg's dropdown shows only the expiries whose strikes arrived")


def test_an_unloaded_leg_pick_is_fetched_and_the_leg_lands_on_its_ladder(page, sent):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    before = _editor(root).get_legs()
    _set_leg_expiry(root, 0, _FAR)

    req = [c for c in sent if c["type"] == "calc_load_expiry"]
    assert req and req[-1]["args"] == {"symbol": "SPY", "expiry": _FAR}
    legs = _editor(root).get_legs()
    assert legs[0]["expiry"] == _FAR, "the pick snapped back to a loaded expiry"
    assert legs[0]["strike"] == before[0]["strike"], (
        "the leg's strike was wiped while its ladder was loading")
    assert [l["expiry"] for l in legs[1:]] == [l["expiry"] for l in before[1:]], (
        "a single leg's pick moved the other legs")

    bus_client.bus().cache_set("cache:options:calc_chain", _merged_far())
    _drive(root, poll)
    legs = _editor(root).get_legs()
    assert legs[0]["expiry"] == _FAR
    assert legs[0]["strike"] in _STRIKES


def test_a_loaded_leg_pick_fetches_nothing(page, sent):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    sent.clear()
    _set_leg_expiry(root, 0, _MID)
    assert not [c for c in sent if c["type"] == "calc_load_expiry"]
    assert _editor(root).get_legs()[0]["expiry"] == _MID


def test_a_failed_leg_fetch_puts_the_leg_back_on_a_loaded_expiry(page):
    root, poll = page
    bus_client.bus().cache_set("cache:options:calc_chain", _lazy_payload())
    _drive(root, poll)
    _set_leg_expiry(root, 0, _FAR)
    bus_client.bus().cache_set("cache:options:calc_chain", _merged_far(failed=True))
    _drive(root, poll)
    assert f"Could not load strikes for {_FAR}" in _joined(root)
    assert _editor(root).get_legs()[0]["expiry"] in (_NEAR, _MID)
