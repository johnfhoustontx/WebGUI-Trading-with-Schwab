"""The public Gamma page (``gamma.render(public=True)``): any dropdown symbol,
the app's subtabs, and exactly one write.

Driven through the REAL render and its own timer callbacks, against the fake
bus, because the promises here are about what the page reads and sends, and a
source-level check of a closure proves neither.
"""
import asyncio
import datetime as dt

import pytest

import bus_client
import visitor_limit
from pages.options import gamma
from pages.options import handoff
from shared import public_gamma as pg

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 9, 22, 15, 0, tzinfo=UTC)


def _rendered(**kw):
    from nicegui import ui
    bus_client.reset()
    with ui.card() as card:
        gamma.render(**kw)
    return list(card.descendants())


def _timer(kids, name):
    found = [t.callback for t in kids if type(t).__name__ == "Timer"
             and getattr(t.callback, "__name__", "") == name]
    assert len(found) == 1, f"timer {name} moved"
    return found[0]


def _tab_names(kids):
    return {e.props.get("name") for e in kids if type(e).__name__ == "Tab"} \
        & set(gamma._VIEW_ORDER)


def _labels(kids):
    out = set()
    for e in kids:
        for attr in ("text", "_text"):
            t = getattr(e, attr, None)
            if isinstance(t, str) and t:
                out.add(t)
        lbl = e.props.get("label") if hasattr(e, "props") else None
        if isinstance(lbl, str):
            out.add(lbl)
    return out


@pytest.fixture
def writes(monkeypatch):
    """Every write the page tries: the public one, and the generic one."""
    sent = {"public": [], "request": []}
    monkeypatch.setattr(bus_client, "request_public_gamma",
                        lambda sym: sent["public"].append(sym) or "1-0")
    monkeypatch.setattr(bus_client, "request",
                        lambda *a, **k: sent["request"].append(a))
    monkeypatch.setattr(gamma, "LIMITER", visitor_limit.Limiter(lambda: 30))
    return sent


def _seed_list(symbols=("$SPX", "SPY", "QQQ", "NVDA", "AAPL")):
    bus_client.bus().cache_set(f"cache:{pg.SYMBOLS_VIEW}", {"symbols": list(symbols)})


# ── what it builds ──────────────────────────────────────────────────────────

def test_it_offers_the_apps_views_but_term_and_net_prem():
    kids = _rendered(public=True)
    assert _tab_names(kids) == set(gamma.PUBLIC_VIEW_ORDER)
    assert gamma.PUBLIC_VIEW_ORDER == ["GEX", "Charm", "DEX", "Vanna", "Flow"]


def test_it_builds_no_command_control_and_schedules_no_enqueue():
    kids = _rendered(public=True)
    labels = _labels(kids)
    for caption in ("Explain", "Analyze", "Briefings", "Refresh", "History:"):
        assert caption not in labels, caption
    timers = {getattr(t.callback, "__name__", "") for t in kids
              if type(t).__name__ == "Timer"}
    assert "_auto_refresh" not in timers
    assert {"_poll", "_initial_load_public", "_public_renew", "_public_stamp"} <= timers
    assert gamma.may_enqueue(None, None, public=True) is False


def test_the_dropdown_offers_the_published_list_and_only_it():
    bus_client.reset()
    _seed_list()
    from nicegui import ui
    with ui.card() as card:
        gamma.render(public=True)
    selects = [e for e in card.descendants() if type(e).__name__ == "Select"
               and e.props.get("label") == "Symbol"]
    assert len(selects) == 1
    assert selects[0].options == ["$SPX", "SPY", "QQQ", "NVDA", "AAPL"]


def test_the_build_never_reads_a_private_key():
    seen, orig = [], (bus_client.read, bus_client.read_version)
    bus_client.read = lambda v: (seen.append(v), orig[0](v))[1]
    bus_client.read_version = lambda v: (seen.append(v), orig[1](v))[1]
    try:
        _rendered(public=True)
    finally:
        bus_client.read, bus_client.read_version = orig
    for private in ("options:gamma", "options:gamma_symbols", "options:gamma_explain",
                    "options:gamma_analyze", "options:gamma_briefings",
                    "options:net_premium"):
        assert private not in seen, private
    assert pg.SYMBOLS_VIEW in seen and pg.STATUS_VIEW in seen


def test_the_poll_follows_the_symbol_on_screen():
    kids = _rendered(public=True)
    asked = []
    orig = bus_client.read_versions
    bus_client.read_versions = lambda vs: (asked.append(list(vs)), orig(vs))[1]
    try:
        asyncio.run(_timer(kids, "_poll")())
    finally:
        bus_client.read_versions = orig
    assert asked == [gamma.polled_views("$SPX", None, public=True)]
    assert asked[0] == ["options:gex_status", pg.STATUS_VIEW, "options:gamma_pub:$SPX"]


# ── what it sends ───────────────────────────────────────────────────────────

def test_the_first_load_asks_for_its_symbol_through_the_one_public_write(writes):
    kids = _rendered(public=True)
    asyncio.run(_timer(kids, "_initial_load_public")())
    assert writes["public"] == ["$SPX"]
    assert writes["request"] == []


def test_a_renewal_resends_the_symbol_on_screen_and_nothing_else(writes):
    kids = _rendered(public=True)
    _timer(kids, "_public_renew")()
    assert writes["public"] == ["$SPX"] and writes["request"] == []


