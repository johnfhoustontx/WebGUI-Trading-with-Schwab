"""Tests for the Swing Scanner page (Tier-3 reader).

The scan pipeline (``screen_spreads`` + ``build_iron_condors`` +
``score_all_signals``, plus ``assign_ids``) moved to
``services/options_svc/compute.swing_scan`` — see that service's tests. The page
now only converts the credit-% input to a fraction (``pct_to_fraction``),
enqueues a ``swing_scan`` command, and reads the result from the Redis bus. So
the page must import NO engine / proxy / scoring code.
"""
import inspect

import bus_client
from pages.options import swing


def test_render_callable():
    assert callable(swing.render)


def test_pct_to_fraction():
    # screen_spreads expects a fraction (0.10), not a percent (10) — regression guard.
    assert swing.pct_to_fraction(10) == 0.10
    assert swing.pct_to_fraction(0) == 0.0


def test_page_imports_no_engine_or_proxy():
    """Regression: the Tier-3 page must not pull in engine / proxy / scoring code."""
    for attr in ("proxy", "scanner_engine", "run_iv_analysis", "scoring",
                 "engines", "OPTIONS_SCANNER", "sys", "assign_ids", "_swing_scan"):
        assert not hasattr(swing, attr), f"swing.py still references {attr}"
    # Also guard the literal import lines so the strings never creep back.
    src = inspect.getsource(swing)
    for forbidden in ("scanner_engine", "OPTIONS_SCANNER", "options_scoring",
                      "import proxy", "run_iv_analysis", "import scoring",
                      "from . import engines", "_swing_scan"):
        assert forbidden not in src, f"swing.py must not reference {forbidden!r}"


# The eight ``status_text`` tests were removed with the function (2026-09-13,
# Strategy Finder redesign): the count line now lives in the summary strip,
# ``finder_view.summary_facts``, whose tests carry the same cases - the quality
# cut, the volatility drop as its own sentence, both together, and a payload
# written before ``vol_filtered`` existed.

def test_status_text_moved_to_the_summary_strip():
    assert not hasattr(swing, "status_text")


def test_family_options_alias_the_finder_groups():
    from pages.options import finder_view
    assert swing._FAMILY_OPTIONS == dict(finder_view.GROUPS)


def test_scan_params_send_no_families():
    """Every scan builds all seven groups; the chips filter on the page. The
    service's ``families=None`` default is what builds them all."""
    params = swing.scan_params(" spy ", 7, 14.0, {"put_d_min": -0.2, "put_d_max": -0.1,
                                                  "call_d_min": 0.1, "call_d_max": 0.2}, 10)
    assert params == {"symbol": "SPY", "dte_min": 7, "dte_max": 14,
                      "put_d_min": -0.2, "put_d_max": -0.1,
                      "call_d_min": 0.1, "call_d_max": 0.2, "min_cr_fraction": 0.10}
    assert "families" not in params


def test_scan_params_tolerate_an_empty_symbol_box():
    params = swing.scan_params(None, 0, 120, {"put_d_min": -0.2, "put_d_max": -0.1,
                                              "call_d_min": 0.1, "call_d_max": 0.2}, 10)
    assert params["symbol"] == ""


_FLY = {"id": "fly", "type": "BUTTERFLY_CALL", "group": "BUTTERFLY",
        "strategy_label": "Call Butterfly", "composite_score": 72.1, "grade": "Good",
        "expiration": "2026-10-16", "dte": 30, "net_debit": 120.0, "max_profit": 374.8,
        "max_loss": 125.2, "pop_pct": 31.5, "underlying_price": 100.0,
        "legs": [{"side": "long", "kind": "call", "strike": 95.0, "qty": 1},
                 {"side": "short", "kind": "call", "strike": 100.0, "qty": 2},
                 {"side": "long", "kind": "call", "strike": 105.0, "qty": 1}],
        "payoff_curve": [[90, -120], [100, 380], [110, -120]]}
