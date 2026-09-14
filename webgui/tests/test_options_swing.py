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


def test_family_options_alias_is_gone():
    """The chips read finder_view.GROUPS directly; nothing else needed the alias."""
    assert not hasattr(swing, "_FAMILY_OPTIONS")


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


def test_list_rows_name_the_strikes_the_card_does():
    """The list's Strikes cell is the same legs line the top-pick card prints."""
    from pages.options import strategy_table
    rows = {r["id"]: r for r in swing.finder_rows([_FLY, _NAKED])}
    strikes = rows["fly"]["strikes"].replace(" ", " ")
    assert strikes == strategy_table.legs_summary(_FLY["legs"])
    assert strikes == swing.card_view(_FLY)["legs"] == "L 95C / S 2×100C / L 105C"


def test_a_calendar_row_names_its_back_month():
    from pages.options import strategy_table
    cal = {**_FLY, "id": "cal", "type": "CALENDAR_PUT",
           "legs": [{"side": "short", "kind": "put", "strike": 220.0,
                     "expiration": "2026-09-21", "qty": 1},
                    {"side": "long", "kind": "put", "strike": 220.0,
                     "expiration": "2026-10-16", "qty": 1}]}
    (row,) = swing.finder_rows([cal])
    strikes = row["strikes"].replace(" ", " ")
    assert strikes == strategy_table.legs_summary(cal["legs"]) == "S 220P / L 220P 10/16"


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
    assert active == "All"
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
    (timer,) = [t for t in card.descendants()
                if isinstance(t, ui.timer) and t.interval == swing.SCAN_TIMEOUT_SEC]
    with card:
        timer.callback()


def test_a_scan_that_never_comes_back_on_a_cold_feed_says_so(monkeypatch):
    """Nothing has published this session: one still card names the cold feed,
    and the status line says what to do - never four cards pulsing forever."""
    from nicegui import ui
    from pages import copy
    card = _render_page()
    monkeypatch.setattr(bus_client, "request", lambda *a, **k: None)
    (empty,) = [e for e in _widgets(card, ui.label) if e.text == swing.EMPTY_PROMPT]
    assert empty.visible
    _click(_buttons(card)["Scan"], card)
    assert not empty.visible
    _fire_scan_timeout(card)
    assert _placeholder_texts(card) == [copy.WAITING_OPTIONS]
    assert not [e for e in _still_cards(card) if "animate-pulse" in e._classes]
    assert [e for e in _widgets(card, ui.label) if e.text == swing.SCAN_SLOW]


def _fire_poll(card):
    from nicegui import ui
    (timer,) = [t for t in card.descendants()
                if isinstance(t, ui.timer) and t.interval == 2.0]
    with card:
        timer.callback()


def _publish(payload):
    bus_client.bus().cache_set("cache:options:swing", payload)


def _still_cards(card):
    (grid,) = [e for e in card.descendants() if "grid" in e._classes]
    return list(grid.default_slot.children)


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
    assert swing.SCAN_SLOW == (
        "The scan is taking longer than expected. It will appear here if it "
        "finishes; if nothing arrives, check System Status and scan again.")
    # ONE still card, not four pulsing placeholders that would pulse forever
    # when the scan never publishes (service down, handler raised).
    assert _placeholder_texts(card) == ["No result for MSFT yet."]
    assert len(_still_cards(card)) == 1
    assert "animate-pulse" not in _still_cards(card)[0]._classes
    assert table.rows == []
    assert table.props["no-data-label"] == "No result for MSFT yet."
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


def test_a_quote_in_the_symbol_cannot_break_the_empty_label(monkeypatch):
    from nicegui import ui
    card = _render_page()
    (table,) = _widgets(card, ui.table)
    _scan(card, monkeypatch, 'a"b c')
    assert table.props["no-data-label"] == 'Scanning A"B C…'


