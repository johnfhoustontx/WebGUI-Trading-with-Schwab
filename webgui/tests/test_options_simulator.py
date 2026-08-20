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


def test_ivshock_figure_two_series():
    base = {"theo_price": 1.0, "delta": 0.5, "gamma": 0.02, "theta": -0.1, "vega": 0.3}
    shock = {"theo_price": 1.6, "delta": 0.55, "gamma": 0.018, "theta": -0.12, "vega": 0.45}
    fig = sim.ivshock_figure(base, shock, mult=1.5)
    assert len(fig["series"]) == 2 and fig["chart"]["type"] == "column"


def test_whatif_and_ivshock_figures_set_explicit_height():
    # These charts mount inside INACTIVE tab panels (default tab is Replay). NiceGUI's
    # highchart only reflows once at mount and never re-measures, so a chart that
    # mounts in a 0-height (display:none) panel must carry an explicit height or it
    # collapses to title-height when the tab is finally shown (the IV-shock bug).
    wf = sim.whatif_figure([{"S": 1, "theo_price": 2}], spot=1.0)
    iv = sim.ivshock_figure({"theo_price": 1.0}, {"theo_price": 1.2}, mult=1.5)
    assert isinstance(wf["chart"].get("height"), (int, float)) and wf["chart"]["height"] >= 300
    assert isinstance(iv["chart"].get("height"), (int, float)) and iv["chart"]["height"] >= 300


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
    fig = sim.replay_figure(trace, cursor=1)
    # 6 stacked series (price + 5 greeks) over 6 stacked yAxes.
    assert len(fig["series"]) == 6
    assert len(fig["yAxis"]) == 6
    # Each greek series points at its own yAxis index.
    assert [s["yAxis"] for s in fig["series"]] == [0, 1, 2, 3, 4, 5]
    assert fig["series"][0]["data"] == [[0, 450.0], [1, 451.0]]
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
            "active_tab": "What-if", "junk": 1}
    snap = ps.snapshot(vals, sim._SIM_KEYS)
    assert "junk" not in snap and snap["dt"] == 7.0
    assert ps.merge_restore(snap, sim._SIM_DEFAULTS)["symbol"] == "AAPL"


# -- the leg editor's CARD layout ---------------------------------------------
# The Simulator mounts the SHARED leg editor in ``layout="card"`` while keeping
# the app-wide dark navy — the near-black CALC_* language belongs to the
# Calculator alone. Everything below asserts against the MOUNTED element tree
# rather than the call site's source: a source grep passes on a call that never
# renders, and it cannot see which palette actually reached the DOM.

_SIM_META = {
    "symbol": "SPY", "spot": 450.0, "n_contracts": 12,
    "expiries": ["2026-06-26", "2026-07-03"],
    "strikes": {e: {"call": [445.0, 450.0, 455.0], "put": [445.0, 450.0, 455.0]}
                for e in ("2026-06-26", "2026-07-03")},
}


def _sim_container():
    """Render the Simulator over a warm sim_meta; return the mount container."""
    import bus_client
    from nicegui import ui

    bus_client.reset()
    bus_client.bus().cache_set("cache:options:sim_meta", _SIM_META)
    with ui.card() as container:
        sim.render()
    return container


def _leg_cards(container):
    return [e for e in container.descendants() if "leg-card" in e._classes]


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


def test_simulator_mounts_the_leg_editor_as_cards_not_rows():
    """The page really renders the card layout — the ``leg-card`` hook is present
    and neither row-mode artefact (the ``leg-row`` line, the ``leg-head`` header)
    survives. Seeded PCS = two legs, so two cards."""
    container = _sim_container()
    assert len(_leg_cards(container)) == 2
    assert not [e for e in container.descendants() if "leg-row" in e._classes]
    assert not [e for e in container.descendants() if "leg-head" in e._classes]


def test_simulator_cards_carry_the_eyebrow_captions():
    """The card's own captions replace the row header the page used to pass."""
    card = _leg_cards(_sim_container())[0]
    txt = _labels(card)
    for cap in ("TYPE", "SIDE", "EXPIRY", "STRIKE", "QTY", "DELTA"):
        assert cap in txt, cap


