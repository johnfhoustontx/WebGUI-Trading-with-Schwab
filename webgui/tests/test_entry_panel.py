"""The shared entry panel: expiry strip, chain grid, column picker, picks."""
import datetime as dt
import re

import pytest
from nicegui import ui

import app_settings
from pages.options import entry_panel as EP

_TODAY = dt.date(2026, 9, 12)


def _c(bid=1.0, ask=1.2, **kw):
    base = {"bid": bid, "ask": ask, "mark": (bid + ask) / 2, "delta": 0.4,
            "volatility": 20.0, "openInterest": 4120, "totalVolume": 88,
            "gamma": 0.03, "theta": -0.05, "vega": 0.1}
    base.update(kw)
    return [base]


def _chain(strikes=range(500, 651, 5)):
    return {
        "callExpDateMap": {"2026-09-19:7": {f"{k}.0": _c() for k in strikes},
                           "2026-09-26:14": {f"{k}.0": _c() for k in strikes}},
        "putExpDateMap": {"2026-09-19:7": {f"{k}.0": _c(bid=2.0, ask=2.2) for k in strikes},
                          "2026-09-26:14": {f"{k}.0": _c() for k in strikes}},
    }


@pytest.fixture
def saved(monkeypatch):
    store = {}
    monkeypatch.setattr(app_settings, "get", lambda key: store.get(key))
    monkeypatch.setattr(app_settings, "set", lambda key, value: store.__setitem__(key, value))
    return store


def _panel(**kw):
    kw.setdefault("today", _TODAY)
    with ui.card() as root:
        panel = EP.build_entry_panel(**kw)
    return panel, root


def _all(root, cls):
    return [e for e in root.descendants() if cls in e._classes]


def _fire(el, event):
    for listener in list(el._event_listeners.values()):
        if listener.type == event:
            listener.handler(None)


def test_panel_has_exactly_one_ticker_input(saved):
    _, root = _panel()
    assert len(_all(root, "entry-ticker")) == 1


def test_no_chain_shows_the_empty_line_and_no_rows(saved):
    _, root = _panel()
    assert not _all(root, "entry-grow")
    assert [e.text for e in _all(root, "entry-empty")] == ["Load a symbol to see its chain."]


def test_set_chain_paints_the_nearest_expiry_and_a_window_around_spot(saved):
    panel, root = _panel()
    assert panel.set_chain(_chain(), 571.0) == "2026-09-19"
    strikes = [float(e.text) for e in _all(root, "entry-strike")]
    assert len(strikes) == 2 * EP.GRID_WINDOW
    assert strikes[EP.GRID_WINDOW - 1] <= 571.0 < strikes[EP.GRID_WINDOW]
    assert [e.text for e in _all(root, "entry-expiry")] == ["Sep 19 · 7d", "Sep 26 · 14d"]


def test_set_chain_keeps_a_requested_expiry_then_the_current_one(saved):
    panel, _ = _panel()
    assert panel.set_chain(_chain(), 571.0, expiry="2026-09-26") == "2026-09-26"
    assert panel.set_chain(_chain(), 572.0) == "2026-09-26"          # still listed
    assert panel.set_chain(_chain(), 572.0, expiry="2031-01-01") == "2026-09-26"


def test_the_atm_strike_is_marked(saved):
    panel, root = _panel()
    panel.set_chain(_chain(), 571.0)
    atm = [e.text for e in _all(root, "entry-atm")]
    assert atm == ["570"]


def test_clicking_a_put_bid_picks_bid_put_strike_expiry(saved):
    panel, root = _panel()
    picks = []
    panel.on_pick(lambda *a: picks.append(a))
    panel.set_chain(_chain(), 571.0)
    rows = _all(root, "entry-grow")
    target = [e for e in rows[EP.GRID_WINDOW - 1].descendants()
              if "entry-put-bid" in e._classes][0]
    _fire(target, "click")
    assert picks == [("bid", "put", 570.0, "2026-09-19")]


