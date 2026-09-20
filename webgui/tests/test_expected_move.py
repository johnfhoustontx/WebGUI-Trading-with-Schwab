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
    # EM cone lines are SOLID (no dashStyle) — a dashed cone read as "broken".
    assert all("dashStyle" not in s for s in fig["series"] if s["type"] == "spline")
    assert len(fig["yAxis"]["plotLines"]) == 2
    # No hover-dimming of the non-hovered series (candles / EM lines).
    assert fig["plotOptions"]["series"]["states"]["inactive"]["enabled"] is False


def test_expected_move_figure_handles_empty_payload():
    fig = em.expected_move_figure({})
    assert fig["series"] == [] or all(not s.get("data") for s in fig["series"])


def test_em_lookback_options_has_auto_and_overrides():
    opts = em.em_lookback_options()
    assert opts["auto"].startswith("Auto")
    for key in ("1mo", "3mo", "6mo", "1y"):
        assert key in opts


# --- summary_text ---------------------------------------------------------

def test_summary_text_full_payload():
    payload = {
        "spot": 772.49, "atm_iv": 0.1158, "expiry": "2026-09-18",
        "em_upper": [[1, 780.0], [2, 781.41]],
    }
    assert em.summary_text(payload) == (
        "Spot 772.49 · ATM IV 11.58% · Expected move ±8.92 (±1.15%) to 2026-09-18")


def test_summary_text_uses_last_cone_point_not_recomputed():
    """The dollar/percent move must be read off the LAST em_upper point (the
    series actually plotted), not independently recomputed from atm_iv, so it
    can never disagree with the drawn cone."""
    payload = {"spot": 100.0, "atm_iv": 0.5, "expiry": "2026-07-18",
               "em_upper": [[1, 101.0], [2, 103.0]]}
    text = em.summary_text(payload)
    assert "±3.00 (±3.00%)" in text


def test_summary_text_missing_atm_iv_omits_that_clause_only():
    payload = {"spot": 100.0, "atm_iv": None, "expiry": "2026-07-18",
               "em_upper": [[1, 105.0]]}
    text = em.summary_text(payload)
    assert "ATM IV" not in text
    assert "Spot 100.00" in text
    assert "Expected move" in text


def test_summary_text_empty_cone_omits_move_clause_only():
    payload = {"spot": 100.0, "atm_iv": 0.2, "expiry": "2026-07-18", "em_upper": []}
    text = em.summary_text(payload)
    assert "Expected move" not in text
    assert "Spot 100.00" in text and "ATM IV 20.00%" in text


def test_summary_text_missing_spot_omits_spot_and_move():
    payload = {"spot": None, "atm_iv": 0.2, "expiry": "2026-07-18",
               "em_upper": [[1, 105.0]]}
    text = em.summary_text(payload)
    assert "Spot" not in text
    assert "Expected move" not in text
    assert "ATM IV 20.00%" in text


def test_summary_text_no_expiry_drops_to_clause_only():
    payload = {"spot": 100.0, "atm_iv": 0.2, "expiry": None, "em_upper": [[1, 105.0]]}
    text = em.summary_text(payload)
    assert "Expected move ±5.00 (±5.00%)" in text
    assert " to " not in text


def test_summary_text_empty_payload_returns_empty_string():
    assert em.summary_text({}) == ""
    assert em.summary_text(None) == ""


def test_summary_text_total_over_junk_input():
    """Never raises — wrong types, malformed cone points, garbage payload."""
    assert em.summary_text("not a dict") == ""
    assert em.summary_text({"spot": "oops", "atm_iv": float("nan"),
                             "em_upper": [[1]], "expiry": 5}) == ""
    assert em.summary_text({"spot": float("inf"), "em_upper": "nope"}) == ""


# --- nearest_strike --------------------------------------------------------

def test_nearest_strike_picks_closest():
    assert em.nearest_strike([540.0, 545.0, 550.0], 546.0) == 545.0


def test_nearest_strike_tie_breaks_to_lower():
    assert em.nearest_strike([95.0, 105.0], 100.0) == 95.0


def test_nearest_strike_empty_ladder_returns_none():
    assert em.nearest_strike([], 100.0) is None
    assert em.nearest_strike(None, 100.0) is None


def test_nearest_strike_missing_spot_returns_none():
    assert em.nearest_strike([95.0, 100.0], None) is None