def test_the_same_symbol_with_another_range_waits_for_its_own_result(monkeypatch):
    """SPY 1-2 wk then SPY 1-3 mo: the 1-2 wk result must not paint as the 1-3 mo
    scan. The handler echoes the request as ``params``."""
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    (table,) = _widgets(card, ui.table)
    sent = []
    monkeypatch.setattr(bus_client, "request", lambda d, cmd: sent.append(cmd["args"]))
    _click(_buttons(card)["1–2 wk"], card)
    _click(_buttons(card)["Scan"], card)
    wk = sent[-1]
    _click(_buttons(card)["1–3 mo"], card)
    _click(_buttons(card)["Scan"], card)
    mo = sent[-1]
    assert (wk["dte_min"], mo["dte_min"]) == (7, 30)
    _publish({**_PAYLOAD, "signals": [_NAKED], "params": wk})
    _fire_poll(card)
    assert table.rows == []                         # not the scan being waited on
    _publish({**_PAYLOAD, "signals": [_FLY], "params": mo})
    _fire_poll(card)
    assert [r["id"] for r in table.rows] == ["fly"]


def test_the_list_badge_shows_the_rounded_score():
    from nicegui import ui
    card = _render_page({**_PAYLOAD, "signals": [{**_FLY, "composite_score": 72.5}]})
    (table,) = _widgets(card, ui.table)
    assert table.rows[0]["score_text"] == "73"
    assert "props.row.score_text" in swing._SCORE_SLOT


# ------------------------------------------------------------ the symbol box
# The box used to start at SPY whatever the painted result was, so returning to
# the page after an NVDA scan showed "SPY" above NVDA's ideas.

def test_initial_symbol_is_the_painted_scan_s_symbol():
    assert swing.initial_symbol({"symbol": "NVDA"}) == "NVDA"
    assert swing.initial_symbol({"symbol": " qqq "}) == "QQQ"


def test_initial_symbol_falls_back_to_spy_with_nothing_painted():
    assert swing.initial_symbol(None) == "SPY"
    assert swing.initial_symbol({}) == "SPY"
    assert swing.initial_symbol({"symbol": ""}) == "SPY"
    assert swing.initial_symbol({"symbol": None}) == "SPY"
    assert swing.initial_symbol({"symbol": 5}) == "SPY"


def test_the_symbol_box_starts_on_the_cached_scan():
    from nicegui import ui
    card = _render_page({**_PAYLOAD, "symbol": "NVDA"})
    assert _widgets(card, ui.input)[0].value == "NVDA"


def test_the_symbol_box_starts_on_spy_with_an_empty_cache():
    from nicegui import ui
    card = _render_page()
    assert _widgets(card, ui.input)[0].value == "SPY"


def test_tabbing_out_of_the_seeded_box_does_not_rescan(monkeypatch):
    """The dedup is seeded from the box's value at bind time, so the box must
    already hold the cached symbol by then - otherwise the first tab-out reads
    NVDA != SPY and fires a scan nobody asked for."""
    from nicegui import ui
    from nicegui.events import GenericEventArguments
    card = _render_page({**_PAYLOAD, "symbol": "NVDA"})
    sent = []
    monkeypatch.setattr(bus_client, "request", lambda *a, **k: sent.append(a))
    symbol = _widgets(card, ui.input)[0]
    (listener,) = [l for l in symbol._event_listeners.values()
                   if l.type == "focusout"]
    with symbol.parent_slot:
        listener.handler(GenericEventArguments(sender=symbol, client=symbol.client,
                                               args=None))
    assert sent == []


def _cached_scan(**params):
    base = {"symbol": "NVDA", "dte_min": 7, "dte_max": 14,
            "put_d_min": -0.30, "put_d_max": -0.20,
            "call_d_min": 0.20, "call_d_max": 0.30, "min_cr_fraction": 0.15}
    return {**_PAYLOAD, "symbol": "NVDA", "params": {**base, **params}}


def test_the_scan_bar_starts_on_the_cached_scan_s_params():
    card = _render_page(_cached_scan())
    assert (_number(card, "DTE min").value, _number(card, "DTE max").value) == (7, 14)
    assert _segment(card, _EXPIRY)[1] == "1–2 wk"
    assert _segment(card, _RISK)[1] == "Aggressive"
    assert _number(card, "Put Δ min").value == -0.30
    assert _number(card, "Call Δ max").value == 0.30
    assert _number(card, "Min credit %").value == 15.0


