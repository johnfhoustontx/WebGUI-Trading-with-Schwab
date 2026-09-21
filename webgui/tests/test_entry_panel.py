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


def _fire(el, event, args=None):
    from types import SimpleNamespace
    for listener in list(el._event_listeners.values()):
        if listener.type == event:
            listener.handler(SimpleNamespace(args=args) if args is not None else None)


def _body(root):
    return _all(root, "entry-gridbody")[0]


def _strikes(root):
    """The strikes the grid body lists, in order (the rows are one html block)."""
    return [float(k) for k in re.findall(r'class="entry-grow[^"]*" data-strike="([^"]+)"',
                                         _body(root).content)]


def _click_cell(root, pick, side, strike):
    _fire(_body(root), "click", {"pick": pick, "side": side, "strike": f"{strike:g}"})


def test_panel_has_exactly_one_ticker_input(saved):
    _, root = _panel()
    assert len(_all(root, "entry-ticker")) == 1


def test_no_chain_shows_the_empty_line_and_no_rows(saved):
    _, root = _panel()
    assert _strikes(root) == []
    assert _all(root, "entry-empty")[0].visible
    assert [e.text for e in _all(root, "entry-empty")] == ["Load a symbol to see its chain."]
    assert not _body(root).visible


def test_set_chain_paints_the_nearest_expiry_and_the_complete_ladder(saved):
    panel, root = _panel()
    assert panel.set_chain(_chain(), 571.0) == "2026-09-19"
    assert _strikes(root) == [float(k) for k in range(500, 651, 5)]     # every strike
    assert _body(root).visible and not _all(root, "entry-empty")[0].visible
    assert [e.text for e in _all(root, "entry-expiry")] == ["Sep 19 · 7d", "Sep 26 · 14d"]


def test_set_chain_keeps_a_requested_expiry_then_the_current_one(saved):
    panel, _ = _panel()
    assert panel.set_chain(_chain(), 571.0, expiry="2026-09-26") == "2026-09-26"
    assert panel.set_chain(_chain(), 572.0) == "2026-09-26"          # still listed
    assert panel.set_chain(_chain(), 572.0, expiry="2031-01-01") == "2026-09-26"


def test_the_atm_strike_is_marked_for_centring(saved):
    panel, root = _panel()
    panel.set_chain(_chain(), 571.0)
    assert re.findall(r'data-strike="([^"]+)" data-atm', _body(root).content) == ["570"]


def test_center_js_scrolls_only_the_body_to_the_atm_row():
    js = EP.center_js(42)
    assert "getHtmlElement(42)" in js and "[data-atm]" in js
    assert "b.scrollTop" in js and "scrollIntoView" not in js   # never the page


def test_clicking_a_put_bid_picks_bid_put_strike_expiry(saved):
    panel, root = _panel()
    picks = []
    panel.on_pick(lambda *a: picks.append(a))
    panel.set_chain(_chain(), 571.0)
    _click_cell(root, "bid", "put", 570.0)
    assert picks == [("bid", "put", 570.0, "2026-09-19")]


def test_clicking_a_call_ask_picks_ask_call(saved):
    panel, root = _panel()
    picks = []
    panel.on_pick(lambda *a: picks.append(a))
    panel.set_chain(_chain(), 571.0)
    _click_cell(root, "ask", "call", 505.0)
    assert picks == [("ask", "call", 505.0, "2026-09-19")]


def test_a_click_that_is_not_a_well_formed_pick_does_nothing(saved):
    panel, root = _panel()
    picks = []
    panel.on_pick(lambda *a: picks.append(a))
    panel.set_chain(_chain(), 571.0)
    for junk in ({}, {"pick": "mark", "side": "put", "strike": "570"},
                 {"pick": "bid", "side": "stock", "strike": "570"},
                 {"pick": "bid", "side": "put", "strike": "abc"},
                 {"pick": "bid", "side": "put", "strike": "nan"}):
        _fire(_body(root), "click", junk)
    assert picks == []


def test_the_default_columns_read_delta_oi_volume_bid_ask_then_mirror():
    calls, puts = EP.side_columns(None)
    assert calls == ["delta", "openInterest", "totalVolume", "bid", "ask"]
    assert puts == ["ask", "bid", "totalVolume", "openInterest", "delta"]


