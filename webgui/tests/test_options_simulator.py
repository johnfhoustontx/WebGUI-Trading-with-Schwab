"""Tests for the Simulator page pure figure/transform builders + Tier-3 wiring.

The ChainSnapshot fetch + sweep engines moved to ``services/options_svc/compute``
(``sim_fetch``/``sim_run``); the page now renders from the Redis cache and drives
compute via commands. The snapshot-object helpers (``expiries_of``/``strikes_of``/
``find_contract``) moved to the service, so their tests live in the service suite.
"""
from pages.options import simulator as sim


def test_whatif_figure_highcharts_dict():
    df = [{"S": 440, "theo_price": -50}, {"S": 450, "theo_price": 0}, {"S": 460, "theo_price": 80}]
    fig = sim.whatif_figure(df, spot=450.0, target_s=455.0)
    assert "series" in fig
    data = fig["series"][0]["data"]
    assert data[0][0] == 440 and data[-1][0] == 460       # [x, y] pairs
    # spot + target are vertical plotLines on xAxis; zero baseline on yAxis.
    assert len(fig["xAxis"]["plotLines"]) == 2
    assert len(fig["yAxis"]["plotLines"]) == 1
    assert fig["accessibility"]["enabled"] is False


def test_whatif_figure_no_target_omits_overlay():
    fig = sim.whatif_figure([{"S": 1, "theo_price": 2}], spot=1.0)
    # Spot line only (no ΔS overlay) + the zero baseline.
    assert len(fig["xAxis"]["plotLines"]) == 1
    assert len(fig["yAxis"]["plotLines"]) == 1


def test_whatif_pnl_zeroes_at_spot():
    # P/L = theo(S) − theo(spot): zero at the spot row, relative elsewhere.
    df = [{"S": 100, "theo_price": 5}, {"S": 110, "theo_price": 8},
          {"S": 120, "theo_price": 20}]
    assert sim.whatif_pnl(df, spot=110) == [[100, -3], [110, 0], [120, 12]]
    assert sim.whatif_pnl([], 110) == []          # empty-safe
    assert sim.whatif_pnl(df, spot=None) == [[100, 5], [110, 8], [120, 20]]  # no spot ⇒ raw


def test_whatif_pnl_from_entry_baseline():
    # value(S) already in dollars (×100 contract multiplier); ``baseline`` = the
    # entry mark (position value at spot, now). The SNDK call credit spread: −5,450
    # at entry, 0 if it expires worthless (max profit = the credit), −20,000 deep
    # ITM (max loss = width − credit). P/L = value(S) − baseline → from-entry payoff.
    rows = [{"S": 1893, "theo_price": -5450.0},
            {"S": 1700, "theo_price": 0.0},
            {"S": 2100, "theo_price": -20000.0}]
    assert sim.whatif_pnl(rows, spot=1893, baseline=-5450.0) == [
        [1893, 0.0], [1700, 5450.0], [2100, -14550.0]]


def test_whatif_pnl_baseline_overrides_zero_at_spot():
    # Once Δt elapses, the nearest-spot row (forward-time value) differs from the
    # entry mark; the explicit baseline must win (from-entry, not "zero at spot").
    rows = [{"S": 100, "theo_price": -300.0},      # row nearest spot, forward-t
            {"S": 120, "theo_price": -2000.0}]
    assert sim.whatif_pnl(rows, spot=100) == [[100, 0.0], [120, -1700.0]]            # legacy
    assert sim.whatif_pnl(rows, spot=100, baseline=-545.0) == [[100, 245.0], [120, -1455.0]]


def test_whatif_figure_threads_baseline():
    rows = [{"S": 1700, "theo_price": 0.0}, {"S": 1893, "theo_price": -5450.0},
            {"S": 2100, "theo_price": -20000.0}]
    data = dict(sim.whatif_figure(rows, spot=1893, baseline=-5450.0)["series"][0]["data"])
    assert data[1700] == 5450.0 and data[2100] == -14550.0   # from-entry profit / loss


def test_whatif_figure_profit_loss_shading_and_bands():
    # The payoff restyle: an area split at the 0 threshold — green (profit) above,
    # red (loss) below — for both line and fill, with faint Profit/Loss background
    # bands. An explicit base color avoids Highcharts' default-blue base path.
    fig = sim.whatif_figure([{"S": 100, "theo_price": 5}, {"S": 110, "theo_price": 8},
                             {"S": 120, "theo_price": 20}], spot=110.0)
    s = fig["series"][0]
    assert fig["chart"]["type"] == "area" and s["type"] == "area"
    assert s["threshold"] == 0
    assert s["color"] == sim.PNL_GREEN and s["negativeColor"] == sim.PNL_RED
    assert s["fillColor"] == sim._PNL_GREEN_FILL and s["negativeFillColor"] == sim._PNL_RED_FILL
    assert {b["label"]["text"] for b in fig["yAxis"]["plotBands"]} == {"Profit", "Loss"}
    assert [110, 0] in s["data"]                  # zero at spot


def test_whatif_figure_sets_explicit_height():
    # This chart mounts inside an INACTIVE tab panel (default tab is Replay). NiceGUI's
    # highchart only reflows once at mount and never re-measures, so a chart that
    # mounts in a 0-height (display:none) panel must carry an explicit height or it
    # collapses to title-height when the tab is finally shown (the IV-shock bug —
    # that chart is now a table, 2026-09-11, so only What-if still needs this).
    wf = sim.whatif_figure([{"S": 1, "theo_price": 2}], spot=1.0)
    assert isinstance(wf["chart"].get("height"), (int, float)) and wf["chart"]["height"] >= 300


def test_replay_figure_stacks_price_and_greeks():
    trace = {
        "spot": 452.0,
        "timestamps": ["2026-06-18T09:30:00", "2026-06-18T09:31:00"],
        "x": [0, 1],
        "prices": [450.0, 451.0],
        "greeks": {"delta": [0.5, 0.55], "gamma": [0.01, 0.01],
                   "theta": [-0.1, -0.1], "vega": [0.2, 0.2], "rho": [0.05, 0.05]},
        "gaps": [], "sessions": [{"start": 0, "end": 2, "date": "2026-06-18"}],
        "ticks": {"pos": [0, 1], "labels": ["09:30", "09:31"]},
        "resolution": "2 bars, 1-min × 1 sessions",
    }
    trace["value"] = [-1000.0, -900.0]
    trace["pnl"] = [0.0, 100.0]
    trace["units"] = "position"
    fig = sim.replay_figure(trace, cursor=1)
    # 6 stacked series over 6 stacked yAxes: price, the POSITION's P/L, then four
    # Greeks. Rho left the panels on 2026-09-11 (still in the payload) — it is the
    # Greek that matters least at these tenors, and a seventh panel crowded all six.
    assert [s["name"] for s in fig["series"]] == [
        "Price", "Profit / loss", "Delta", "Gamma", "Theta per day", "Vega"]
    assert len(fig["yAxis"]) == 6
    # Each series points at its own yAxis index.
    assert [s["yAxis"] for s in fig["series"]] == [0, 1, 2, 3, 4, 5]
    assert fig["series"][0]["data"] == [[0, 450.0], [1, 451.0]]
    assert fig["series"][1]["data"] == [[0, 0.0], [1, 100.0]]
    # Scrub cursor present as an xAxis plotLine at the cursor index.
    assert any(pl["value"] == 1 for pl in fig["xAxis"]["plotLines"])