def test_a_cached_hand_edited_scan_shows_custom_and_no_preset():
    from nicegui import ui
    card = _render_page(_cached_scan(dte_min=3, dte_max=21, put_d_min=-0.27))
    assert _segment(card, _EXPIRY)[1] is None
    assert _segment(card, _RISK)[1] is None
    (custom,) = [e for e in _widgets(card, ui.label) if e.text == swing.fv.RISK_CUSTOM]
    assert custom.visible


def test_the_scan_bar_starts_on_defaults_with_an_empty_cache():
    from nicegui import ui
    card = _render_page()
    assert (_number(card, "DTE min").value, _number(card, "DTE max").value) == (0, None)
    assert _segment(card, _EXPIRY)[1] == "All"
    assert _segment(card, _RISK)[1] == swing.fv.RISK_DEFAULT
    (custom,) = [e for e in _widgets(card, ui.label) if e.text == swing.fv.RISK_CUSTOM]
    assert not custom.visible


def test_scanning_again_from_a_seeded_bar_resends_the_cached_params(monkeypatch):
    card = _render_page(_cached_scan())
    sent = []
    monkeypatch.setattr(bus_client, "request", lambda *a, **k: sent.append(a))
    _click(_buttons(card)["Scan"], card)
    args = sent[0][1]["args"]
    assert args == {"symbol": "NVDA", "dte_min": 7, "dte_max": 14,
                    "put_d_min": -0.30, "put_d_max": -0.20,
                    "call_d_min": 0.20, "call_d_max": 0.30, "min_cr_fraction": 0.15}


# ------------------------------------------------- whole chain: no DTE limit
# (docs/plans/2026-09-14-strategy-finder-whole-chain-design.md)

_BANDS = {"put_d_min": -0.2, "put_d_max": -0.1, "call_d_min": 0.1, "call_d_max": 0.2}


def test_scan_params_blank_dte_max_is_no_limit():
    assert swing.scan_params("spy", 0, None, _BANDS, 10)["dte_max"] is None
    assert swing.scan_params("spy", 0, "", _BANDS, 10)["dte_max"] is None
    assert swing.scan_params("spy", 0, 60.0, _BANDS, 10)["dte_max"] == 60
    assert isinstance(swing.scan_params("spy", 0, 60.0, _BANDS, 10)["dte_max"], int)


def test_the_expiry_group_has_six_pills_and_all_is_the_default():
    card = _render_page()
    buttons, active = _segment(card, _EXPIRY)
    assert list(buttons) == ["1–2 wk", "2–6 wk", "1–3 mo", "3–12 mo", "1 yr+", "All"]
    assert active == "All"


def test_the_dte_max_box_says_no_limit_when_blank():
    card = _render_page()
    box = _number(card, "DTE max")
    assert box.value is None
    assert box.props.get("placeholder") == "no limit"


def test_all_then_scan_sends_no_upper_limit_and_a_typed_max_is_sent(monkeypatch):
    card = _render_page(_cached_scan())
    sent = []
    monkeypatch.setattr(bus_client, "request", lambda d, cmd: sent.append(cmd["args"]))
    _click(_buttons(card)["All"], card)
    assert (_number(card, "DTE min").value, _number(card, "DTE max").value) == (0, None)
    _click(_buttons(card)["Scan"], card)
    assert sent[-1]["dte_min"] == 0 and sent[-1]["dte_max"] is None
    _number(card, "DTE max").value = 60
    assert _segment(card, _EXPIRY)[1] is None
    _click(_buttons(card)["Scan"], card)
    assert sent[-1]["dte_max"] == 60


# --------------------------------------------------------- the earnings tag

_EARN = {**_FLY, "id": "earn", "spans_earnings": True, "earnings_date": "2026-11-19"}