def test_bid_and_ask_sit_next_to_the_strike_on_both_sides():
    calls, puts = EP.side_columns(["gamma", "bid", "ask", "delta"])
    assert calls[-2:] == ["bid", "ask"] and puts[:2] == ["ask", "bid"]


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


def test_the_grid_uses_the_saved_columns_and_the_picker_persists(saved):
    saved[EP.SETTINGS_KEY] = ["bid", "ask", "gamma"]
    panel, root = _panel()
    panel.set_chain(_chain(), 571.0)
    heads = [e.text for e in _all(root, "entry-ghead")[0].descendants()
             if isinstance(e, ui.label)]
    assert heads == ["Gamma", "Bid", "Ask", "STRIKE", "Ask", "Bid", "Gamma"]
    box = _all(root, "entry-col-openInterest")[0]
    box.value = True
    assert saved[EP.SETTINGS_KEY] == ["gamma", "openInterest", "bid", "ask"]
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
    assert _all(root, "entry-empty")[0].visible and _strikes(root) == []


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


# ── every listed expiration, strikes on demand (2026-09-12) ─────────────────

_ALL = ["2026-09-19", "2026-09-26", "2026-10-30", "2026-11-20"]


def test_the_strip_lists_every_expiration_not_just_the_loaded_ones(saved):
    panel, root = _panel()
    panel.set_chain(_chain(), 571.0, expirations=_ALL)
    assert [e.text for e in _all(root, "entry-expiry")] == [
        "Sep 19 · 7d", "Sep 26 · 14d", "Oct 30 · 48d", "Nov 20 · 69d"]


def test_clicking_an_unloaded_expiration_notifies_and_says_it_is_loading(saved):
    panel, root = _panel()
    seen = []
    panel.on_expiry(seen.append)
    panel.set_chain(_chain(), 571.0, expirations=_ALL)
    _fire(_all(root, "entry-expiry")[3], "click")
    assert seen == ["2026-11-20"] and panel.selected_expiry() == "2026-11-20"
    assert _strikes(root) == []
    assert [e.text for e in _all(root, "entry-empty")] == ["Loading strikes for Nov 20…"]


def test_set_chain_keeps_an_unloaded_selection_that_is_still_listed(saved):
    panel, root = _panel()
    panel.set_chain(_chain(), 571.0, expirations=_ALL)
    panel.set_expiry("2026-11-20")
    assert panel.selected_expiry() == "2026-11-20"
    chain = _chain()
    chain["callExpDateMap"]["2026-11-20:69"] = chain["callExpDateMap"]["2026-09-19:7"]
    chain["putExpDateMap"]["2026-11-20:69"] = chain["putExpDateMap"]["2026-09-19:7"]
    assert panel.set_chain(chain, 571.0, expirations=_ALL) == "2026-11-20"
    assert len(_strikes(root)) == 31


def test_is_loaded_reports_whether_strikes_have_arrived(saved):
    panel, _ = _panel()
    panel.set_chain(_chain(), 571.0, expirations=_ALL)
    assert panel.is_loaded("2026-09-19") is True
    assert panel.is_loaded("2026-11-20") is False


# ── the kit migration (Phase 2, Task 2) ──────────────────────────────────────
# Two of the panel's three buttons are actions and go through ``pages/ui_kit``.
# The third - the expiry pill - is a segmented picker rendered N times per
# repaint, and stays raw with its reason in the guard's ALLOWED.


def test_the_panels_go_button_says_load_and_is_the_pages_refresh(saved):
    """A page with a Symbol control bar has no header Refresh, because its Load
    button IS the refresh - ``rescue.py`` already ships exactly that shape. The
    word changes from REFRESH; the behaviour (re-pull this symbol's chain and
    quotes) does not, and both pages still wire it through ``panel.refresh_btn``."""
    from pages.options import theme
    panel, root = _panel()
    btn = panel.refresh_btn
    assert btn.text == "Load"
    assert btn._props.get("icon") == "refresh"
    assert "entry-refresh" in btn.classes
    for tok in theme.BTN_PRIMARY.split():
        assert tok in btn.classes
    assert [e.text for e in btn.descendants() if isinstance(e, ui.tooltip)] == \
        ["Re-pull the chain and quotes"]
    assert btn in _all(root, "entry-refresh")