def test_replay_figure_draws_session_boundaries():
    trace = {
        "x": [0, 1, 2, 3], "prices": [1, 2, 3, 4],
        "greeks": {g: [0, 0, 0, 0] for g in ("delta", "gamma", "theta", "vega", "rho")},
        "sessions": [{"start": 0, "end": 2, "date": "2026-06-18"},
                     {"start": 2, "end": 4, "date": "2026-06-19"}],
        "resolution": "x",
    }
    fig = sim.replay_figure(trace, cursor=None)
    # Boundary before the 2nd session (start 2 -> plotLine at 1.5); no cursor line.
    assert any(pl["value"] == 1.5 for pl in fig["xAxis"]["plotLines"])
    assert not any(pl.get("color") == sim.CURSOR_COLOR for pl in fig["xAxis"]["plotLines"])


def test_replay_figure_empty_trace_is_safe():
    fig = sim.replay_figure({}, cursor=0)
    assert len(fig["series"]) == 6
    assert all(s["data"] == [] for s in fig["series"])


def test_lookback_options_has_auto_and_overrides():
    opts = sim.lookback_options()
    assert opts["auto"] == "Auto (by DTE)"
    # The override keys must match the service-side override keys.
    for key in ("1m_1d", "5m_3d", "5m_5d", "15m_10d", "1d_20d"):
        assert key in opts


def test_replay_figure_tooltip_limits_decimals():
    # Hover readout must not show raw float precision (e.g. 0.4879016861546994).
    trace = {"x": [0, 1], "prices": [450.0, 451.0],
             "greeks": {g: [0.0, 0.0] for g in ("delta", "gamma", "theta", "vega", "rho")},
             "sessions": [], "resolution": "x"}
    fig = sim.replay_figure(trace, cursor=0)
    assert fig["tooltip"]["valueDecimals"] == 2


def test_records_normalizes_df_and_list():
    class _DF:
        def to_dict(self, orient):
            return [{"S": 1}]

    assert sim._records(_DF()) == [{"S": 1}]
    assert sim._records([{"S": 2}]) == [{"S": 2}]
    assert sim._records(None) == []


def test_plotline_fields():
    pl = sim._plotline(450.0, "#fff", dash="Dash")
    assert pl["value"] == 450.0
    assert pl["dashStyle"] == "Dash"


def test_simulator_module_imports_no_engine_or_proxy():
    """Regression: the page must NOT import the engine, proxy, numpy, or splice
    OPTIONS_SCANNER onto sys.path — all of that moved into the options service."""
    import pathlib

    src = pathlib.Path(sim.__file__).read_text(encoding="utf-8")
    for forbidden in ("options_simulator", "import proxy", "OPTIONS_SCANNER",
                      "import numpy"):
        assert forbidden not in src, f"simulator.py must not reference {forbidden!r}"


