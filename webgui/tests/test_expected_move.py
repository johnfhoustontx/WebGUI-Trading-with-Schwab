from pages.options import handoff


def test_signal_to_em_payload_pcs():
    sig = {"type": "PCS", "symbol": "SPY", "expiration": "2026-07-18",
           "short_strike": 540, "long_strike": 535}
    out = handoff.signal_to_em_payload(sig)
    assert out["symbol"] == "SPY" and out["expiry"] == "2026-07-18"
    assert out["legs"] == [
        {"strike": 540.0, "option_type": "put", "side": "short"},
        {"strike": 535.0, "option_type": "put", "side": "long"},
    ]


def test_signal_to_em_payload_iron_condor():
    sig = {"type": "IC", "symbol": "QQQ", "expiration": "2026-07-18",
           "short_strike": 470, "long_strike": 465,
           "call_short": 490, "call_long": 495}
    legs = handoff.signal_to_em_payload(sig)["legs"]
    assert {"strike": 470.0, "option_type": "put", "side": "short"} in legs
    assert {"strike": 495.0, "option_type": "call", "side": "long"} in legs
    assert len(legs) == 4


def test_signal_to_em_payload_strips_dollar_symbol():
    sig = {"type": "LONG_CALL", "symbol": "$SPX", "expiration": "2026-07-18",
           "long_strike": 5400}
    out = handoff.signal_to_em_payload(sig)
    assert out["symbol"] == "SPX"
    assert out["legs"] == [{"strike": 5400.0, "option_type": "call", "side": "long"}]


from pages.options import expected_move as em


def _payload():
    return {
        "symbol": "SPY", "expiry": "2026-07-18", "spot": 100.0, "atm_iv": 0.2, "dte": 5,
        "candles": [[1, 100, 101, 99, 100], [2, 100, 102, 99, 101]],
        "em_upper": [[2, 100.0], [3, 101.0]],
        "em_lower": [[2, 100.0], [3, 99.0]],
        "legs": [{"strike": 540.0, "option_type": "put", "side": "short"},
                 {"strike": 535.0, "option_type": "put", "side": "long"}],
        "error": None,
    }


def test_leg_lines_short_solid_long_dashed():
    lines = em.leg_lines(_payload()["legs"])
    assert lines[0]["value"] == 540.0 and "dashStyle" not in lines[0]
    assert lines[1]["value"] == 535.0 and lines[1]["dashStyle"] == "Dash"


def test_expected_move_figure_series_and_crosshair():
    fig = em.expected_move_figure(_payload())
    types = {s["type"] for s in fig["series"]}
    assert "candlestick" in types
    assert fig["series"][0]["data"][0] == [1, 100, 101, 99, 100]
    assert fig["xAxis"]["type"] == "datetime"
    # Ordinal axis collapses non-trading-day gaps (no blank weekend/holiday candles).
    assert fig["xAxis"]["ordinal"] is True
    # X crosshair keeps the vertical LINE but drops its (raw-ms) label box; the
    # DATE is shown via the tooltip header instead (Highcharts won't date-format
    # the datetime X crosshair label in this build).
    assert fig["xAxis"]["crosshair"]["label"]["enabled"] is False
    assert "%" in fig["tooltip"]["xDateFormat"]      # date in the tooltip header
    assert fig["tooltip"]["valueDecimals"] == 2       # EM/price values to 2dp
    # Y crosshair shows a PRICE label box.
    assert fig["yAxis"]["crosshair"]["label"]["enabled"] is True
    assert "value" in fig["yAxis"]["crosshair"]["label"]["format"]
    assert any(s.get("dashStyle") == "Dash" for s in fig["series"] if s["type"] == "spline")
    assert len(fig["yAxis"]["plotLines"]) == 2


def test_expected_move_figure_handles_empty_payload():
    fig = em.expected_move_figure({})
    assert fig["series"] == [] or all(not s.get("data") for s in fig["series"])