def test_the_visitor_limit_stops_a_pick_before_it_is_sent(writes, monkeypatch):
    monkeypatch.setattr(gamma, "LIMITER", visitor_limit.Limiter(lambda: 1))
    kids = _rendered(public=True)
    asyncio.run(_timer(kids, "_initial_load_public")())        # the one allowed
    asyncio.run(_timer(kids, "_initial_load_public")())        # refused
    assert writes["public"] == ["$SPX"]
    assert gamma.PUBLIC_LIMITED in _labels(kids)
    _timer(kids, "_public_renew")()                            # no walking around it
    assert writes["public"] == ["$SPX"]


def test_the_page_adds_no_symbol_to_its_own_list():
    """``_set_symbol`` appends an unknown symbol on the private page. On the
    public one the list is the service's, so a hand-off naming anything else
    falls back to $SPX instead of widening it."""
    bus_client.reset()
    _seed_list()
    handoff._pending["gamma"] = "ZZZZ"
    from nicegui import ui
    with ui.card() as card:
        gamma.render(public=True)
    kids = list(card.descendants())
    asyncio.run(_timer(kids, "_initial_load_public")())
    sel = next(e for e in kids if type(e).__name__ == "Select"
               and e.props.get("label") == "Symbol")
    assert sel.value == "$SPX" and "ZZZZ" not in sel.options


# ── the line under the dropdown ─────────────────────────────────────────────

def _st(**kw):
    base = {"permanent": ["$SPX", "QQQ", "SPY"], "hot": [], "cap": 8, "last": {}}
    return {**base, **kw}


def _at(sec):
    return (T0 + dt.timedelta(seconds=sec)).isoformat()


def test_a_permanent_symbol_is_live():
    assert gamma.public_status_text("SPY", _st(), T0, T0, True) == \
        pg.OUTCOME_TEXT["live"]


def test_a_hot_symbol_with_no_data_yet_is_loading():
    st = _st(hot=["NVDA"], last={"NVDA": {"outcome": "added", "at": _at(1)}})
    assert gamma.public_status_text("NVDA", st, T0, T0, False) == \
        pg.OUTCOME_TEXT["added"]
    assert gamma.public_status_text("NVDA", st, T0, T0, True) == \
        pg.OUTCOME_TEXT["live"]


def test_full_and_closed_are_worded_when_they_answer_this_request():
    for outcome in ("full", "closed"):
        st = _st(last={"NVDA": {"outcome": outcome, "at": _at(1)}})
        assert gamma.public_status_text("NVDA", st, T0, T0, False) == \
            pg.OUTCOME_TEXT[outcome]


def test_an_outcome_older_than_the_request_answers_nothing():
    st = _st(last={"NVDA": {"outcome": "full", "at": _at(-600)}})
    assert gamma.public_status_text("NVDA", st, T0, T0, False) == gamma.PUBLIC_SENDING
    later = T0 + dt.timedelta(seconds=gamma.PUBLIC_ANSWER_SEC + 1)
    assert gamma.public_status_text("NVDA", st, T0, later, False) == \
        gamma.PUBLIC_NOT_ANSWERING


def test_a_refused_or_failed_send_is_said_unless_the_symbol_is_live_anyway():
    assert gamma.public_status_text("NVDA", _st(), None, T0, False,
                                    limited=True) == gamma.PUBLIC_LIMITED
    assert gamma.public_status_text("NVDA", _st(), None, T0, False,
                                    failed=True) == gamma.PUBLIC_SEND_FAILED
    assert gamma.public_status_text("SPY", _st(), None, T0, True,
                                    limited=True) == pg.OUTCOME_TEXT["live"]


def test_junk_status_never_raises():
    for junk in (None, [], "x", {"last": "x"}, {"permanent": None},
                 {"last": {"NVDA": "x"}}, {"hot": "NVDA"},
                 {"last": {"NVDA": {"outcome": "full", "at": "not a time"}}}):
        assert gamma.public_status_text("NVDA", junk, T0, T0, False)


# ── the hand-off from the public Flow Alerts screen ─────────────────────────

def test_on_the_public_origin_the_hand_off_is_per_tab(monkeypatch):
    """The module stash is one value for every visitor; the public origin must
    not use it, or one visitor's click moves the next visitor's page."""
    tab = {}
    monkeypatch.setattr(handoff._shell, "is_public", lambda: True)
    monkeypatch.setattr(handoff, "_tab", lambda: tab)
    handoff._pending["gamma"] = None
    handoff.set_pending_gamma(" nvda ")
    assert handoff._pending["gamma"] is None
    assert tab == {handoff._TAB_GAMMA: "NVDA"}
    assert handoff.take_pending_gamma() == "NVDA"
    assert handoff.take_pending_gamma() is None          # consume-once


def test_a_lost_tab_store_loses_the_hand_off_quietly(monkeypatch):
    def gone():
        raise RuntimeError("no socket yet")
    monkeypatch.setattr(handoff._shell, "is_public", lambda: True)
    monkeypatch.setattr(handoff, "_tab", gone)
    handoff.set_pending_gamma("NVDA")                    # must not raise
    assert handoff.take_pending_gamma() is None


def test_the_private_hand_off_is_unchanged(monkeypatch):
    monkeypatch.setattr(handoff._shell, "is_public", lambda: False)
    handoff.set_pending_gamma("AMD")
    assert handoff.take_pending_gamma() == "AMD"
    assert handoff.take_pending_gamma() is None