def test_render_callable():
    assert callable(sim.render)


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service cold) — the Tier-3 graceful-empty path. Mirrors the swing
    page test: rendering inside a slot context exercises the widget wiring + the
    initial fetch-free paint (no meta → fetch prompt, no result → select prompt).
    """
    import bus_client
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:sim_meta") is None
    assert bus_client.read("options:sim_result") is None
    with ui.card():
        sim.render()  # must not raise


def test_render_with_warm_meta_seeds_strategy_editor():
    """render() with a warm sim_meta snapshot must mount the strategy dropdown +
    leg editor and seed the default (PCS) template against the cached strikes/
    expiries WITHOUT raising — exercises the _apply_meta editor-population path +
    the option-type→``kind`` leg-payload mapping (the cold-cache test skips both
    since no meta arrives)."""
    import bus_client
    from nicegui import ui

    bus_client.reset()
    meta = {
        "symbol": "SPY", "spot": 450.0, "n_contracts": 12,
        "expiries": ["2026-06-26", "2026-07-03"],
        "strikes": {
            "2026-06-26": {"call": [445.0, 450.0, 455.0], "put": [445.0, 450.0, 455.0]},
            "2026-07-03": {"call": [445.0, 450.0, 455.0], "put": [445.0, 450.0, 455.0]},
        },
    }
    bus_client.bus().cache_set("cache:options:sim_meta", meta)
    with ui.card():
        sim.render()  # must not raise (editor seeded from the warm meta)


def test_sim_capture_keys_cover_inputs():
    # Guard against forgetting to persist a Simulator input across navigation.
    assert set(sim._SIM_KEYS) == {
        "symbol", "strategy", "legs", "dt", "mult", "lookback", "ds", "active_tab"}


def test_sim_snapshot_roundtrips_via_page_state():
    from pages.options import page_state as ps
    vals = {"symbol": "AAPL", "strategy": "IC", "legs": [{"option_type": "put"}],
            "dt": 7.0, "mult": 2.0, "lookback": "5m_3d", "ds": -3.0,
            "active_tab": "Volatility", "junk": 1}
    snap = ps.snapshot(vals, sim._SIM_KEYS)
    assert "junk" not in snap and snap["dt"] == 7.0
    assert ps.merge_restore(snap, sim._SIM_DEFAULTS)["symbol"] == "AAPL"


# -- the shared entry panel + the leg TABLE (2026-09-12) ------------------------
# The Simulator mounts the shared entry panel and the leg editor's
# ``layout="table"``, keeping the app-wide dark navy — the near-black CALC_*
# language belongs to the Calculator alone. Everything below asserts against the
# MOUNTED element tree rather than the call site's source: a source grep passes
# on a call that never renders, and it cannot see which palette reached the DOM.

_SIM_META = {
    "symbol": "SPY", "spot": 450.0, "n_contracts": 12,
    "expiries": ["2026-06-26", "2026-07-03"],
    "strikes": {e: {"call": [445.0, 450.0, 455.0], "put": [445.0, 450.0, 455.0]}
                for e in ("2026-06-26", "2026-07-03")},
}


def _sim_chain(delta=-0.31):
    contract = [{"bid": 1.0, "ask": 1.2, "mark": 1.1, "delta": delta,
                 "openInterest": 900, "volatility": 18.0}]
    side = {f"{e}:9": {f"{k}": contract for k in (445.0, 450.0, 455.0)}
            for e in ("2026-06-26", "2026-07-03")}
    return {"symbol": "SPY", "chain": {"callExpDateMap": side, "putExpDateMap": side}}


def _sim_container():
    """Render the Simulator over a warm sim_meta; return the mount container."""
    import bus_client
    from nicegui import ui
    from pages.options import shared_position

    bus_client.reset()
    sim._LAST_SIM.clear()
    shared_position.reset()
    bus_client.bus().cache_set("cache:options:sim_meta", _SIM_META)
    with ui.card() as container:
        sim.render()
    return container


def _leg_rows(container):
    return [e for e in container.descendants() if "leg-trow" in e._classes]


def _labels(el):
    from nicegui import ui
    return [e.text for e in el.descendants() if isinstance(e, ui.label)]


def _sim_buttons(container):
    from nicegui import ui
    return [e for e in container.descendants() if isinstance(e, ui.button)]


def _fire_click(el):
    # snapshot: the handler re-renders, which deletes elements and mutates the
    # listener registry mid-iteration
    for listener in list(el._event_listeners.values()):
        if listener.type == "click":
            listener.handler(None)


def _hooked(container, cls):
    return [e for e in container.descendants() if cls in e._classes]


def test_simulator_mounts_the_leg_editor_as_a_table_not_cards_or_rows():
    """Seeded PCS = two legs, so two table rows — and neither older layout's
    artefact survives."""
    container = _sim_container()
    assert len(_leg_rows(container)) == 2
    for gone in ("leg-card", "leg-row", "leg-head"):
        assert not _hooked(container, gone), gone


def test_simulator_mounts_the_shared_entry_panel_with_one_ticker():
    container = _sim_container()
    assert len(_hooked(container, "entry-panel")) == 1
    assert len(_hooked(container, "entry-ticker")) == 1


def test_simulator_table_carries_the_column_captions():
    head = _hooked(_sim_container(), "leg-thead")[0]
    txt = _labels(head)
    for cap in ("SIDE", "QTY", "EXPIRY", "STRIKE", "TYPE"):
        assert cap in txt, cap


def test_simulator_keeps_the_default_navy_palette():
    """No ``tokens`` — the Simulator stays app-wide dark navy. The assertion is
    against the shared defaults (and the absence of the CALC_* language), not
    against whatever the Calculator happens to use today."""
    from pages.options import entry_panel as EP
    from pages.options import leg_editor as LE
    from pages.options import theme

    container = _sim_container()
    rows = _leg_rows(container)
    for cls in LE.DEFAULT_LEG_TOKENS["frame"].split():
        assert cls in rows[0]._classes, cls
    for cls in LE.DEFAULT_LEG_TOKENS["side_short"].split():      # PCS leg 1 sells
        assert cls in _hooked(container, "leg-side")[0]._classes, cls
    panel = _hooked(container, "entry-panel")[0]
    for cls in EP.DEFAULT_PANEL_TOKENS["frame"].split():
        assert cls in panel._classes, cls
    shared = ({c for v in LE.DEFAULT_LEG_TOKENS.values() for c in v.split()}
              | {c for v in EP.DEFAULT_PANEL_TOKENS.values() for c in v.split()})
    calc = {c for name in dir(theme) if name.startswith("CALC_")
            for c in str(getattr(theme, name)).split()
            if c.startswith(("bg-[", "text-[", "border-[", "border-l-["))}
    on_page = {c for e in container.descendants() for c in e._classes}
    assert calc, "no CALC_* palette found to compare against"
    for cls in calc - shared:
        assert cls not in on_page, cls


def test_simulator_table_has_no_price_column():
    """``show_premium=False`` survives: the simulator prices each leg off the
    chain's IV, so a typed premium would be a lie. The PRICE track collapses."""
    from nicegui import ui
    from pages.options import leg_editor as LE

    container = _sim_container()
    assert "PRICE" not in _labels(_hooked(container, "leg-thead")[0])
    row = _leg_rows(container)[0]
    assert not _hooked(row, "leg-price")
    assert len([e for e in row.descendants() if isinstance(e, ui.number)]) == 1  # qty only
    want = [c for c in LE._TABLE_GRIDS[(False, True)].split() if c.startswith("grid-cols-")]
    assert want[0] in row._classes


def test_simulator_delta_is_an_em_dash_until_the_chain_lands_then_the_chains():
    """The grid's chain (``options:sim_chain``, from the SAME fetch as the
    snapshot) is the delta source. Before it lands the cell is an em-dash —
    never a confident 0.00."""
    import asyncio

    import bus_client
    container = _sim_container()
    deltas = [e.text for e in _hooked(container, "leg-delta")]
    assert deltas and all(t == "\u2014" for t in deltas)
    assert not [t for t in deltas if t in ("+0.00", "-0.00")]

    bus_client.bus().cache_set("cache:options:sim_meta", _SIM_META)
    bus_client.bus().cache_set("cache:options:sim_chain", _sim_chain(delta=-0.31))
    _fire(container, "_poll_meta")
    _run_async(container, "_poll_chain", asyncio)
    deltas = [e.text for e in _hooked(container, "leg-delta")]
    assert "+0.31" in deltas and "-0.31" in deltas     # the short put inverts


def _run_async(container, name, asyncio):
    from nicegui import ui
    timers = [e for e in container.descendants()
              if isinstance(e, ui.timer) and getattr(e.callback, "__name__", "") == name]
    assert timers, f"no {name} timer mounted"
    result = timers[0].callback()
    if asyncio.iscoroutine(result):
        asyncio.new_event_loop().run_until_complete(result)


def test_a_sim_chain_for_another_symbol_is_not_painted():
    import asyncio

    import bus_client
    container = _sim_container()
    bus_client.bus().cache_set("cache:options:sim_meta", _SIM_META)
    _fire(container, "_poll_meta")
    other = _sim_chain()
    other["symbol"] = "QQQ"
    bus_client.bus().cache_set("cache:options:sim_chain", other)
    _run_async(container, "_poll_chain", asyncio)
    assert _grid_strikes(container) == []
    assert "Load a symbol to see its chain." in _labels(container)


def test_a_grid_click_on_an_unmatched_side_adds_a_leg_and_prices_the_new_position():
    import asyncio

    import bus_client
    container = _render_cold()
    bus_client.bus().cache_set("cache:options:sim_meta", _SIM_META)
    bus_client.bus().cache_set("cache:options:sim_chain", _sim_chain())
    _fire(container, "_poll_meta")
    _run_async(container, "_poll_chain", asyncio)
    assert _grid_strikes(container) == [445.0, 450.0, 455.0]
    _click_grid(container, "ask", "call", 455.0)
    assert len(_leg_rows(container)) == 3
    legs = _last_command("sim_run")["args"]["legs"]
    assert {"kind": "call", "strike": 455.0, "expiry": "2026-06-26",
            "side": "long", "qty": 1} in legs