def test_clicking_a_call_ask_picks_ask_call(saved):
    panel, root = _panel()
    picks = []
    panel.on_pick(lambda *a: picks.append(a))
    panel.set_chain(_chain(), 571.0)
    _fire(_all(root, "entry-call-ask")[0], "click")
    assert picks[0][:2] == ("ask", "call")


def test_bid_and_ask_sit_next_to_the_strike_on_both_sides():
    calls, puts = EP.side_columns(["bid", "ask", "delta", "openInterest"])
    assert calls == ["openInterest", "delta", "bid", "ask"]
    assert puts == ["bid", "ask", "delta", "openInterest"]


def test_clicking_an_expiry_pill_repaints_and_notifies(saved):
    panel, root = _panel()
    seen = []
    panel.on_expiry(seen.append)
    panel.set_chain(_chain(), 571.0)
    _fire(_all(root, "entry-expiry")[1], "click")
    assert seen == ["2026-09-26"]
    assert panel.selected_expiry() == "2026-09-26"
    on = EP.DEFAULT_PANEL_TOKENS["pill_on"].split()[0]
    assert [on in e._classes for e in _all(root, "entry-expiry")] == [False, True]


def test_set_expiry_is_programmatic_and_fires_nothing(saved):
    panel, _ = _panel()
    seen = []
    panel.on_expiry(seen.append)
    panel.set_chain(_chain(), 571.0)
    panel.set_expiry("2026-09-26")
    assert panel.selected_expiry() == "2026-09-26" and seen == []


def test_more_strikes_below_extends_the_window(saved):
    panel, root = _panel()
    panel.set_chain(_chain(), 571.0)
    before = len(_all(root, "entry-grow"))
    _fire(_all(root, "entry-more-below")[0], "click")
    assert len(_all(root, "entry-grow")) > before


def test_the_grid_uses_the_saved_columns_and_the_picker_persists(saved):
    saved[EP.SETTINGS_KEY] = ["bid", "ask", "gamma"]
    panel, root = _panel()
    panel.set_chain(_chain(), 571.0)
    heads = [e.text for e in _all(root, "entry-ghead")[0].descendants()
             if isinstance(e, ui.label)]
    assert heads == ["Gamma", "Bid", "Ask", "STRIKE", "Bid", "Ask", "Gamma"]
    box = _all(root, "entry-col-openInterest")[0]
    box.value = True
    assert saved[EP.SETTINGS_KEY] == ["bid", "ask", "gamma", "openInterest"]
    heads = [e.text for e in _all(root, "entry-ghead")[0].descendants()
             if isinstance(e, ui.label)]
    assert "OI" in heads


def test_bid_and_ask_cannot_be_unticked(saved):
    _, root = _panel()
    assert not _all(root, "entry-col-bid")[0].enabled
    assert not _all(root, "entry-col-ask")[0].enabled


def test_an_unlisted_expiry_says_so(saved):
    panel, root = _panel()
    panel.set_chain({"callExpDateMap": {"2026-09-19:7": {}}, "putExpDateMap": {}}, 571.0)
    assert [e.text for e in _all(root, "entry-empty")] == ["No strikes listed for this expiry."]


def test_panel_tokens_merge_and_ignore_junk():
    merged = EP.panel_tokens({"frame": "border-[#000]", "bogus": "x", "text": " "})
    assert merged["frame"] == "border-[#000]"
    assert "bogus" not in merged
    assert merged["text"] == EP.DEFAULT_PANEL_TOKENS["text"]
    assert EP.panel_tokens("frame") == EP.DEFAULT_PANEL_TOKENS


def test_panel_classes_are_spaceless_tailwind_arbitraries():
    for src in list(EP.DEFAULT_PANEL_TOKENS.values()) + list(EP._GRID_TRACKS.values()):
        for arb in re.findall(r"\[[^\]]*\]", src):
            assert " " not in arb, src