_NAKED = {"id": "sp", "type": "SHORT_PUT", "group": "DIRECTIONAL", "composite_score": 60.0,
          "grade": "Marginal", "net_credit": 240.0, "max_profit": 240.0,
          "max_loss": 10866.0, "unbounded_loss": True}


def test_finder_rows_wire_the_real_classes_and_paper_gate():
    from pages.options import scanner, strategy_table
    rows = {r["id"]: r for r in swing.finder_rows([_FLY, _NAKED])}
    assert rows["fly"]["_score_class"] == scanner.score_zone_class(72.1)
    assert rows["fly"]["_grade_class"] == strategy_table.grade_class("Good")
    assert rows["fly"]["_allow_paper"] is True
    assert rows["sp"]["_allow_paper"] is False      # naked shorts: analysis only
    assert rows["sp"]["_grade_class"] == strategy_table.grade_class("Marginal")


def test_card_view_adds_legs_and_the_paper_gate():
    from pages.options import strategy_table
    card = swing.card_view(_FLY)
    assert card["legs"] == strategy_table.legs_summary(_FLY["legs"]) == "L 95C / S 2×100C / L 105C"
    assert card["allow_paper"] is True and card["title"] == "Call Butterfly"
    assert card["score_class"] == swing.finder_rows([_FLY])[0]["_score_class"]
    assert card["grade_class"] == strategy_table.grade_class("Good")
    assert swing.card_view(_NAKED)["allow_paper"] is False


def test_scanning_text_names_the_symbol_being_scanned():
    assert swing.scanning_text("spy") == "Scanning SPY…"
    assert swing.scanning_text("") == "Scanning…"


def test_empty_line_before_any_scan():
    assert swing.EMPTY_PROMPT == "Enter a symbol and press Scan to rank every strategy for it."


def test_render_paints_a_cached_scan():
    """A published payload paints cards, chips and the list without raising."""
    from nicegui import ui

    bus_client.reset()
    payload = {"symbol": "SPY", "signals": [_FLY, _NAKED], "filtered_out": 2,
               "vol_filtered": 0, "view": {"direction": "neutral", "conviction": 0.2,
                                           "vol_regime": "mid"}}
    bus_client.bus().cache_set("cache:options:swing", payload)
    with ui.card():
        swing.render()


# --------------------------------------------------------------- page wiring
# Driven through the real widgets in a slot context: each control's value is set
# the way a browser change sets it, which runs the page's change handlers.

def _render_page(payload=None):
    from nicegui import ui

    bus_client.reset()
    if payload is not None:
        bus_client.bus().cache_set("cache:options:swing", payload)
    with ui.card() as card:
        swing.render()
    return card


def _widgets(card, kind):
    return [e for e in card.descendants() if isinstance(e, kind)]


def _number(card, label):
    from nicegui import ui
    (n,) = [e for e in _widgets(card, ui.number)
            if label in (e.props.get("label"), e.props.get("aria-label"))]
    return n


def _buttons(card):
    from nicegui import ui
    return {getattr(b, "text", ""): b for b in _widgets(card, ui.button)}


def _click(element, card):
    from nicegui.events import GenericEventArguments
    (listener,) = [l for l in element._event_listeners.values()
                   if l.type.split(".")[0] == "click"]
    # NiceGUI runs a handler inside the SENDER's parent slot (events.handle_event),
    # which is what decides where anything the handler builds is mounted.
    with element.parent_slot:
        listener.handler(GenericEventArguments(sender=element, client=element.client,
                                               args=None))


_PAYLOAD = {"symbol": "SPY", "signals": [_FLY, _NAKED], "filtered_out": 0,
            "vol_filtered": 0, "view": {"direction": "neutral"}}


def _segment(card, options):
    """The pill buttons of one segmented group, by label, and the active label."""
    buttons = {k: v for k, v in _buttons(card).items() if k in options}
    assert set(buttons) == set(options)
    on = swing.SEG_ON.split()[0]
    active = [k for k, b in buttons.items() if on in b._classes]
    assert len(active) <= 1
    return buttons, (active[0] if active else None)