def _grid_strikes(container):
    import re
    body = _hooked(container, "entry-gridbody")[0]
    return [float(k) for k in re.findall(r'class="entry-grow[^"]*" data-strike="([^"]+)"',
                                         body.content)]


def _click_grid(container, pick, side, strike):
    """A delegated grid click, as the browser's js_handler emits it."""
    from types import SimpleNamespace
    body = _hooked(container, "entry-gridbody")[0]
    for listener in list(body._event_listeners.values()):
        if listener.type == "click":
            listener.handler(SimpleNamespace(
                args={"pick": pick, "side": side, "strike": f"{strike:g}"}))


def _fire_click_on(el):
    for listener in list(el._event_listeners.values()):
        if listener.type == "click":
            listener.handler(None)


def test_an_expiry_pill_moves_every_leg():
    import asyncio

    import bus_client
    container = _render_cold()
    bus_client.bus().cache_set("cache:options:sim_meta", _SIM_META)
    bus_client.bus().cache_set("cache:options:sim_chain", _sim_chain())
    _fire(container, "_poll_meta")
    _run_async(container, "_poll_chain", asyncio)
    pills = _hooked(container, "entry-expiry")
    assert [p.text.split(" · ")[0] for p in pills] == ["Jun 26", "Jul 3"]
    _fire_click_on(pills[1])
    legs = _last_command("sim_run")["args"]["legs"]
    assert {l["expiry"] for l in legs} == {"2026-07-03"}


def test_simulator_floors_the_leg_count_at_one():
    """``min_legs`` is left at the default 1. Both removes are live at the seeded
    two legs; the LAST leg locks — a zero-leg simulator enqueues nothing
    (``_current_params`` returns None) and silently freezes the charts on a stale
    sweep, which is worse than a disabled remove with a tooltip."""
    container = _sim_container()
    removes = [b for b in _sim_buttons(container) if "leg-remove" in b._classes]
    assert len(removes) == 2 and all(b.enabled for b in removes)
    _fire_click(removes[0])
    left = [b for b in _sim_buttons(container) if "leg-remove" in b._classes]
    assert len(left) == 1 and not left[0].enabled


def test_simulator_legs_footer_offers_add_leg_and_no_reset():
    """``on_reset`` stays None: the strategy picker already re-seeds the template
    on every pick, so a "Reset to template" button would be a second control for
    the same act — a Calculator affordance, not one this page asked for.

    Both labels were sentence-cased on 2026-09-20 when the shared leg editor's
    footer moved onto the page kit; the assertion is re-aimed, not relaxed."""
    labels = [b.text for b in _sim_buttons(_sim_container())]
    assert "Add leg" in labels
    assert "Reset to template" not in labels


def test_replay_pnl_panel_is_green_above_zero_and_red_below():
    trace = {"x": [0, 1], "prices": [1, 2], "pnl": [0.0, -50.0], "value": [10.0, -40.0],
             "greeks": {}, "sessions": [], "units": "position"}
    pnl = sim.replay_figure(trace)["series"][1]
    assert pnl["type"] == "area" and pnl["threshold"] == 0
    assert pnl["color"] == sim.PNL_GREEN and pnl["negativeColor"] == sim.PNL_RED


def test_replay_x_axis_shows_dates_not_bar_numbers():
    """The bar index meant nothing to a reader. Categories carry each bar's real
    time (the tooltip header), and the ticks sit on session starts."""
    trace = {
        "x": [0, 1, 2, 3], "prices": [1, 2, 3, 4], "greeks": {},
        "timestamps": ["2026-08-24T09:30:00", "2026-08-24T09:45:00",
                       "2026-08-25T09:30:00", "2026-08-25T09:45:00"],
        "sessions": [{"start": 0, "end": 2, "date": "2026-08-24"},
                     {"start": 2, "end": 4, "date": "2026-08-25"}],
    }
    x = sim.replay_figure(trace)["xAxis"]
    assert x["categories"] == ["Aug 24 09:30", "Aug 24 09:45", "Aug 25 09:30", "Aug 25 09:45"]
    assert x["tickPositions"] == [0, 2]


def test_replay_legacy_per_share_greeks_are_scaled():
    trace = {"x": [0], "prices": [1.0], "greeks": {"delta": [0.45]}, "sessions": []}
    fig = sim.replay_figure(trace)
    assert fig["series"][2]["data"] == [[0, 45.0]]


# -- the 2026-09-11 readouts, driven through the real wiring --------------------
# These render the page, then fire its own poll timers after writing the cache,
# so ``_apply_meta`` / ``_poll_result`` / ``_poll_replay`` run exactly as they do
# in the browser. A source grep would pass on wiring that never runs.

def _future_meta(days_near=10, days_far=40):
    from datetime import date, timedelta
    near = (date.today() + timedelta(days=days_near)).isoformat()
    far = (date.today() + timedelta(days=days_far)).isoformat()
    ladder = {"call": [440.0, 445.0, 450.0, 455.0, 460.0],
              "put": [440.0, 445.0, 450.0, 455.0, 460.0]}
    return {"symbol": "SPY", "spot": 450.0, "n_contracts": 20,
            "expiries": [near, far], "strikes": {near: ladder, far: ladder}}


def _render_cold():
    import bus_client
    from nicegui import ui
    from pages.options import shared_position
    bus_client.reset()
    sim._LAST_SIM.clear()
    shared_position.reset()
    with ui.card() as container:
        sim.render()
    return container


def _fire(container, name):
    """Fire a poll timer INSIDE its own parent slot, the way NiceGUI runs one
    (``Timer._get_context``). Without the slot, anything the callback builds — a
    once-timer, say — lands on the client's ROOT slot instead of on the page, so
    a test looking at ``container.descendants()`` cannot see it."""
    from nicegui import ui
    timers = [e for e in container.descendants()
              if isinstance(e, ui.timer) and getattr(e.callback, "__name__", "") == name]
    assert timers, f"no {name} timer mounted"
    with timers[0].parent_slot:
        timers[0].callback()


def _texts(container, cls):
    from nicegui import ui
    return [d.text for e in container.descendants() if cls in e._classes
            for d in [e, *e.descendants()] if isinstance(d, ui.label)]


def _last_command(kind):
    import json
    import bus_client
    entries = bus_client.bus()._r.xrange("cmd:options")
    for _id, fields in reversed(entries):
        cmd = json.loads(fields["data"])
        if cmd.get("type") == kind:
            return cmd
    return None