def test_simulator_keeps_the_default_navy_card_palette():
    """No ``tokens`` — the Simulator stays app-wide dark navy. This is the
    regression that would otherwise be invisible: a later Calculator restyle
    must not repaint this page, so the assertion is against
    ``DEFAULT_CARD_TOKENS`` (and the absence of the CALC_* language), not
    against whatever the Calculator happens to use today."""
    from pages.options import leg_editor as LE
    from pages.options import theme

    cards = _leg_cards(_sim_container())
    for tok in ("frame", "accent_short"):        # PCS leg 1 is the short put
        for cls in LE.DEFAULT_CARD_TOKENS[tok].split():
            assert cls in cards[0]._classes, cls
    for cls in LE.DEFAULT_CARD_TOKENS["accent_long"].split():
        assert cls in cards[1]._classes, cls     # leg 2 is the long put
    # Nothing on the card may come from the Calculator's vocabulary that is not
    # ALREADY part of the shared default. Subtracting the defaults (rather than
    # hand-listing the overlap) keeps this honest when the two languages happen
    # to agree — ``CALC_EDGE_POS`` and the navy ``accent_short`` are the same
    # green today, and that coincidence is not a palette leak.
    shared = {c for v in LE.DEFAULT_CARD_TOKENS.values() for c in v.split()}
    calc = {c for name in dir(theme) if name.startswith("CALC_")
            for c in str(getattr(theme, name)).split()
            if c.startswith(("bg-[", "text-[", "border-[", "border-l-["))}
    on_card = {c for card in cards for c in card._classes}
    assert calc, "no CALC_* palette found to compare against"
    for cls in calc - shared:
        assert cls not in on_card, cls


def test_simulator_cards_have_no_premium_cell():
    """``show_premium=False`` survives the switch — the simulator prices each leg
    off the chain's IV, so a manual premium input would be a lie. The PREMIUM
    track is COLLAPSED, not left as a hole, so DELTA keeps its own caption."""
    from nicegui import ui
    from pages.options import leg_editor as LE

    card = _leg_cards(_sim_container())[0]
    assert "PREMIUM" not in _labels(card)
    assert len([e for e in card.descendants() if isinstance(e, ui.number)]) == 1  # qty only
    grids = [c for e in card.descendants() for c in e._classes if c.startswith("grid-cols-")]
    no_prem = [c for c in LE._CARD_ROW2_COLS_NO_PREMIUM.split() if c.startswith("grid-cols-")][0]
    full = [c for c in LE._CARD_ROW2_COLS.split() if c.startswith("grid-cols-")][0]
    assert no_prem in grids and full not in grids


def test_simulator_delta_reads_an_em_dash_with_no_chain_greeks():
    """No ``delta_for``: ``sim_meta`` carries spot/expiries/strikes and no greeks,
    so the card must show an em-dash — never a confident 0.00."""
    card = _leg_cards(_sim_container())[0]
    txt = _labels(card)
    assert "\u2014" in txt
    assert "+0.00" not in txt and "-0.00" not in txt


def test_simulator_floors_the_leg_count_at_one():
    """``min_legs`` is left at the default 1. Both removes are live at the seeded
    two legs (today's behaviour); the LAST leg locks — a zero-leg simulator
    enqueues nothing (``_current_params`` returns None) and silently freezes the
    charts on a stale sweep, which is worse than a disabled ✕ with a tooltip."""
    container = _sim_container()
    removes = [b for b in _sim_buttons(container) if "leg-remove" in b._classes]
    assert len(removes) == 2 and all(b.enabled for b in removes)
    _fire_click(removes[0])
    left = [b for b in _sim_buttons(container) if "leg-remove" in b._classes]
    assert len(left) == 1 and not left[0].enabled


def test_simulator_card_footer_offers_add_leg_and_no_reset():
    """``on_reset`` stays None: the strategy picker already re-seeds the template
    on every pick, so a RESET TO TEMPLATE button would be a second control for
    the same act — a Calculator affordance, not one this page asked for."""
    labels = [b.text for b in _sim_buttons(_sim_container())]
    assert "ADD LEG" in labels
    assert "RESET TO TEMPLATE" not in labels