def test_nearest_strike_total_over_junk_input():
    assert em.nearest_strike([95.0, 100.0], "oops") is None
    assert em.nearest_strike([95.0, 100.0], float("nan")) is None
    assert em.nearest_strike([95.0, 100.0], -1) == 95.0  # negative spot still resolves
    assert em.nearest_strike(["junk", None, 105.0], 100.0) == 105.0


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
    """A control selected by the label sitting ABOVE it.

    ⚠ Re-aimed 2026-09-20 with the page-kit migration, and deliberately not
    weakened: ``kit.field`` puts a field's name in a sibling ``ui.label`` at the
    top of a one-column wrapper and never writes Quasar's ``label`` prop, so the
    old selector (``el._props.get("label") == label``) found nothing and all
    seven call sites below raised. They still name the same four controls."""
    from nicegui import ui as _ui

    for el in _descendants(card):
        kids = getattr(getattr(el, "default_slot", None), "children", [])
        if not any(isinstance(k, _ui.label) and k.text == label for k in kids):
            continue
        for k in kids:
            if isinstance(k, cls):
                return k
    raise AssertionError(f"no {cls.__name__} under a {label!r} label in the built page")


def _button(card, text):
    """The page's button carrying ``text`` (the kit builds one per action)."""
    from nicegui import ui as _ui

    for el in _descendants(card):
        if isinstance(el, _ui.button) and el.text == text:
            return el
    raise AssertionError(f"no button labelled {text!r} in the built page")


def _scrim(card):
    """The wait scrim ``pages/busy.py`` mounts over the chart — the element that
    holds the spinner, selected by that rather than by position."""
    from nicegui import ui as _ui

    for el in _descendants(card):
        kids = getattr(getattr(el, "default_slot", None), "children", [])
        if any(isinstance(k, _ui.spinner) for k in kids):
            return el
    raise AssertionError("the page mounts no wait scrim")


def _page_polls(callbacks):
    """The page's OWN coalesced cache poll, selected by name.

    ⚠ Re-aimed 2026-09-20, and by name rather than by position on purpose. Two
    timers the page does not own now sit alongside it: ``pages/busy.py``'s 1 s
    deadline watchdog per spinner (which the old filter already excluded), and
    ``kit.header``'s 5 s stamp poll — which is built FIRST, so "the first timer"
    stopped being the page's. Neither is a cache poll, and neither may be
    mistaken for one when a test reaches for "the poll"."""
    return [c for c in callbacks
            if getattr(c, "__qualname__", "").rsplit(".", 1)[-1] == "_poll"]


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

    # The invariant is one coalesced POLL, not one timer on the page: the inline
    # busy spinner owns a watchdog timer of its own (pages/busy.py), which is
    # unrelated to the cache polling this test is about.
    polls = _page_polls(captured_callbacks)
    assert len(polls) == 1, "expected exactly ONE coalesced poll timer"
    poll = polls[0]

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


def test_strike_defaults_to_nearest_spot_no_handoff(monkeypatch):
    """Standalone flow (no handoff): once a chain lands and the user picks
    the expiry, the Strike dropdown should silently pre-select the strike
    nearest spot — so the user isn't scrolling a 181-strike ladder."""
    import nicegui.ui as ng_ui
    from nicegui import ui

    import bus_client

    bus_client.reset()
    handoff.take_pending_expected_move()  # clear any stray pending stash

    monkeypatch.setattr(bus_client, "request", lambda domain, command: None)
    captured_callbacks = []
    monkeypatch.setattr(ng_ui, "timer", _make_capturing_timer(captured_callbacks))

    with ui.card() as card:
        em.render()

    poll = _page_polls(captured_callbacks)[0]
    expiry_sel = _find(card, ui.select, "Expiry")
    strike_sel = _find(card, ui.select, "Strike (optional)")

    assert strike_sel.value is None  # nothing to default to before a chain loads

    bus_client.bus().cache_set("cache:options:em_chain", {
        "symbol": "SPY", "api": "SPY", "spot": 551.0,
        "expirations": ["2026-09-18"],
        "strikes": {"2026-09-18": [540.0, 550.0, 555.0]}, "error": None})
    poll()
    expiry_sel.value = "2026-09-18"  # real user pick -> populates the ladder

    assert strike_sel.value == 550.0  # nearest to spot 551.0