def test_the_columns_button_is_a_kit_secondary_and_still_hosts_its_menu(saved):
    """The column picker mounts as a CHILD of the trigger, so the kit button has
    to keep working as the menu's anchor."""
    from pages.options import theme
    _, root = _panel()
    btn = _all(root, "entry-columns")[0]
    assert btn.text == "Columns"
    assert btn._props.get("icon") == "view_column"
    for tok in theme.BTN.split():
        assert tok in btn.classes
    assert [e for e in btn.descendants() if isinstance(e, ui.menu)], \
        "the columns menu no longer hangs off its trigger"


def test_the_expiry_pill_stays_raw_and_keeps_its_two_state_swap(saved):
    """MUST NOT CHANGE - passes on both sides. A segmented picker, one per
    listed expiration per repaint, whose selected state is a class SWAP; the
    kit's four button kinds carry no selected state, so routing it through them
    would mean a page-side swap over ``button_classes(...)`` - the exact drift
    the kit exists to stop."""
    panel, root = _panel()
    panel.set_chain(_chain(), 571.0)
    pills = _all(root, "entry-expiry")
    on = EP.DEFAULT_PANEL_TOKENS["pill_on"].split()[0]
    off = EP.DEFAULT_PANEL_TOKENS["pill_off"].split()[0]
    assert [on in p._classes for p in pills] == [True, False]
    assert [off in p._classes for p in pills] == [False, True]


def test_the_panel_tokens_keep_every_encoding_and_lose_the_button_skin():
    """bid-green, ask-red, the ATM amber, the ITM wash and the pill pair are
    READINGS and are untouched. ``btn`` was the skin on the two controls the kit
    now paints, and a token nothing reads is the half-live defect this phase
    keeps finding."""
    for reading in ("bid", "ask", "strike", "strike_atm", "itm", "cell", "pick",
                    "pill_on", "pill_off", "rule", "frame", "eyebrow", "text",
                    "muted", "spot", "ticker"):
        assert reading in EP.DEFAULT_PANEL_TOKENS, reading
    assert "btn" not in EP.DEFAULT_PANEL_TOKENS


# ── the public Calculator's panel: no grid, stacked (2026-09-21) ────────────

def test_grid_false_builds_no_chain_grid_and_no_columns_picker(saved):
    panel, root = _panel(grid=False)
    for cls in ("entry-grid", "entry-gridbody", "entry-columns", "entry-empty"):
        assert _all(root, cls) == [], cls
    # the strip still lists every expiration and the legs box is still there
    panel.set_chain(_chain(), 575.0)
    assert len(_all(root, "entry-expiry")) == 2
    assert _all(root, "entry-legs")


def test_the_default_panel_still_builds_the_grid(saved):
    _, root = _panel()
    for cls in ("entry-grid", "entry-gridbody", "entry-columns"):
        assert len(_all(root, cls)) == 1, cls


def test_stack_lets_the_legs_column_shrink_to_a_phone(saved):
    _, root = _panel(stack=True)
    legs = _all(root, "entry-legs")[0]
    assert "min-w-[430px]" not in legs._classes and "min-w-0" in legs._classes
    _, root = _panel()
    assert "min-w-[430px]" in _all(root, "entry-legs")[0]._classes


def test_fixed_columns_draw_only_those_and_build_no_picker(saved):
    saved[EP.SETTINGS_KEY] = ["gamma", "openInterest", "bid", "ask"]
    panel, root = _panel(columns=["delta", "mark", "bid", "ask"], stack=True)
    assert _all(root, "entry-columns") == []
    panel.set_chain(_chain(), 575.0)
    heads = [e.text for e in _all(root, "entry-ghead")[0].descendants()
             if isinstance(e, ui.label)]
    assert "Gamma" not in heads and "OI" not in heads
    assert {"Delta", "Mark", "Bid", "Ask"} <= set(heads)