_EXPIRY = [label for label, _lo, _hi in swing.fv.EXPIRY_PRESETS]
_RISK = list(swing.fv.RISK_STYLES)


def test_the_scan_bar_uses_pill_groups_not_quasar_toggles():
    from nicegui import ui
    card = _render_page()
    assert _widgets(card, ui.toggle) == []
    for options in (_EXPIRY, _RISK):
        buttons, _active = _segment(card, options)
        for b in buttons.values():
            assert "no-caps" in b.props and "font-normal" in b._classes


def test_scan_bar_groups_share_one_label_style():
    """Symbol, Expiry and Risk style each carry the same EYEBROW label above the
    control, rather than a q-field floating label beside a bare toggle."""
    from nicegui import ui
    card = _render_page()
    eyebrows = [e.text for e in _widgets(card, ui.label)
                if swing.EYEBROW.split()[0] in e._classes]
    assert eyebrows[:3] == ["Symbol", "Expiry", "Risk style"]
    symbol = _widgets(card, ui.input)[0]
    assert "label" not in symbol.props


def test_an_expiry_preset_writes_both_boxes_and_a_hand_edit_clears_it():
    card = _render_page()
    buttons, active = _segment(card, _EXPIRY)
    assert active == "Any"
    _click(buttons["2–6 wk"], card)
    assert (_number(card, "DTE min").value, _number(card, "DTE max").value) == (14, 42)
    assert _segment(card, _EXPIRY)[1] == "2–6 wk"
    _number(card, "DTE max").value = 50
    assert _segment(card, _EXPIRY)[1] is None
    _number(card, "DTE max").value = 42
    assert _segment(card, _EXPIRY)[1] == "2–6 wk"


def test_a_risk_style_writes_the_delta_fields_and_a_hand_edit_shows_custom():
    from nicegui import ui
    card = _render_page()
    buttons, active = _segment(card, _RISK)
    (custom,) = [e for e in _widgets(card, ui.label) if e.text == "Custom"]
    assert active == "Balanced" and not custom.visible
    _click(buttons["Aggressive"], card)
    assert _number(card, "Put Δ min").value == -0.30
    assert _number(card, "Call Δ max").value == 0.30
    assert _segment(card, _RISK)[1] == "Aggressive" and not custom.visible
    _number(card, "Call Δ max").value = 0.27
    assert _segment(card, _RISK)[1] is None and custom.visible
    assert "Custom" not in _buttons(card)           # never a choosable value


def test_the_split_bar_marks_its_zero_point():
    card = _render_page(_PAYLOAD)
    (grid,) = [e for e in card.descendants() if "grid" in e._classes]
    ticks = [e for e in grid.descendants() if swing.SPLIT_TICK.split()[0] in e._classes]
    assert ticks and all(set(swing.SPLIT_TICK.split()) <= set(t._classes) for t in ticks)


def test_the_card_payoff_shape_is_centred():
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    (grid,) = [e for e in card.descendants() if "grid" in e._classes]
    shapes = [e for e in grid.descendants() if isinstance(e, ui.html)]
    assert shapes and all("self-center" in e._classes for e in shapes)
    assert all('width="280"' in e.content for e in shapes)


def test_the_advanced_expansion_is_light():
    from nicegui import ui
    card = _render_page()
    (adv,) = _widgets(card, ui.expansion)
    assert "finder-advanced" in adv._classes and "dense" in adv.props
    assert ".finder-advanced .q-focus-helper" in swing.FINDER_CSS


def test_chips_filter_the_list_and_cards_without_a_scan(monkeypatch):
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    sent = []
    monkeypatch.setattr(bus_client, "request", lambda *a, **k: sent.append(a))
    (table,) = _widgets(card, ui.table)
    assert {r["id"] for r in table.rows} == {"fly", "sp"}

    _click(_buttons(card)["Butterflies & condors 1"], card)
    assert [r["id"] for r in table.rows] == ["fly"]
    _click(_buttons(card)["All 2"], card)
    assert {r["id"] for r in table.rows} == {"fly", "sp"}
    assert sent == []                            # filtering never rescans