def test_handoff_multileg_survives_chain_auto_select_strike(monkeypatch):
    """A multi-leg handoff (PCS/IC/…) must still show ALL its legs — and
    strike_touched must stay False — after the chain loads and _fill_strikes
    runs its nearest-to-spot default. Unlike the no-handoff case above, the
    default must be SKIPPED entirely here (the Strike dropdown stays empty):
    silently pre-filling it would make a later, unrelated put/call toggle
    look like "the user already picked a strike" and clobber the handed
    lines with a single-strike override (see _fill_strikes)."""
    import nicegui.ui as ng_ui
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
    captured_callbacks = []
    monkeypatch.setattr(ng_ui, "timer", _make_capturing_timer(captured_callbacks))

    with ui.card() as card:
        em.render()

    poll = _page_polls(captured_callbacks)[0]
    strike_sel = _find(card, ui.select, "Strike (optional)")
    lookback_sel = _find(card, ui.select, "Look-back")

    handed_draws = [c for c in calls if c[1].get("type") == "expected_move"]
    assert len(handed_draws) == 1
    assert handed_draws[0][1]["args"]["legs"] == handed_legs

    # The chain lands — a naive "nearest to spot" default would land on 550,
    # which must NOT happen while the handoff's legs are unpreserved.
    bus_client.bus().cache_set("cache:options:em_chain", {
        "symbol": "SPY", "api": "SPY", "spot": 550.0,
        "expirations": ["2026-09-18"],
        "strikes": {"2026-09-18": [540.0, 545.0, 550.0]}, "error": None})
    poll()

    assert strike_sel.value is None  # the default was skipped, not just guarded

    # No spurious redraw came from the chain landing (same symbol, no expiry
    # string change), and — the strike_touched proof — a look-back change
    # still resends the ORIGINAL handed legs, not a single-strike override.
    draws_after_chain = [c for c in calls if c[1].get("type") == "expected_move"]
    assert len(draws_after_chain) == 1

    lookback_sel.value = "1y"
    redraws = [c for c in calls if c[1].get("type") == "expected_move"]
    assert len(redraws) == 2
    assert redraws[-1][1]["args"]["legs"] == handed_legs


def test_a_symbol_only_hand_off_loads_the_chain_without_a_warning(monkeypatch):
    """The Symbol Dossier hands over a symbol with no expiry. That must open
    the page on the symbol and load its expirations — not also try to draw and
    complain at a user who asked for nothing wrong.

    ⚠ The complaint it names was the ``"Symbol + expiry required."`` toast, and
    that toast is gone (2026-09-20 — Draw is simply held until both fields are
    set). An assertion about its absence would now be vacuous, so this asserts
    the stronger thing it always meant: the page says nothing at all. Green both
    before and after."""
    from nicegui import ui

    import bus_client

    bus_client.reset()
    handoff.take_pending_expected_move()
    calls, toasts = [], []
    monkeypatch.setattr(bus_client, "request",
                        lambda domain, command: calls.append((domain, command)))
    monkeypatch.setattr(ui, "notify", lambda msg, **k: toasts.append(msg))
    handoff.set_pending_expected_move({"symbol": "MU"})

    with ui.card() as card:
        em.render()

    assert _find(card, ui.input, "Symbol").value == "MU"
    kinds = [c[1].get("type") for c in calls]
    assert "em_chain" in kinds
    assert "expected_move" not in kinds
    assert toasts == []


# --- the page kit (2026-09-20) -------------------------------------------

def test_the_frame_is_the_kit_and_the_page_finally_names_itself():
    """Nothing on screen said what this page WAS: it carries no title of its own
    and no tab strip sits above it, so a reader arriving from a hand-off saw a
    control row and a chart. The kit header is the first thing that names it."""
    import inspect

    src = inspect.getsource(em.render)
    assert "kit.page()" in src
    assert ('kit.header("Expected Move", view="options:expected_move", stale=False)'
            in src), "the header names the page and stamps the on-demand view"
    assert "with kit.control_bar():" in src
    assert "kit.status_line()" in src
    # BTN_3D is the legacy alias the design retires.
    assert "BTN_3D" not in inspect.getsource(em)


def test_the_missing_field_toast_is_gone_because_draw_is_simply_held():
    """"Symbol + expiry required." was FIELD VALIDATION delivered as an outcome
    toast — and one caller already routed around it deliberately (the Symbol
    Dossier's symbol-only hand-off). Holding Draw says the same thing before the
    click instead of after it."""
    import inspect

    src = inspect.getsource(em)
    # Both spellings: routing it through ``kit.toast`` instead would keep the
    # very shape this replaces - validation announced after the click.
    assert "ui.notify" not in src
    assert "kit.toast(" not in src
    assert "kit.gate(draw_btn, symbol_in, expiry_sel)" in inspect.getsource(em.render)