def test_the_reload_button_says_load():
    # The shared panel's one Go button replaced "Load chain": Enter or tab-out
    # on the ticker is the load, and this re-pulls the same symbol.
    #
    # Renamed and re-aimed 2026-09-20 (was ..._says_refresh, asserting the
    # upper-case "REFRESH"): a screen with a Symbol control bar carries no
    # header Refresh, because its Load button IS the refresh - the shape
    # rescue.py already ships. The behaviour it names is unchanged.
    labels = [b.text for b in _sim_buttons(_render_cold())]
    assert "Load" in labels
    assert "REFRESH" not in labels
    assert "Load chain" not in labels and "Fetch snapshot" not in labels


def test_six_position_tiles_mount_as_em_dashes_before_any_price():
    container = _render_cold()
    tiles = [e for e in container.descendants() if "sim-tile" in e._classes]
    assert len(tiles) == 6
    values = _texts(container, "sim-tile")
    assert values.count("—") >= 6


def test_the_ivshock_view_is_a_table_not_a_chart():
    from nicegui import ui
    container = _render_cold()
    charts = [e for e in container.descendants() if isinstance(e, ui.highchart)]
    assert len(charts) == 2          # Replay + What-if; IV shock is the table
    assert any("sim-shock-grid" in e._classes for e in container.descendants())


def test_a_legacy_ivshock_cache_renders_in_position_dollars():
    """A result cached BEFORE the service stated its units is per share; the table
    must scale it, not print -$10 for a position worth -$1,000."""
    import bus_client
    from nicegui import ui
    bus_client.reset()
    sim._LAST_SIM.clear()
    base = {"theo_price": -10.0, "delta": 1.45, "gamma": -0.01, "theta": 0.42, "vega": -0.8}
    shock = {"theo_price": -20.5, "delta": 1.9, "gamma": -0.008, "theta": 0.6, "vega": -0.9}
    bus_client.bus().cache_set("cache:options:sim_result", {
        "spot": 450.0, "whatif_rows": [{"S": 440.0, "theo_price": -1500.0},
                                        {"S": 460.0, "theo_price": -500.0}],
        "whatif_baseline": -1000.0, "ivshock": {"base": base, "shock": shock}})
    with ui.card() as container:
        sim.render()
    cells = _texts(container, "sim-shock-grid")
    assert "-$1,000" in cells and "-$2,050" in cells and "-$1,050" in cells


def test_meta_arrival_fits_the_days_slider_and_offers_snaps():
    import bus_client
    from nicegui import ui
    container = _render_cold()
    bus_client.bus().cache_set("cache:options:sim_meta", _future_meta())
    _fire(container, "_poll_meta")

    days = next(e for e in container.descendants() if "sim-days" in e._classes)
    assert 9 <= days._props["max"] <= 11          # the near expiry, not a fixed month
    snaps = [b.text for e in container.descendants() if "sim-snaps" in e._classes
             for b in e.descendants() if isinstance(b, ui.button)]
    assert snaps == ["Now", "Halfway", "Expiry"]
    edited = next(e for e in container.descendants() if "sim-edited" in e._classes)
    assert edited.visible is False                # the template, untouched


def test_a_result_for_the_legs_on_screen_fills_the_tiles():
    """End to end through the wiring: meta lands, the page enqueues sim_run with
    its legs, the service's echo of those legs comes back, and the tiles state
    the entry and the expiry figures."""
    import bus_client
    container = _render_cold()
    bus_client.bus().cache_set("cache:options:sim_meta", _future_meta())
    _fire(container, "_poll_meta")
    run = _last_command("sim_run")
    assert run is not None
    legs = run["args"]["legs"]
    short = next(l for l in legs if l["side"] == "short")
    long_ = next(l for l in legs if l["side"] == "long")
    width = (short["strike"] - long_["strike"]) * 100

    bus_client.bus().cache_set("cache:options:sim_result", {
        "spot": 450.0, "symbol": "SPY", "legs": legs, "dt": 5.0, "mult": 1.5,
        "whatif_rows": [{"S": 400.0, "theo_price": -width}, {"S": 500.0, "theo_price": 0.0}],
        "whatif_baseline": -120.0,
        "ivshock": {"base": {"theo_price": -120.0, "delta": 12.0, "theta": 3.0},
                    "shock": {"theo_price": -160.0, "delta": 14.0, "theta": 4.0},
                    "units": "position"}})
    _fire(container, "_poll_result")
    values = _texts(container, "sim-tile")
    assert "Entry credit" in values and "$120" in values
    assert f"${width - 120:,.0f}" in values        # max loss = width minus the credit
    assert "+12" in values                          # delta, shares-equivalent


def test_a_result_for_other_legs_leaves_the_tiles_waiting():
    import bus_client
    container = _render_cold()
    bus_client.bus().cache_set("cache:options:sim_meta", _future_meta())
    _fire(container, "_poll_meta")
    bus_client.bus().cache_set("cache:options:sim_result", {
        "spot": 450.0, "symbol": "SPY",
        "legs": [{"kind": "call", "strike": 999.0, "expiry": "2030-01-01",
                  "side": "long", "qty": 1}],
        "whatif_rows": [], "whatif_baseline": -120.0, "ivshock": None})
    _fire(container, "_poll_result")
    values = _texts(container, "sim-tile")
    assert "$120" not in values
    assert "waiting for a price" in values


def test_a_new_replay_trace_resizes_the_scrubber_and_parks_it_on_the_last_bar():
    """The bug this pins: ``scrub_slider.max = …`` set a Python attribute the
    browser never saw, so the cursor could only reach bars 0 and 1."""
    import bus_client
    container = _render_cold()
    n = 12
    bus_client.bus().cache_set("cache:options:sim_replay", {
        "x": list(range(n)), "prices": [450.0 + i for i in range(n)],
        "timestamps": [f"2026-08-24T09:{30 + i:02d}:00" for i in range(n)],
        "pnl": [float(i) for i in range(n)], "value": [float(i) for i in range(n)],
        "greeks": {"delta": [50.0] * n}, "sessions": [{"start": 0, "end": n}],
        "units": "position", "resolution": "12 bars"})
    _fire(container, "_poll_replay")
    scrub = next(e for e in container.descendants() if "sim-scrub" in e._classes)
    assert scrub._props["max"] == n - 1
    assert scrub.value == n - 1


# -- review fix: every readout refuses a result priced for other legs ------------

_OTHER_LEGS = [{"kind": "call", "strike": 999.0, "expiry": "2030-01-01",
                "side": "long", "qty": 1}]


def _visible(container, cls):
    return next(e for e in container.descendants() if cls in e._classes).visible