def test_a_stamped_card_carries_the_earnings_badge_and_an_unstamped_one_does_not():
    from nicegui import ui
    card = _render_page({**_PAYLOAD, "signals": [_EARN, _NAKED]})
    (grid,) = [e for e in card.descendants() if "grid" in e._classes]
    tags = [e for e in grid.descendants()
            if isinstance(e, ui.label) and e.text.startswith("Earnings")]
    assert [t.text for t in tags] == ["Earnings Nov 19"]
    assert set(swing.BADGE_WARN.split()) <= set(tags[0]._classes)


def test_a_stamped_row_carries_the_earnings_text_and_an_unstamped_one_does_not():
    rows = {r["id"]: r for r in swing.finder_rows([_EARN, _NAKED])}
    assert rows["earn"]["earnings"] == "Earnings Nov 19"
    assert rows["sp"]["earnings"] is None


def test_the_strategy_slot_shows_the_earnings_tag_as_text_never_html():
    slot = swing._STRATEGY_SLOT
    assert 'v-if="props.row.earnings"' in slot
    assert "{{ props.row.earnings }}" in slot
    assert 'v-html="props.row.earnings"' not in slot
    # The warn colour is the shared badge token, as classes.
    assert swing.BADGE_WARN in slot


# ------------------------------------------- the spinner holds until the answer

def test_scan_timeout_is_three_minutes():
    assert swing.SCAN_TIMEOUT_SEC == 180


def test_scanning_text_is_the_counter_s_own_head():
    for sym in ("spy", "", 'a"b c', None):
        assert swing.scanning_text(sym) == swing.fv.scan_timeout_text(sym, None)


def _scan_busy(card):
    """The list's spinner scrim and its 1 s watchdog timer."""
    from nicegui import ui
    (spinner,) = [e for e in card.descendants() if isinstance(e, ui.spinner)]
    scrim = spinner.parent_slot.parent
    from pages import busy
    # build_busy mounts its watchdog outside the target, beside the page's own
    # timers; it carries busy's marker.
    (watchdog,) = [t for t in card.descendants()
                   if isinstance(t, ui.timer) and busy.WATCHDOG_MARK in t._markers]
    return scrim, watchdog


def _tick_after(card, monkeypatch, seconds):
    from pages import busy
    scrim, watchdog = _scan_busy(card)
    start = busy._time.monotonic()
    monkeypatch.setattr(busy._time, "monotonic", lambda: start + seconds)
    with card:
        watchdog.callback()
    return scrim


def test_the_spinner_outlasts_the_old_thirty_second_backstop(monkeypatch):
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    _scan(card, monkeypatch, "msft")
    scrim = _tick_after(card, monkeypatch, 31)
    assert scrim.visible
    assert _placeholder_texts(card) == ["Scanning MSFT…"] * 4
    (count,) = [e for e in scrim.descendants() if isinstance(e, ui.label)]
    assert count.text == "Scanning MSFT… 31 s"
    one_shots = [t for t in card.descendants()
                 if isinstance(t, ui.timer) and t.interval == swing.SCAN_TIMEOUT_SEC]
    assert len(one_shots) == 1


def test_the_counter_names_the_current_request_s_symbol(monkeypatch):
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    _scan(card, monkeypatch, "aapl")
    _scan(card, monkeypatch, "msft")
    scrim = _tick_after(card, monkeypatch, 5)
    (count,) = [e for e in scrim.descendants() if isinstance(e, ui.label)]
    assert count.text == "Scanning MSFT… 5 s"


def test_an_answering_payload_hides_the_spinner(monkeypatch):
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    (table,) = _widgets(card, ui.table)
    _scan(card, monkeypatch, "msft")
    scrim = _tick_after(card, monkeypatch, 31)
    _publish({**_PAYLOAD, "symbol": "AAPL"})          # not ours
    _fire_poll(card)
    assert scrim.visible and table.rows == []
    _publish({**_PAYLOAD, "symbol": "MSFT"})
    _fire_poll(card)
    assert not scrim.visible
    assert {r["id"] for r in table.rows} == {"fly", "sp"}