def test_draw_is_held_until_both_a_symbol_and_an_expiry_are_set(monkeypatch):
    """The behaviour the toast used to report after the fact."""
    from nicegui import ui

    import bus_client

    bus_client.reset()
    handoff.take_pending_expected_move()
    monkeypatch.setattr(bus_client, "request", lambda domain, command: None)

    with ui.card() as card:
        em.render()

    draw = _button(card, "Draw")
    symbol_in = _find(card, ui.input, "Symbol")
    expiry_sel = _find(card, ui.select, "Expiry")

    assert not draw.enabled, "no expiry has been picked yet"

    expiry_sel.options = em.expiry_options(["2026-09-18"])
    expiry_sel.value = "2026-09-18"
    assert draw.enabled, "symbol + expiry are both set"

    symbol_in.value = ""
    assert not draw.enabled, "an empty symbol holds it again"


def test_a_chain_landing_mid_compute_leaves_the_compute_spinner_up(monkeypatch):
    """Two independent fetches share ONE scrim over the chart: loading a
    symbol's expirations, and computing the move. Until 2026-09-20 either
    version bump called ``chart_busy.hide()`` outright, so a chain load landing
    while a compute was still running took the compute's spinner down with it —
    the page read as finished while it was still working."""
    import nicegui.ui as ng_ui
    from nicegui import ui

    import bus_client

    bus_client.reset()
    handoff.take_pending_expected_move()
    calls = []
    monkeypatch.setattr(bus_client, "request",
                        lambda domain, command: calls.append((domain, command)))
    captured_callbacks = []
    monkeypatch.setattr(ng_ui, "timer", _make_capturing_timer(captured_callbacks))

    with ui.card() as card:
        em.render()

    poll = _page_polls(captured_callbacks)[0]
    scrim = _scrim(card)
    expiry_sel = _find(card, ui.select, "Expiry")

    # render() loaded the chain, so the page is already waiting on expirations.
    assert scrim.visible

    # The user picks an expiry — a SECOND wait, the compute, starts under it.
    expiry_sel.options = em.expiry_options(["2026-09-18"])
    expiry_sel.value = "2026-09-18"
    assert [c for c in calls if c[1].get("type") == "expected_move"]

    # The CHAIN lands first.
    bus_client.bus().cache_set("cache:options:em_chain", {
        "symbol": "SPY", "api": "SPY", "spot": 550.0,
        "expirations": ["2026-09-18"],
        "strikes": {"2026-09-18": [545.0, 550.0]}, "error": None})
    poll()
    assert scrim.visible, "the compute is still running; its spinner must stay up"

    # …and the compute landing takes it down — the non-vacuity half.
    bus_client.bus().cache_set("cache:options:expected_move", {
        "symbol": "SPY", "expiry": "2026-09-18", "spot": 550.0, "atm_iv": 0.2,
        "candles": [], "em_upper": [], "em_lower": [], "legs": [], "error": None})
    poll()
    assert not scrim.visible, "nothing is outstanding any more"


def test_the_charts_data_colours_and_leg_encoding_are_untouched():
    """A must-not-change guard: green on both sides of the migration. The candle
    up/down, the cone's two ends, the put/call hues and the short-solid /
    long-dashed leg encoding are DATA."""
    assert (em.UP_COLOR, em.DOWN_COLOR) == ("#26a69a", "#ef5350")
    assert (em.EM_UP_COLOR, em.EM_DOWN_COLOR) == ("#66bb6a", "#ef5350")
    assert (em.PUT_COLOR, em.CALL_COLOR) == ("#ef9a9a", "#90caf9")
    lines = em.leg_lines([{"strike": 5.0, "option_type": "call", "side": "long"},
                          {"strike": 4.0, "option_type": "put", "side": "short"}])
    assert lines[0]["color"] == em.CALL_COLOR and lines[0]["dashStyle"] == "Dash"
    assert lines[1]["color"] == em.PUT_COLOR and "dashStyle" not in lines[1]


def test_the_stock_module_construction_survives_the_migration():
    """A must-not-change guard: green on both sides. ``extras=["stock"]`` +
    ``type="stockChart"`` + in-place ``update()`` is the combination CLAUDE.md's
    blanket rule forbade and commit 4980539 measured and settled FOR THIS PAGE
    (1 → 3 series, no throw). Nothing here may drift back."""
    import inspect

    src = inspect.getsource(em.render)
    assert 'type="stockChart"' in src and 'extras=["stock"]' in src
    fig = em.expected_move_figure(_payload())
    assert fig["xAxis"]["ordinal"] is True
    assert fig["rangeSelector"]["enabled"] is False
    assert fig["navigator"]["enabled"] is False
    assert fig["scrollbar"]["enabled"] is False