def test_a_stale_result_for_other_legs_draws_no_whatif_and_no_ivshock():
    """Review finding: after a webgui restart the page painted the LAST priced
    position's chart, readout ("At 386.00 …: profit $8,240") and IV-shock verdict
    beside the default template's legs. Every readout now refuses it."""
    import bus_client
    from nicegui import ui
    bus_client.reset()
    sim._LAST_SIM.clear()
    bus_client.bus().cache_set("cache:options:sim_result", {
        "spot": 450.0, "symbol": "SPY", "legs": _OTHER_LEGS, "dt": 5.0, "mult": 1.5,
        "whatif_rows": [{"S": 440.0, "theo_price": -1500.0}, {"S": 460.0, "theo_price": -500.0}],
        "whatif_baseline": -1000.0,
        "ivshock": {"base": {"theo_price": -1000.0}, "shock": {"theo_price": -2050.0},
                    "units": "position"}})
    with ui.card() as container:
        sim.render()
    charts = [e for e in container.descendants() if isinstance(e, ui.highchart)]
    assert all(not c.visible for c in charts)
    assert _texts(container, "sim-readout") == [""]
    assert _visible(container, "sim-shock-grid") is False
    assert _visible(container, "sim-shock-head") is False


def test_a_stale_replay_for_other_legs_is_not_drawn():
    import bus_client
    from nicegui import ui
    container = _render_cold()
    bus_client.bus().cache_set("cache:options:sim_meta", _future_meta())
    _fire(container, "_poll_meta")
    bus_client.bus().cache_set("cache:options:sim_replay", {
        "symbol": "SPY", "legs": _OTHER_LEGS, "x": [0, 1], "prices": [1.0, 2.0],
        "timestamps": ["2026-08-24T09:30:00", "2026-08-24T09:31:00"], "pnl": [0.0, 5.0],
        "greeks": {}, "sessions": [], "units": "position"})
    _fire(container, "_poll_replay")
    charts = [e for e in container.descendants() if isinstance(e, ui.highchart)]
    assert not charts[0].visible                     # the Replay chart
    # Re-aimed 2026-09-20 from the raw ``opacity-70`` class the label used to
    # carry: the three empty states are ``kit.empty`` now, so the page's own
    # hook is what names WHICH of them this is. The behaviour is unchanged.
    assert _texts(container, "sim-replay-empty") == ["Pricing this position…"]


def test_the_calculators_position_names_its_strategy_and_raises_no_edited_chip():
    """Review finding: the picker stayed on PCS after an iron condor was copied in,
    and the Edited chip called legs nobody edited 'edited'. The copy button is gone
    (2026-09-12); the Calculator's position is now simply what the Simulator opens
    with, and the same two guarantees hold."""
    import bus_client
    from nicegui import ui
    from pages.options import shared_position
    bus_client.reset()
    sim._LAST_SIM.clear()
    shared_position.reset()
    meta = _future_meta()
    exp = meta["expiries"][0]
    ic = [{"option_type": "put", "side": "short", "strike": 445.0, "expiry": exp, "qty": 1},
          {"option_type": "put", "side": "long", "strike": 440.0, "expiry": exp, "qty": 1},
          {"option_type": "call", "side": "short", "strike": 455.0, "expiry": exp, "qty": 1},
          {"option_type": "call", "side": "long", "strike": 460.0, "expiry": exp, "qty": 1}]
    shared_position.publish("SPY", "PCS", ic, exp)
    with ui.card() as container:
        sim.render()
    bus_client.bus().cache_set("cache:options:sim_meta", meta)
    _fire(container, "_poll_meta")
    assert _visible(container, "sim-edited") is False
    assert len(_leg_rows(container)) == 4


def test_whatif_tooltip_shows_two_decimals_and_a_leading_minus():
    """Reported from prod: the tooltip printed the raw sweep price
    '87.33760000000001' (twice: the header plus the '{point.x:g}' line) and the
    P/L as '$-1,317'. The price and the P/L now show exactly two decimals, and a
    loss reads '-$1,317.00'. A JS formatter (NiceGUI's ':'-prefixed key) is the
    only way to put the sign before the dollar sign; it also replaces the default
    header, which is where the unrounded x came from."""
    tip = sim.whatif_figure([{"S": 87.33760000000001, "theo_price": -1317.0}], spot=89.0)["tooltip"]
    assert "pointFormat" not in tip and "headerFormat" not in tip
    js = tip[":formatter"]
    assert js.startswith("function(){") and "this.x.toFixed(2)" in js
    assert "minimumFractionDigits:2" in js and "maximumFractionDigits:2" in js
    assert "y<0?'-':''" in js                      # the sign goes before the $


def test_a_landed_meta_fills_the_panel_spot():
    import bus_client
    container = _render_cold()
    assert _texts(container, "entry-spot") == ["—"]
    bus_client.bus().cache_set("cache:options:sim_meta", _SIM_META)
    _fire(container, "_poll_meta")
    assert _texts(container, "entry-spot") == ["450.00"]


# ── every expiration listed, strikes on demand (2026-09-12) ─────────────────
_ALL_EXPS = ["2026-06-26", "2026-07-03", "2026-07-17"]


def test_the_simulator_asks_for_a_lazy_fetch():
    import bus_client
    container = _render_cold()
    symbol = _hooked(container, "entry-ticker")[0]
    symbol.value = "TSLA"
    for listener in list(symbol._event_listeners.values()):
        if listener.type == "keydown.enter":
            listener.handler(None)
    cmd = _last_command("sim_fetch")
    assert cmd["args"]["symbol"] == "TSLA" and cmd["args"]["lazy"] is True
    assert isinstance(cmd["args"]["expiries"], list)


def test_an_unloaded_expiry_is_fetched_then_every_leg_moves_there():
    import asyncio

    import bus_client
    container = _render_cold()
    meta = dict(_SIM_META, expirations=_ALL_EXPS)
    bus_client.bus().cache_set("cache:options:sim_meta", meta)
    bus_client.bus().cache_set("cache:options:sim_chain", _sim_chain())
    _fire(container, "_poll_meta")
    _run_async(container, "_poll_chain", asyncio)

    pills = _hooked(container, "entry-expiry")
    assert [p.text.split(" · ")[0] for p in pills] == ["Jun 26", "Jul 3", "Jul 17"]
    _fire_click_on(pills[2])
    req = _last_command("sim_fetch_expiry")
    assert req["args"] == {"symbol": "SPY", "expiry": "2026-07-17"}

    far = dict(meta, added="2026-07-17",
               expiries=["2026-06-26", "2026-07-03", "2026-07-17"],
               strikes=dict(meta["strikes"], **{"2026-07-17": {"call": [445.0, 450.0, 455.0],
                                                             "put": [445.0, 450.0, 455.0]}}))
    bus_client.bus().cache_set("cache:options:sim_meta", far)
    _fire(container, "_poll_meta")
    legs = _last_command("sim_run")["args"]["legs"]
    assert {l["expiry"] for l in legs} == {"2026-07-17"}
    assert len(_leg_rows(container)) == 2          # moved, not re-seeded or dropped