def test_a_failed_scan_answer_ends_the_wait_and_says_so(monkeypatch):
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    (table,) = _widgets(card, ui.table)
    _scan(card, monkeypatch, "msft")
    scrim, _ = _scan_busy(card)
    assert scrim.visible
    _publish({"symbol": "MSFT", "signals": [], "error": "TypeError", "spot": 410.5})
    _fire_poll(card)
    assert not scrim.visible
    assert table.rows == []
    assert table.props["no-data-label"] == (
        "The scan for MSFT failed. Check System Status and scan again.")
    assert _placeholder_texts(card) == []
    (failed,) = [e for e in _widgets(card, ui.label) if e.text == "Scan failed"]
    assert failed.parent_slot.parent.visible


def test_a_zero_idea_answer_shows_the_price_in_the_summary(monkeypatch):
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    _scan(card, monkeypatch, "spy")
    _publish({"symbol": "SPY", "signals": [], "spot": 764.48, "filtered_out": 18,
              "vol_filtered": 0})
    _fire_poll(card)
    (price,) = [e for e in _widgets(card, ui.label) if e.text == "$764.48"]
    summary = price.parent_slot.parent
    assert summary.visible
    texts = [e.text for e in summary.descendants() if isinstance(e, ui.label)]
    assert "0 ideas · 18 below the quality bar" in texts
    (table,) = _widgets(card, ui.table)
    assert table.props["no-data-label"] == (
        "No strategies cleared the quality bar for SPY at $764.48.")


def test_a_payload_with_no_price_says_price_unavailable():
    from nicegui import ui
    card = _render_page({"symbol": "SPY", "signals": [], "filtered_out": 1})
    assert [e for e in _widgets(card, ui.label) if e.text == "Price unavailable"]


# ---------------------------------------------------------------- paged list

def _many(n):
    return [{**_FLY, "id": f"s{i:03d}", "composite_score": 50.0 + (i % 40),
             "max_loss": float(i), "group": "BUTTERFLY" if i % 2 else "VERTICAL"}
            for i in range(n)]


_COLS = swing.fv.finder_columns()


def test_page_of_slices_fifty_and_reports_the_total():
    rows = [{"id": i, "_max_loss_n": i} for i in range(120)]
    page, pag = swing.page_of(rows, _COLS, None)
    assert [r["id"] for r in page] == list(range(50))
    assert pag == {"sortBy": None, "descending": False, "page": 1,
                   "rowsPerPage": swing.PAGE_SIZE, "rowsNumber": 120}
    page, pag = swing.page_of(rows, _COLS, {"page": 3, "rowsPerPage": 50})
    assert [r["id"] for r in page] == list(range(100, 120)) and pag["page"] == 3


def test_page_of_sorts_the_whole_list_before_slicing():
    rows = [{"id": i, "_max_loss_n": (i * 37) % 120} for i in range(120)]
    req = {"sortBy": "max_loss", "descending": True, "page": 1, "rowsPerPage": 50}
    page, pag = swing.page_of(rows, _COLS, req)
    assert [r["_max_loss_n"] for r in page] == list(range(119, 69, -1))
    assert (pag["sortBy"], pag["descending"]) == ("max_loss", True)
    page, _ = swing.page_of(rows, _COLS, {**req, "descending": False, "page": 2})
    assert [r["_max_loss_n"] for r in page] == list(range(50, 100))


def test_page_of_sorts_like_quasar_missing_first_ascending():
    rows = [{"id": "a", "_pop_n": 40.0}, {"id": "b", "_pop_n": None},
            {"id": "c", "_pop_n": 10.0}]
    up, _ = swing.page_of(rows, _COLS, {"sortBy": "pop", "descending": False})
    down, _ = swing.page_of(rows, _COLS, {"sortBy": "pop", "descending": True})
    assert [r["id"] for r in up] == ["b", "c", "a"]
    assert [r["id"] for r in down] == ["a", "c", "b"]
    text, _ = swing.page_of([{"id": 1, "strategy": "b"}, {"id": 2, "strategy": "A"}],
                            _COLS, {"sortBy": "strategy", "descending": False})
    assert [r["id"] for r in text] == [2, 1]