def test_scan_clears_the_old_result_and_shows_placeholders(monkeypatch):
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    sent = []
    monkeypatch.setattr(bus_client, "request", lambda *a, **k: sent.append(a))
    (table,) = _widgets(card, ui.table)
    assert table.rows
    symbol = _widgets(card, ui.input)[0]
    symbol.value = "qqq"
    _click(_buttons(card)["Scan"], card)
    assert table.rows == []
    assert sent and sent[0][1]["args"]["symbol"] == "QQQ"
    (grid,) = [e for e in card.descendants() if "grid" in e._classes]
    texts = [e.text for e in grid.descendants() if isinstance(e, ui.label)]
    assert texts == ["Scanning QQQ…"] * 4


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service cold) — the Tier-3 graceful-empty path. Mirrors the header
    page test: rendering inside a slot context is enough to exercise the widget
    wiring + the initial fetch-free paint.
    """
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:swing") is None  # confirm empty
    with ui.card():
        swing.render()  # must not raise


def test_delta_band_expander_does_not_claim_credit_spreads_only():
    """The delta bands now bind every short leg the Finder sells - the naked short
    put/call, the short strangle and the covered call or collar call as well as
    PCS/CCS - so the expander must not be labelled as a credit-spread control."""
    src = inspect.getsource(swing)
    assert "Advanced — credit spreads" not in src
    assert "Advanced — delta bands and credit floor" in src


def _fire_scan_timeout(card):
    from nicegui import ui
    from pages import busy
    (timer,) = [t for t in card.descendants()
                if isinstance(t, ui.timer) and t.interval == busy.BUSY_TIMEOUT_SEC]
    with card:
        timer.callback()


def test_a_scan_that_never_comes_back_on_a_cold_feed_says_so(monkeypatch):
    from nicegui import ui
    from pages import copy
    card = _render_page()
    monkeypatch.setattr(bus_client, "request", lambda *a, **k: None)
    (empty,) = [e for e in _widgets(card, ui.label) if e.text == swing.EMPTY_PROMPT]
    assert empty.visible
    _click(_buttons(card)["Scan"], card)
    assert not empty.visible
    _fire_scan_timeout(card)
    assert empty.visible and empty.text == copy.WAITING_OPTIONS
    (grid,) = [e for e in card.descendants() if "grid" in e._classes]
    assert not list(grid.descendants())          # the placeholders are gone


def _fire_poll(card):
    from nicegui import ui
    (timer,) = [t for t in card.descendants()
                if isinstance(t, ui.timer) and t.interval == 2.0]
    with card:
        timer.callback()


def _publish(payload):
    bus_client.bus().cache_set("cache:options:swing", payload)


def _placeholder_texts(card):
    from nicegui import ui
    (grid,) = [e for e in card.descendants() if "grid" in e._classes]
    return [e.text for e in grid.descendants() if isinstance(e, ui.label)]


def _scan(card, monkeypatch, symbol):
    from nicegui import ui
    monkeypatch.setattr(bus_client, "request", lambda *a, **k: None)
    _widgets(card, ui.input)[0].value = symbol
    _click(_buttons(card)["Scan"], card)


def test_a_slow_scan_says_it_is_still_coming_and_keeps_waiting(monkeypatch):
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    (table,) = _widgets(card, ui.table)
    _scan(card, monkeypatch, "msft")
    _fire_scan_timeout(card)
    (status,) = [e for e in _widgets(card, ui.label) if e.text == swing.SCAN_SLOW]
    assert swing.SCAN_SLOW == ("The scan is taking longer than expected — "
                               "results will appear when it finishes.")
    assert _placeholder_texts(card) == ["Scanning MSFT…"] * 4
    assert table.rows == []
    # ... and the late result still lands.
    _publish({**_PAYLOAD, "symbol": "MSFT"})
    _fire_poll(card)
    assert {r["id"] for r in table.rows} == {"fly", "sp"} and status.text == ""


def test_a_result_for_another_symbol_never_lands_under_the_scan(monkeypatch):
    """Scan AAPL, then quickly MSFT: AAPL's result (or another tab's scan) must
    not paint under the MSFT request."""
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    (table,) = _widgets(card, ui.table)
    _scan(card, monkeypatch, "MSFT")
    _publish({**_PAYLOAD, "symbol": "AAPL"})
    _fire_poll(card)
    assert table.rows == []
    assert _placeholder_texts(card) == ["Scanning MSFT…"] * 4
    _publish({**_PAYLOAD, "symbol": "MSFT", "signals": [_FLY]})
    _fire_poll(card)
    assert [r["id"] for r in table.rows] == ["fly"]
    # No scan waiting any more: any new result paints, as before.
    _publish({**_PAYLOAD, "symbol": "QQQ"})
    _fire_poll(card)
    assert {r["id"] for r in table.rows} == {"fly", "sp"}


def _panel(card):
    (col,) = [e for e in card.descendants()
              if "w-11" in e._classes or "w-[360px]" in e._classes]
    return col


def _row_click(card, sig_id):
    from nicegui import ui
    from nicegui.events import GenericEventArguments
    (table,) = _widgets(card, ui.table)
    (listener,) = [l for l in table._event_listeners.values() if l.type == "rowClick"]
    with card:
        listener.handler(GenericEventArguments(sender=table, client=table.client,
                                               args=[{}, {"id": sig_id}, 0]))


def test_the_detail_panel_starts_collapsed_and_opens_on_the_first_selection():
    card = _render_page(_PAYLOAD)
    assert "w-11" in _panel(card)._classes          # collapsed at build
    _row_click(card, "fly")
    assert "w-11" not in _panel(card)._classes and "w-[360px]" in _panel(card)._classes


def test_every_selection_reopens_a_collapsed_panel():
    """A click that updates a collapsed panel invisibly reads as broken."""
    card = _render_page(_PAYLOAD)
    _row_click(card, "fly")
    (toggle,) = [b for b in _widgets(card, __import__("nicegui").ui.button)
                 if b.props.get("icon") == "last_page"]
    _click(toggle, card)                             # the user collapses it
    assert "w-11" in _panel(card)._classes
    (grid,) = [e for e in card.descendants() if "grid" in e._classes]
    first_card = next(iter(grid.default_slot.children))
    _click(first_card, card)                         # selecting a card reopens
    assert "w-[360px]" in _panel(card)._classes


def test_a_new_scan_clears_the_detail_panel(monkeypatch):
    from nicegui import ui
    from pages.options import detail
    card = _render_page(_PAYLOAD)
    _row_click(card, "fly")
    placeholder = [e for e in _widgets(card, ui.label) if e.text == detail._PLACEHOLDER]
    assert not placeholder
    _scan(card, monkeypatch, "MSFT")
    assert [e for e in _widgets(card, ui.label) if e.text == detail._PLACEHOLDER]


def test_the_card_paper_dialog_survives_a_repaint():
    """ui.dialog mounts on the client layout but leaves a CANARY in the slot it
    was built from, and deletes itself when that canary is collected. Built from
    a pick card, the next repaint (picks_grid.clear()) would close an open dialog
    under the user's hands."""
    import gc
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    client = card.client
    before = {id(e) for e in client.elements.values() if isinstance(e, ui.dialog)}
    _click(_buttons(card)["Paper"], card)
    (dialog,) = [e for e in client.elements.values()
                 if isinstance(e, ui.dialog) and id(e) not in before]
    _click(_buttons(card)["Butterflies & condors 1"], card)   # repaints the cards
    _click(_buttons(card)["All 2"], card)
    gc.collect()
    assert not dialog.is_deleted


def test_the_list_says_why_it_is_empty(monkeypatch):
    from nicegui import ui
    empty = {"symbol": "SPY", "signals": [], "filtered_out": 4, "vol_filtered": 0}
    card = _render_page(empty)
    (table,) = _widgets(card, ui.table)
    assert table.props["no-data-label"] == swing.fv.no_data_label(empty)
    _scan(card, monkeypatch, "qqq")
    assert table.props["no-data-label"] == "Scanning QQQ…"