# ── one position shared with the Calculator (2026-09-12) ────────────────────

def test_the_copy_to_calculator_button_is_gone():
    labels = [b.text for b in _sim_buttons(_render_cold())]
    assert "Copy to Calculator" not in labels


def test_the_simulator_opens_with_the_shared_symbol_and_legs():
    import bus_client
    from nicegui import ui
    from pages.options import shared_position
    bus_client.reset()
    sim._LAST_SIM.clear()
    meta = dict(_future_meta(), symbol="TSLA")
    exp = meta["expiries"][0]
    legs = [{"option_type": "call", "side": "short", "strike": 455.0, "expiry": exp,
             "qty": 2, "premium": 3.1},
            {"option_type": "call", "side": "long", "strike": 460.0, "expiry": exp,
             "qty": 2, "premium": 1.2}]
    shared_position.publish("TSLA", "CCS", legs, exp)
    with ui.card() as container:
        sim.render()
    fetch = _last_command("sim_fetch")
    assert fetch["args"]["symbol"] == "TSLA" and exp in fetch["args"]["expiries"]
    bus_client.bus().cache_set("cache:options:sim_meta", meta)
    _fire(container, "_poll_meta")
    run = _last_command("sim_run")["args"]
    assert run["symbol"] == "TSLA"
    assert sorted((l["strike"], l["qty"]) for l in run["legs"]) == [(455.0, 2), (460.0, 2)]


def test_a_simulator_edit_is_published_for_the_calculator():
    import bus_client
    from pages.options import shared_position
    container = _render_cold()
    bus_client.bus().cache_set("cache:options:sim_meta", _future_meta())
    _fire(container, "_poll_meta")
    _fire_click_on(_hooked(container, "leg-side")[0])            # flip leg 1
    pos = shared_position.current()
    assert pos["symbol"] == "SPY"
    assert pos["legs"][0]["side"] == "long"


def test_share_legs_are_carried_through_not_simulated_and_never_dropped():
    import bus_client
    from nicegui import ui
    from pages.options import shared_position
    bus_client.reset()
    sim._LAST_SIM.clear()
    meta = _future_meta()
    exp = meta["expiries"][0]
    stock = {"option_type": "stock", "side": "long", "strike": None, "expiry": None,
             "qty": 1, "premium": 450.0}
    call = {"option_type": "call", "side": "short", "strike": 455.0, "expiry": exp,
            "qty": 1, "premium": 2.0}
    shared_position.publish("SPY", "COVERED_CALL", [stock, call], exp)
    with ui.card() as container:
        sim.render()
    bus_client.bus().cache_set("cache:options:sim_meta", meta)
    _fire(container, "_poll_meta")
    assert len(_leg_rows(container)) == 1                          # the call only
    assert [t for t in _texts(container, "sim-warning") if "share" in t]
    _fire_click_on(_hooked(container, "leg-strike-up")[0])         # an edit here…
    pos = shared_position.current()
    assert stock in pos["legs"], "the Calculator's shares were dropped"
    assert pos["strategy"] == "COVERED_CALL"
    assert any(l["option_type"] == "call" and l["strike"] == 460.0 for l in pos["legs"])


# ── the view tabs: named for what you change, what-if first (2026-09-12) ─────

def _tab_labels(container):
    from nicegui import ui
    return [t._props.get("label") or t._props.get("name")
            for t in container.descendants() if isinstance(t, ui.tab)]


def test_the_view_tabs_are_price_and_time_then_volatility_then_history():
    from nicegui import ui
    container = _render_cold()
    names = [t._props.get("name") for t in container.descendants() if isinstance(t, ui.tab)]
    assert names == ["Price & Time", "Volatility", "History"]
    tabs = [e for e in container.descendants() if isinstance(e, ui.tabs)][0]
    value = tabs.value
    assert getattr(value, "_props", {}).get("name", value) == "Price & Time"   # the first tab opens


def test_an_old_saved_tab_name_falls_back_to_the_first_tab():
    import bus_client
    from nicegui import ui
    from pages.options import shared_position
    bus_client.reset()
    shared_position.reset()
    sim._LAST_SIM.clear()
    sim._LAST_SIM.update({"symbol": "SPY", "strategy": "PCS", "legs": [], "active_tab": "Replay"})
    with ui.card() as container:
        sim.render()
    tabs = [e for e in container.descendants() if isinstance(e, ui.tabs)][0]
    assert tabs.value == "Price & Time"


def test_a_grid_click_on_a_matching_side_moves_that_leg_instead_of_adding_one():
    import asyncio

    import bus_client
    container = _render_cold()
    bus_client.bus().cache_set("cache:options:sim_meta", _SIM_META)
    bus_client.bus().cache_set("cache:options:sim_chain", _sim_chain())
    _fire(container, "_poll_meta")
    _run_async(container, "_poll_chain", asyncio)
    before = _last_command("sim_run")["args"]["legs"]
    assert {l["side"] for l in before if l["kind"] == "put"} == {"short", "long"}
    _click_grid(container, "ask", "put", 445.0)           # moves the long put
    assert len(_leg_rows(container)) == 2
    legs = _last_command("sim_run")["args"]["legs"]
    assert [l["strike"] for l in legs if l["side"] == "long"] == [445.0]


# ── the page kit, and the charts that mounted hidden (2026-09-20) ───────────
# The frame is ``pages/ui_kit.py``'s now: one header line with the snapshot's
# Updated stamp, the app surface, and the app's one empty line. The page keeps
# its layout, its three tab panels IN THEIR BUILD ORDER, its payoff palette and
# every ``sim-*`` hook.

def _module_src():
    import pathlib
    return pathlib.Path(sim.__file__).read_text(encoding="utf-8")


def _code_src():
    """The page's CODE with its comments dropped, so a name mentioned in a
    comment that explains why it is GONE does not read as a use of it."""
    import ast
    return ast.unparse(ast.parse(_module_src()))


def _reflow_timers(container):
    """The once-timers a reveal queues (``_queue_reflow``)."""
    from nicegui import ui
    return [e for e in container.descendants()
            if isinstance(e, ui.timer)
            and getattr(e.callback, "__name__", "") == "_reflow_charts"]


def _charts(container):
    """Replay first, What-if second — the BUILD order of the tab panels, which
    the strip deliberately reverses. Do not reorder the panels."""
    from nicegui import ui
    return [e for e in container.descendants() if isinstance(e, ui.highchart)]