def test_page_of_clamps_a_page_past_the_end_and_ignores_an_unknown_column():
    rows = [{"id": i} for i in range(60)]
    page, pag = swing.page_of(rows, _COLS, {"page": 9, "sortBy": "nope",
                                            "rowsPerPage": 0})
    assert pag["page"] == 2 and pag["rowsPerPage"] == swing.PAGE_SIZE
    assert pag["sortBy"] is None
    assert [r["id"] for r in page] == list(range(50, 60))
    empty, pag = swing.page_of([], _COLS, {"page": 4})
    assert empty == [] and pag["page"] == 1 and pag["rowsNumber"] == 0


def _request_page(card, pagination):
    from nicegui import ui
    from nicegui.events import GenericEventArguments
    (table,) = _widgets(card, ui.table)
    (listener,) = [l for l in table._event_listeners.values() if l.type == "request"]
    with card:
        listener.handler(GenericEventArguments(sender=table, client=table.client,
                                               args={"pagination": pagination}))


def test_the_list_sends_one_page_at_a_time():
    from nicegui import ui
    card = _render_page({**_PAYLOAD, "signals": _many(120)})
    (table,) = _widgets(card, ui.table)
    assert len(table.rows) == 50
    assert table.pagination["rowsPerPage"] == 50
    assert table.pagination["rowsNumber"] == 120 and table.pagination["page"] == 1
    assert table.props["rows-per-page-options"] == [50]
    _request_page(card, {"sortBy": "max_loss", "descending": True, "page": 2,
                         "rowsPerPage": 50})
    assert [r["id"] for r in table.rows][:2] == ["s069", "s068"]
    assert table.pagination["page"] == 2 and table.pagination["sortBy"] == "max_loss"


def test_a_chip_click_returns_the_list_to_page_one():
    from nicegui import ui
    card = _render_page({**_PAYLOAD, "signals": _many(120)})
    (table,) = _widgets(card, ui.table)
    _request_page(card, {"sortBy": "max_loss", "descending": True, "page": 2,
                         "rowsPerPage": 50})
    _click(_buttons(card)["Spreads 60"], card)
    assert table.pagination["page"] == 1 and table.pagination["rowsNumber"] == 60
    # The sort the reader chose survives the filter.
    assert table.rows[0]["id"] == "s118"


def test_a_new_scan_returns_the_list_to_page_one(monkeypatch):
    from nicegui import ui
    card = _render_page({**_PAYLOAD, "signals": _many(120)})
    (table,) = _widgets(card, ui.table)
    _request_page(card, {"sortBy": None, "descending": False, "page": 3,
                         "rowsPerPage": 50})
    assert table.pagination["page"] == 3
    _scan(card, monkeypatch, "spy")
    assert table.pagination["page"] == 1 and table.rows == []
    _publish({**_PAYLOAD, "signals": _many(120)})
    _fire_poll(card)
    assert table.pagination["page"] == 1 and len(table.rows) == 50


def test_a_row_click_on_a_later_page_selects_its_signal():
    card = _render_page({**_PAYLOAD, "signals": _many(120)})
    _request_page(card, {"sortBy": None, "descending": False, "page": 3,
                         "rowsPerPage": 50})
    _row_click(card, "s119")
    assert "w-[360px]" in _panel(card)._classes


def test_the_page_request_asks_the_browser_for_the_pagination_only():
    """The wire shape: Quasar's ``request`` also carries a ``getCellValue``
    function, so the listener names the one field it reads. Dropping the args
    list would change what the browser sends."""
    from nicegui import ui
    card = _render_page({**_PAYLOAD, "signals": _many(120)})
    (table,) = _widgets(card, ui.table)
    (listener,) = [l for l in table._event_listeners.values() if l.type == "request"]
    assert listener.args == [["pagination"]]


# ------------------------------------------------------------ review fixes