def test_em_lookback_options_has_auto_and_overrides():
    opts = em.em_lookback_options()
    assert opts["auto"].startswith("Auto")
    for key in ("1mo", "3mo", "6mo", "1y"):
        assert key in opts


def test_render_callable():
    assert callable(em.render)


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service cold) — the Tier-3 graceful-empty path. Rendering inside a
    slot context exercises the widget wiring + the initial fetch-free paint.
    """
    import bus_client
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:expected_move") is None
    with ui.card():
        em.render()  # must not raise


def test_pending_expected_move_round_trip():
    handoff.set_pending_expected_move({"symbol": "SPY"})
    assert handoff.take_pending_expected_move() == {"symbol": "SPY"}
    assert handoff.take_pending_expected_move() is None  # one-shot clear


def test_send_to_expected_move_navigates(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        handoff.ui, "navigate",
        type("N", (), {"to": staticmethod(
            lambda *a, **k: calls.setdefault("to", (a, k)))}))
    monkeypatch.setattr(handoff.ui, "notify", lambda *a, **k: None)
    handoff.send_to_expected_move(
        {"symbol": "SPY", "expiry": "2026-07-18", "legs": []})
    assert calls["to"][0][0] == "/options/expected-move"
    assert calls["to"][1].get("new_tab") is True
    assert handoff.take_pending_expected_move()["symbol"] == "SPY"  # stashed


def test_actions_slot_has_expected_move_button():
    assert "to_em" in handoff._ACTIONS_SLOT
    assert "show_chart" in handoff._ACTIONS_SLOT


def test_em_action_slot_is_expected_move_only():
    assert "to_em" in handoff._EM_ACTION_SLOT
    assert "to_calc" not in handoff._EM_ACTION_SLOT
    assert "to_paper" not in handoff._EM_ACTION_SLOT


def test_send_to_expected_move_no_symbol_does_not_navigate(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        handoff.ui, "navigate",
        type("N", (), {"to": staticmethod(
            lambda *a, **k: calls.setdefault("to", True))}))
    monkeypatch.setattr(handoff.ui, "notify", lambda *a, **k: None)
    handoff.send_to_expected_move({"symbol": "", "legs": []})
    assert "to" not in calls  # warned, did not navigate
    handoff.take_pending_expected_move()  # clean up stash


def test_expected_move_figure_legs_override_wins():
    p = _payload()
    p["legs"] = [{"strike": 100.0, "option_type": "put", "side": "short"}]
    fig = em.expected_move_figure(
        p, legs=[{"strike": 105.0, "option_type": "call", "side": "long"}])
    lines = fig["yAxis"]["plotLines"]
    assert [ln["value"] for ln in lines] == [105.0]
    assert lines[0]["color"] == em.CALL_COLOR


def test_expected_move_figure_legs_none_falls_back_to_payload():
    p = _payload()
    p["legs"] = [{"strike": 100.0, "option_type": "put", "side": "short"}]
    fig = em.expected_move_figure(p)
    assert [ln["value"] for ln in fig["yAxis"]["plotLines"]] == [100.0]


def test_expected_move_figure_legs_empty_list_clears_lines():
    p = _payload()
    p["legs"] = [{"strike": 100.0, "option_type": "put", "side": "short"}]
    assert em.expected_move_figure(p, legs=[])["yAxis"]["plotLines"] == []


def test_strike_options_labels_are_trimmed():
    assert em.strike_options([765.0, 770.5]) == {765.0: "765", 770.5: "770.5"}
    assert em.strike_options([]) == {}


def test_expiry_options_labels_carry_dte():
    import datetime as dt
    today = dt.date(2026, 8, 12)
    opts = em.expiry_options(["2026-08-14", "2026-09-18"], today=today)
    assert opts["2026-08-14"] == "2026-08-14  (2d)"
    assert opts["2026-09-18"] == "2026-09-18  (37d)"
    assert em.expiry_options([], today=today) == {}


def test_expiry_options_tolerates_junk():
    import datetime as dt
    opts = em.expiry_options(["nope"], today=dt.date(2026, 8, 12))
    assert opts["nope"] == "nope"


# --- render()-level state-machine tests ---------------------------------
#
# The page's dropdown/poll wiring outgrew "smoke-verify render() doesn't
# crash": these tests drive the ACTUAL closures built inside render() by
# building the page under `ui.card()`, walking the element tree it produced
# to grab the live widgets by label, and setting `.value`/`.options` on them
# exactly as a real user pick or a real poll landing would (NiceGUI does not
# distinguish the two — a programmatic `.value =` assignment fires
# `on_value_change` identically to a client-driven one). `bus_client.request`
# is monkeypatched to a recorder so we can assert on what was actually
# enqueued, instead of needing a live/fake command consumer.

def _descendants(root):
    """Flatten every element nested under ``root`` (NiceGUI slot tree)."""
    out = []
    for child in root.default_slot.children:
        out.append(child)
        out.extend(_descendants(child))
    return out


def _find(card, cls, label):
    for el in _descendants(card):
        if isinstance(el, cls) and el._props.get("label") == label:
            return el
    raise AssertionError(f"no {cls.__name__} labeled {label!r} in the built page")


def _make_capturing_timer(sink):
    """Build a ``ui.timer`` stand-in that appends its callback to ``sink`` —
    the real ``Timer`` defers/never runs its callback under plain pytest (no
    event loop), so this is what makes the page's poll tick callable from a
    test. ``Timer`` is not a NiceGUI ``Element`` (it never joins the slot
    tree), so the callback must be captured this way, not by tree-walking."""

    class _CapturingTimer:
        def __init__(self, interval, callback, *a, **kw):
            self.callback = callback
            sink.append(callback)

        def cancel(self, *a, **kw):
            pass

    return _CapturingTimer


def test_symbol_switch_same_expiry_forces_redraw(monkeypatch):
    """A same-named expiry (e.g. a standard monthly) can survive a symbol
    switch verbatim, which makes NiceGUI's value-unchanged reassignment a
    no-op that never fires on_value_change / _draw(). _apply_chain must force
    a redraw off the CHAIN's own symbol whenever it differs from the symbol
    the chart is currently drawn for — independent of the expiry string."""
    import nicegui.ui as ng_ui
    from nicegui import ui

    import bus_client

    bus_client.reset()
    handoff.take_pending_expected_move()  # clear any stray pending stash

    calls = []
    monkeypatch.setattr(bus_client, "request",
                        lambda domain, command: calls.append((domain, command)))
    captured_callbacks = []
    monkeypatch.setattr(ng_ui, "timer", _make_capturing_timer(captured_callbacks))

    with ui.card() as card:
        em.render()

    assert len(captured_callbacks) == 1, "expected exactly ONE coalesced poll timer"
    poll = captured_callbacks[0]

    symbol_in = _find(card, ui.input, "Symbol")
    expiry_sel = _find(card, ui.select, "Expiry")

    same_expiry = "2026-09-18"

    # 1. SPY's chain lands with the standard monthly; the user picks it.
    bus_client.bus().cache_set("cache:options:em_chain", {
        "symbol": "SPY", "api": "SPY", "spot": 550.0,
        "expirations": [same_expiry],
        "strikes": {same_expiry: [545.0, 550.0]}, "error": None})
    poll()
    expiry_sel.value = same_expiry           # real user pick -> draws SPY
    spy_draws = [c for c in calls if c[1].get("type") == "expected_move"]
    assert len(spy_draws) == 1
    assert spy_draws[0][1]["args"]["symbol"] == "SPY"
    assert spy_draws[0][1]["args"]["expiry"] == same_expiry

    # 2. User switches the Symbol field to QQQ and QQQ's chain lands — SAME
    # expiry string, so expiry_sel.value's reassignment is a no-op and would
    # never fire on_value_change on its own.
    symbol_in.value = "QQQ"
    bus_client.bus().cache_set("cache:options:em_chain", {
        "symbol": "QQQ", "api": "QQQ", "spot": 370.0,
        "expirations": [same_expiry],
        "strikes": {same_expiry: [365.0, 370.0]}, "error": None})
    poll()

    draws = [c for c in calls if c[1].get("type") == "expected_move"]
    assert len(draws) == 2, "the symbol switch must force a SECOND draw"
    assert draws[-1][1]["args"]["symbol"] == "QQQ"
    assert draws[-1][1]["args"]["expiry"] == same_expiry


def test_lookback_change_resends_touched_strike_not_stale_legs(monkeypatch):
    """Once the user has picked a LOCAL strike (a plotLine, no round trip), a
    later Look-back change must resend the CURRENT on-screen strike, not the
    empty/stale legs that were stored when the query was last enqueued."""
    from nicegui import ui

    import bus_client

    bus_client.reset()
    handoff.take_pending_expected_move()

    calls = []
    monkeypatch.setattr(bus_client, "request",
                        lambda domain, command: calls.append((domain, command)))

    with ui.card() as card:
        em.render()

    expiry_sel = _find(card, ui.select, "Expiry")
    strike_sel = _find(card, ui.select, "Strike (optional)")
    lookback_sel = _find(card, ui.select, "Look-back")

    # Draw with no strike picked yet.
    expiry_sel.options = em.expiry_options(["2026-09-18"])
    expiry_sel.value = "2026-09-18"
    first = [c for c in calls if c[1].get("type") == "expected_move"][-1]
    assert first[1]["args"]["legs"] == []

    # Pick a strike locally (no new command — see _strike_changed).
    strike_sel.options = em.strike_options([545.0, 550.0])
    strike_sel.value = 550.0
    legs_only_draws = [c for c in calls if c[1].get("type") == "expected_move"]
    assert len(legs_only_draws) == 1, "a local strike pick must not enqueue a command"

    # Change the look-back — must resend the STRIKE now on screen, not [].
    lookback_sel.value = "3mo"
    redraws = [c for c in calls if c[1].get("type") == "expected_move"]
    assert len(redraws) == 2
    assert redraws[-1][1]["args"]["legs"] == [
        {"strike": 550.0, "option_type": "put", "side": "short"}]
    assert redraws[-1][1]["args"]["lookback"] == "3mo"


def test_lookback_change_untouched_keeps_handoff_legs(monkeypatch):
    """A multi-leg handoff (PCS/IC/…) the user has NOT touched the strike
    dropdown for must re-send its OWN legs on a look-back change, not an
    empty override — the strike_touched gate exists exactly for this case."""
    from nicegui import ui

    import bus_client

    bus_client.reset()
    handed_legs = [
        {"strike": 540.0, "option_type": "put", "side": "short"},
        {"strike": 535.0, "option_type": "put", "side": "long"},
    ]
    handoff.set_pending_expected_move(
        {"symbol": "SPY", "expiry": "2026-09-18", "legs": handed_legs})

    calls = []
    monkeypatch.setattr(bus_client, "request",
                        lambda domain, command: calls.append((domain, command)))

    with ui.card() as card:
        em.render()

    lookback_sel = _find(card, ui.select, "Look-back")

    handed_draws = [c for c in calls if c[1].get("type") == "expected_move"]
    assert len(handed_draws) == 1
    assert handed_draws[0][1]["args"]["legs"] == handed_legs

    lookback_sel.value = "1y"
    redraws = [c for c in calls if c[1].get("type") == "expected_move"]
    assert len(redraws) == 2
    assert redraws[-1][1]["args"]["legs"] == handed_legs  # untouched -> preserved
    assert redraws[-1][1]["args"]["lookback"] == "1y"