def test_the_simulator_frame_is_the_kit_and_carries_no_surface_of_its_own():
    """``stale=False`` is the decision worth pinning: all four ``sim_*`` views are
    request/response, published only by a command handler, so nothing is due and
    an age can never mean 'behind'."""
    import inspect
    src = inspect.getsource(sim.render)
    assert "kit.page()" in src
    assert 'kit.header("Simulator", view="options:sim_meta", stale=False)' in src
    # The page-scoped escape hatch and its scope hook go: ``APP_FIELD_CSS`` is
    # the identical block under ``.ns-app``, injected app-wide by BOTH
    # entrypoints, so a per-page copy is a second one free to drift.
    code = _code_src()
    for token in ("QUASAR_INTERNAL_CSS", "calc-v2", "ui.add_css(", "PAGE"):
        assert token not in code, f"{token} is a surface of the page's own"


def test_an_empty_symbol_is_reported_under_the_field_not_in_a_toast():
    """Validation, not an outcome — so it belongs where the reader is looking.
    ``kit.symbol_error`` also forgets the load dedup, so the same ticker can be
    retried from the field itself."""
    container = _render_cold()
    symbol = _hooked(container, "entry-ticker")[0]
    symbol.value = ""
    load = next(b for b in _sim_buttons(container) if b.text == "Load")
    _fire_click_on(load)
    assert symbol.error == "Enter a symbol first."
    assert "ui.notify" not in _module_src()


def test_a_named_symbol_clears_the_message_the_empty_one_left():
    container = _render_cold()
    symbol = _hooked(container, "entry-ticker")[0]
    load = next(b for b in _sim_buttons(container) if b.text == "Load")
    symbol.value = ""
    _fire_click_on(load)
    assert symbol.error
    symbol.value = "TSLA"
    _fire_click_on(load)
    assert symbol.error is None


def test_the_lookback_picker_is_a_labelled_field_not_a_floating_label():
    """The standard's field rule: the label sits ABOVE the control, never
    floating inside it and never as a placeholder."""
    from nicegui import ui
    container = _render_cold()
    sel = next(e for e in container.descendants()
               if isinstance(e, ui.select) and "w-44" in e._classes)
    assert sel._props.get("label") is None, "the look-back label still floats"
    assert "Look-back" in _labels(container)
    assert set(sel.options) == set(sim.lookback_options())


def test_the_three_empty_states_are_the_apps_one_empty_line():
    from pages import ui_kit as kit
    container = _render_cold()
    for hook in ("sim-replay-empty", "sim-whatif-empty", "sim-shock-empty"):
        el = next(e for e in container.descendants() if hook in e._classes)
        for cls in kit.EMPTY.split():
            assert cls in el._classes, f"{hook} is missing {cls}"
        assert "opacity-70" not in el._classes


def test_a_first_result_reflows_the_whatif_chart_it_just_revealed(monkeypatch):
    """THE BUG. A ``ui.highchart`` measures its container ONCE at mount and
    NiceGUI's element has no ResizeObserver, so a chart that mounted hidden
    renders ~600px wide for the rest of the session. ``_reflow_charts`` fired
    only on a TAB CLICK — but the path a cold page actually takes is the
    ``set_visibility(True)`` in ``_render_figures`` when its first result lands,
    and that called nothing at all."""
    import bus_client
    from nicegui import ui

    container = _render_cold()
    assert not _reflow_timers(container), \
        "a cold page has revealed nothing, so it must queue no reflow"
    bus_client.bus().cache_set("cache:options:sim_meta", _future_meta())
    _fire(container, "_poll_meta")
    legs = _last_command("sim_run")["args"]["legs"]
    bus_client.bus().cache_set("cache:options:sim_result", {
        "spot": 450.0, "symbol": "SPY", "legs": legs, "dt": 5.0, "mult": 1.5,
        "whatif_rows": [{"S": 400.0, "theo_price": -500.0},
                        {"S": 500.0, "theo_price": 0.0}],
        "whatif_baseline": -120.0, "ivshock": None})
    _fire(container, "_poll_result")

    whatif = _charts(container)[1]
    assert whatif.visible, "the What-if chart should be on screen"
    timers = _reflow_timers(container)
    assert timers, "the chart was revealed and nothing asked it to reflow"
    js = []
    monkeypatch.setattr(ui, "run_javascript", lambda code, *a, **k: js.append(code))
    timers[-1].callback()
    assert f"getElement({whatif.id})" in " ".join(js)


def test_a_first_replay_trace_reflows_the_chart_it_just_revealed(monkeypatch):
    """The History panel is the worse half of the same bug: it mounts inside an
    INACTIVE tab panel, so it has measured 0×0 before any trace arrives."""
    import bus_client
    from nicegui import ui

    container = _render_cold()
    bus_client.bus().cache_set("cache:options:sim_meta", _future_meta())
    _fire(container, "_poll_meta")
    assert not _reflow_timers(container)
    n = 6
    bus_client.bus().cache_set("cache:options:sim_replay", {
        "x": list(range(n)), "prices": [450.0 + i for i in range(n)],
        "timestamps": [f"2026-08-24T09:{30 + i:02d}:00" for i in range(n)],
        "pnl": [float(i) for i in range(n)], "value": [float(i) for i in range(n)],
        "greeks": {"delta": [50.0] * n}, "sessions": [{"start": 0, "end": n}],
        "units": "position", "resolution": "6 bars"})
    _fire(container, "_poll_replay")

    replay = _charts(container)[0]
    assert replay.visible
    timers = _reflow_timers(container)
    assert timers, "the Replay chart was revealed and nothing reflowed it"
    js = []
    monkeypatch.setattr(ui, "run_javascript", lambda code, *a, **k: js.append(code))
    timers[-1].callback()
    assert f"getElement({replay.id})" in " ".join(js)


def test_a_repaint_of_an_already_visible_chart_queues_no_second_reflow():
    """The reflow is gated on the hidden→visible TRANSITION, and that gate is
    the point: ``_render_figures`` runs on every slider step, so an ungated
    reflow would queue a timer per step of a drag."""
    import bus_client
    container = _render_cold()
    bus_client.bus().cache_set("cache:options:sim_meta", _future_meta())
    _fire(container, "_poll_meta")
    legs = _last_command("sim_run")["args"]["legs"]
    result = {"spot": 450.0, "symbol": "SPY", "legs": legs, "dt": 5.0, "mult": 1.5,
              "whatif_rows": [{"S": 400.0, "theo_price": -500.0},
                              {"S": 500.0, "theo_price": 0.0}],
              "whatif_baseline": -120.0, "ivshock": None}
    bus_client.bus().cache_set("cache:options:sim_result", result)
    _fire(container, "_poll_result")
    first = len(_reflow_timers(container))
    assert first == 1

    bus_client.bus().cache_set("cache:options:sim_result", dict(result, dt=6.0))
    _fire(container, "_poll_result")
    assert len(_reflow_timers(container)) == first, \
        "a repaint of a chart already on screen queued another reflow"