def test_scan_params_blank_dte_min_is_zero():
    assert swing.scan_params("spy", None, 30, _BANDS, 10)["dte_min"] == 0
    assert swing.scan_params("spy", " ", 30, _BANDS, 10)["dte_min"] == 0
    assert swing.scan_params("spy", 7.0, 30, _BANDS, 10)["dte_min"] == 7


def test_a_blank_dte_min_box_still_scans_from_today(monkeypatch):
    card = _render_page()
    sent = []
    monkeypatch.setattr(bus_client, "request", lambda d, cmd: sent.append(cmd["args"]))
    _number(card, "DTE min").value = None
    _click(_buttons(card)["Scan"], card)
    assert sent and sent[-1]["dte_min"] == 0 and sent[-1]["dte_max"] is None


def _count_updates(monkeypatch, table):
    calls = []
    real = table.update
    monkeypatch.setattr(table, "update", lambda: (calls.append(1), real())[1])
    return calls


def test_a_chip_repaint_pushes_the_table_once(monkeypatch):
    from nicegui import ui
    card = _render_page({**_PAYLOAD, "signals": _many(120)})
    (table,) = _widgets(card, ui.table)
    calls = _count_updates(monkeypatch, table)
    _click(_buttons(card)["Spreads 60"], card)
    assert len(calls) == 1


def test_an_answer_and_a_scan_each_push_the_table_once(monkeypatch):
    from nicegui import ui
    card = _render_page(_PAYLOAD)
    (table,) = _widgets(card, ui.table)
    calls = _count_updates(monkeypatch, table)
    _scan(card, monkeypatch, "msft")
    assert len(calls) == 1
    calls.clear()
    _publish({**_PAYLOAD, "symbol": "MSFT", "signals": _many(120)})
    _fire_poll(card)
    assert len(calls) == 1 and len(table.rows) == 50
    calls.clear()
    _request_page(card, {"sortBy": None, "descending": False, "page": 2,
                         "rowsPerPage": 50})
    assert len(calls) == 1


def test_payoff_shapes_are_built_only_for_the_page_sent(monkeypatch):
    from nicegui import ui
    built = []
    real = swing.fv.payoff_svg

    def spy(curve, spot, width=120, height=32):
        if curve and width == 72:            # a list-row shape with a curve to draw
            built.append(1)
        return real(curve, spot, width=width, height=height)

    monkeypatch.setattr(swing.fv, "payoff_svg", spy)
    card = _render_page({**_PAYLOAD, "signals": _many(120)})
    (table,) = _widgets(card, ui.table)
    assert len(built) == 50
    assert all(r["_payoff_svg"].startswith("<svg") for r in table.rows)
    built.clear()
    _request_page(card, {"sortBy": "max_loss", "descending": True, "page": 3,
                         "rowsPerPage": 50})
    assert len(built) == 20 and len(table.rows) == 20
    assert all(r["_payoff_svg"].startswith("<svg") for r in table.rows)


def test_list_rows_are_shapeless_and_paired_with_their_signals():
    sigs = _many(5)
    rows, paired = swing.list_rows(sigs)
    assert [r["id"] for r in rows] == [s["id"] for s in paired]
    assert all(r["_payoff_svg"] == "" for r in rows)
    shaped = swing.with_shapes(rows[:2], lambda r: {s["id"]: s for s in paired}[r["id"]])
    assert [r["_payoff_svg"] for r in shaped] == [
        swing.finder_rows([s])[0]["_payoff_svg"] for s in paired[:2]]
    assert rows[0]["_payoff_svg"] == ""          # the stored row is not mutated


def test_the_scan_timeout_not_the_spinner_ends_the_wait(monkeypatch):
    """The spinner's own deadline sits past SCAN_TIMEOUT_SEC, so the timed-out
    handler - which also says what happened - is the one thing that ends it."""
    card = _render_page(_PAYLOAD)
    _scan(card, monkeypatch, "msft")
    scrim = _tick_after(card, monkeypatch, swing.SCAN_TIMEOUT_SEC + 1)
    assert scrim.visible
    _fire_scan_timeout(card)
    assert not scrim.visible
    assert _placeholder_texts(card) == ["No result for MSFT yet."]
