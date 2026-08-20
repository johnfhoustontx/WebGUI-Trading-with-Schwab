"""Tests for the Calculator pure transforms (banding, grid mapping, formatting)."""
import datetime as dt

from pages.options import calculator as calc


def test_pnl_cell_class_neutral_for_zero():
    assert calc.pnl_cell_class(0, 100, -100) == "neutral"
    assert calc.pnl_cell_class(None, 100, -100) == "neutral"


def test_pnl_cell_class_profit_bands():
    assert calc.pnl_cell_class(100, 100, -100) == "p5"
    assert calc.pnl_cell_class(10, 100, -100) == "p1"
    assert calc.pnl_cell_class(50, 100, -100) == "p3"


def test_pnl_cell_class_loss_bands():
    assert calc.pnl_cell_class(-100, 100, -100) == "l5"
    assert calc.pnl_cell_class(-10, 100, -100) == "l1"


def test_grid_rows_shapes_price_and_cell_pairs():
    data = [{"price": 450.0, "pnl": [10, -5], "pnl_pct": [2.0, -1.0]}]
    rows = calc.grid_rows(data)
    assert rows[0]["price"] == 450.0
    assert len(rows[0]["cells"]) == 2
    assert rows[0]["cells"][0]["pnl"] == 10
    assert rows[0]["cells"][1]["pnl_pct"] == -1.0


def test_grid_extremes():
    data = [{"price": 1, "pnl": [10, -5], "pnl_pct": [0, 0]},
            {"price": 2, "pnl": [3, -20], "pnl_pct": [0, 0]}]
    g_max, g_min = calc.grid_extremes(data)
    assert g_max == 10 and g_min == -20


def test_eval_date_labels():
    dates = [dt.date(2026, 1, 15), dt.date(2026, 2, 28)]
    assert calc.eval_date_labels(dates) == ["01/15", "02/28"]


def test_formatters():
    assert calc.fmt_dollar(1234) == "+1,234"
    assert calc.fmt_dollar(-5) == "-5"
    assert calc.fmt_pct(12.34) == "+12.3%"
    assert calc.fmt_dollar(None) == "—"


def test_extract_atm_iv_picks_closest_strike():
    chain = {"callExpDateMap": {"2026-06-19:4": {
        "445.0": [{"volatility": 20.0}],
        "450.0": [{"volatility": 18.5}],
        "455.0": [{"volatility": 22.0}],
    }}}
    assert calc.extract_atm_iv(chain, 450.0) == 18.5


def test_extract_atm_iv_normalizes_decimal_vol():
    chain = {"callExpDateMap": {"e": {"450.0": [{"volatility": 0.185}]}}}
    assert abs(calc.extract_atm_iv(chain, 450.0) - 18.5) < 1e-6


def test_extract_atm_iv_none_when_empty():
    assert calc.extract_atm_iv({}, 450.0) is None
    assert calc.extract_atm_iv(None, 450.0) is None


def test_extract_atm_iv_filters_by_expiry():
    chain = {"callExpDateMap": {
        "2026-06-15:1": {"742.0": [{"volatility": 80.0}]},
        "2026-06-19:5": {"742.0": [{"volatility": 18.0}]},
    }}
    assert calc.extract_atm_iv(chain, 742.0, expiry="2026-06-19") == 18.0


CHAIN_PREM = {
    "callExpDateMap": {"2026-06-18:4": {"450.0": [{"mark": 1.25, "bid": 1.2, "ask": 1.3}]}},
    "putExpDateMap": {"2026-06-18:4": {"445.0": [{"mark": 0, "bid": 0.06, "ask": 0.10}]}},
}


def test_extract_premium_uses_mark():
    assert calc.extract_premium(CHAIN_PREM, "call", 450.0) == 1.25


def test_extract_premium_falls_back_to_mid():
    assert abs(calc.extract_premium(CHAIN_PREM, "put", 445.0) - 0.08) < 1e-9


def test_extract_premium_strike_tolerance():
    assert calc.extract_premium(CHAIN_PREM, "call", 450.3) == 1.25


def test_extract_premium_none_when_missing():
    assert calc.extract_premium(CHAIN_PREM, "call", 999) is None


def test_extract_premium_expiry_filter():
    assert calc.extract_premium(CHAIN_PREM, "call", 450.0, expiry="2026-06-18") == 1.25
    assert calc.extract_premium(CHAIN_PREM, "call", 450.0, expiry="2026-06-19") is None


CHAIN_SEL = {
    "callExpDateMap": {"2026-06-18:4": {"450.0": [{}], "455.0": [{}]}},
    "putExpDateMap": {"2026-06-18:4": {"445.0": [{}]}, "2026-06-22:8": {"440.0": [{}]}},
}


def test_chain_expiries_sorted_unique():
    assert calc.chain_expiries(CHAIN_SEL) == ["2026-06-18", "2026-06-22"]
    assert calc.chain_expiries({}) == []


def test_chain_strikes_by_expiry_and_kind():
    assert calc.chain_strikes(CHAIN_SEL, "2026-06-18", "call") == [450.0, 455.0]
    assert calc.chain_strikes(CHAIN_SEL, "2026-06-18", "put") == [445.0]
    assert calc.chain_strikes(CHAIN_SEL, "2026-06-22", "put") == [440.0]
    assert calc.chain_strikes(CHAIN_SEL, "2026-06-18", "put") != [440.0]


def test_api_symbol_maps_spx():
    assert calc.api_symbol("SPX") == "$SPX"
    assert calc.api_symbol("spy") == "SPY"


def test_strategy_options_cover_strategies():
    """The strategy dropdown (now sourced from the shared ``strategies`` module's
    groups) exposes the core codes; the templates carry the right leg counts."""
    from pages.options import strategies as S

    opts = calc.strategy_options()
    for strat in ("PCS", "CCS", "IC", "LONG_PUT", "NAKED_CALL"):
        assert strat in opts
    # New multi-leg strategies are now offered too (butterflies / calendars).
    assert "BUTTERFLY_CALL" in opts and "CALENDAR_CALL" in opts
    assert len(S.STRATEGY_TEMPLATES["IC"]) == 4
    assert len(S.STRATEGY_TEMPLATES["PCS"]) == 2


def test_summary_strategy_routes_and_imports_strategies():
    """do_calc routes the summary strategy code through this helper. Regression:
    the helper must import ``strategies`` itself — the only ``strategies`` alias on
    the page lived inside ``strategy_options``, so an inline reference in do_calc
    raised ``NameError: name 'S' is not defined`` on Calculate."""
    from pages.options import strategies as S

    strikes = [90, 95, 100, 105, 110]
    pcs = S.build_default_legs("PCS", 100, strikes, ["2026-07-17"])
    assert calc._summary_strategy("PCS", pcs, dirty=False) == "PCS"     # clean canonical
    assert calc._summary_strategy("PCS", pcs, dirty=True) == "CUSTOM"   # edited -> generic
    condor = S.build_default_legs("CONDOR_CALL", 100, strikes, ["2026-07-17"])
    assert calc._summary_strategy("CONDOR_CALL", condor, dirty=False) == "CUSTOM"


# ── Tier-3 migration regression (Task 2.6h) ──────────────────────────────────
def test_calculator_holds_no_engine_imports():
    """The chain-fetch + options_calculator math moved to the options service;
    the page must no longer import the proxy or any options-scanner engine."""
    import inspect

    src = inspect.getsource(calc)
    for forbidden in ("scanner_engine", "options_calculator", "import proxy",
                      "OPTIONS_SCANNER", "_ensure_engine_path"):
        assert forbidden not in src, f"calculator.py still references {forbidden!r}"


def test_render_grid_takes_preformatted_labels():
    """``_render_grid`` now consumes pre-formatted MM/DD label STRINGS (no date
    objects); a graceful-empty grid renders a 'No P&L data.' note."""
    import types

    captured = {"html": None, "label": None}

    class _FakeBox:
        def clear(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake_ui = types.SimpleNamespace(
        html=lambda h: (captured.__setitem__("html", h),
                        types.SimpleNamespace(classes=lambda *a: None))[1],
        label=lambda t: (captured.__setitem__("label", t),
                         types.SimpleNamespace(classes=lambda *a: None))[1],
        timer=lambda *a, **k: None,            # spot-row centering (no-op in test)
        run_javascript=lambda *a, **k: None)

    import sys
    real_nicegui = sys.modules.get("nicegui")
    sys.modules["nicegui"] = types.SimpleNamespace(ui=fake_ui)
    try:
        # Empty grid -> "No P&L data." note (no exception).
        calc._render_grid(_FakeBox(), ["06/18", "06/19"], [], 450.0)
        assert captured["label"] == "No P&L data."

        # Populated grid -> the pre-formatted labels appear verbatim in the HTML.
        pnl = [{"price": 450.0, "pnl": [10, -5], "pnl_pct": [2.0, -1.0]}]
        calc._render_grid(_FakeBox(), ["06/18", "06/19"], pnl, 450.0)
        assert "06/18 $" in captured["html"] and "06/19 $" in captured["html"]
    finally:
        if real_nicegui is not None:
            sys.modules["nicegui"] = real_nicegui
        else:
            del sys.modules["nicegui"]


def test_render_callable():
    assert callable(calc.render)


def test_big_calc_chain_read_is_off_loop():
    """Perf regression (P6): the ~10 MB cache:options:calc_chain (full ~7000-
    contract chain) must be read OFF the event loop via run.io_bound, while the
    cheap :ver probe stays ON the loop.

    Guards: the version compare still uses read_version (the tiny :ver counter,
    never wrapped), the big GET+parse goes through run.io_bound, _poll_chain became
    an async guard_async coroutine, version-gating is preserved (only fetch on a
    version change), and an in-flight guard stops a slow read stacking across the
    1 s poll ticks. calc_result / calc_iv are small and stay inline."""
    import inspect

    src = inspect.getsource(calc.render)
    assert 'run.io_bound(bus_client.read, "options:calc_chain")' in src
    assert 'read_version("options:calc_chain")' in src   # cheap probe NOT wrapped
    assert "async def _poll_chain" in src
    assert "guard_async" in src
    assert 'version == state["chain_ver"]' in src        # version-gate preserved
    assert 'state.get("chain_fetching")' in src          # re-entrancy guard
    # The small result/iv reads stay inline (not moved off-loop).
    assert 'bus_client.read("options:calc_result")' in src
    assert 'bus_client.read("options:calc_iv")' in src


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty (options
    service cold) — the Tier-3 graceful-empty path. Mirrors the swing/simulator
    page tests: rendering inside a slot context exercises the widget wiring + the
    initial fetch-free paint, including mounting the shared leg-editor (PCS default
    template) against an empty chain (no strikes/expiries yet)."""
    import bus_client
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:calc_chain") is None
    assert bus_client.read("options:calc_result") is None
    with ui.card():
        calc.render()  # must not raise


def test_strikes_window_n_either_side_of_spot():
    xs = list(range(1, 101))          # strikes 1..100
    # 3 strikes ≤ spot (48,49,50) + 3 strikes > spot (51,52,53).
    assert calc.strikes_window(xs, 50.4, 3) == [48.0, 49.0, 50.0, 51.0, 52.0, 53.0]
    assert calc.strikes_window(xs, 50.0, 2) == [49.0, 50.0, 51.0, 52.0]


def test_strikes_window_edges_and_empty():
    xs = list(range(1, 101))
    assert calc.strikes_window([], 50, 5) == []
    assert calc.strikes_window(xs, None, 5) == []
    assert calc.strikes_window(xs, 0, 3) == [1.0, 2.0, 3.0]       # spot below all → first n above
    assert calc.strikes_window(xs, 999, 2) == [99.0, 100.0]       # spot above all → last n below


def test_strikes_window_dedups_and_ignores_junk():
    assert calc.strikes_window([100, 100, 105, None, "x", 95], 100, 5) == [95.0, 100.0, 105.0]


def test_calc_capture_keys_cover_inputs():
    # Guard against forgetting to persist a Calculator input across navigation.
    assert set(calc._CALC_KEYS) == {
        "symbol", "strategy", "legs", "iv", "rate", "ivadj", "contracts",
        "price", "num_strikes", "expiry"}


def test_calc_snapshot_roundtrips_via_page_state():
    from pages.options import page_state as ps
    snap = ps.snapshot({"symbol": "MSFT", "strategy": "CCS", "legs": [], "iv": 25.0,
                        "rate": 4.5, "ivadj": 0.0, "contracts": 3, "price": 410.0,
                        "num_strikes": 30, "expiry": "2026-07-17", "junk": 1}, calc._CALC_KEYS)
    assert "junk" not in snap
    assert ps.merge_restore(snap, calc._CALC_DEFAULTS)["num_strikes"] == 30


def test_tile_color_class_maps_palette():
    from pages.options import theme
    assert calc.tile_color_class("#66bb6a") == theme.TXT_POS
    assert calc.tile_color_class("#ef5350") == theme.TXT_NEG
    assert calc.tile_color_class("#bdbdbd") == theme.TXT_NEUTRAL


# ── the redesign's page-side readouts ────────────────────────────────────────
# (the module is bound as `calc` throughout this file; the plan writes it as `C`)

def _chain(delta=-0.31):
    return {"putExpDateMap": {"2026-08-21:2": {"660.0": [
        {"mark": 2.4, "delta": delta, "volatility": 14.2}]}}}


def test_extract_delta_reads_the_chains_own_delta():
    assert calc.extract_delta(_chain(), "put", 660.0, "2026-08-21") == -0.31


def test_extract_delta_is_none_when_the_contract_has_no_delta():
    # Index chains read hollow outside regular hours. A missing delta must
    # render as an em-dash, NOT as 0.00 — a confident wrong number on a row
    # that otherwise looks live.
    chain = {"putExpDateMap": {"2026-08-21:2": {"660.0": [{"mark": 2.4}]}}}
    assert calc.extract_delta(chain, "put", 660.0, "2026-08-21") is None


def test_extract_delta_rejects_the_chains_missing_greek_sentinel():
    # Schwab sends -999.0 for a greek it does not have; options_svc.flow_alerts
    # already drops |delta| > 1 for exactly that reason. Rendering it would put
    # "-999.00" in the DELTA column of an otherwise live-looking row.
    assert calc.extract_delta(_chain(-999.0), "put", 660.0, None) is None
    assert calc.extract_delta(_chain(1.5), "put", 660.0, None) is None
    assert calc.extract_delta(_chain(float("nan")), "put", 660.0, None) is None


def test_extract_delta_keeps_a_legitimate_zero():
    # 0.00 IS a reading for a far-out-of-the-money contract. Only a MISSING
    # delta degrades to the em-dash.
    assert calc.extract_delta(_chain(0.0), "put", 660.0, None) == 0.0


def test_extract_delta_honours_the_expiry_filter():
    assert calc.extract_delta(_chain(), "put", 660.0, "2026-09-18") is None


def test_extract_delta_is_none_for_an_absent_strike():
    assert calc.extract_delta(_chain(), "put", 999.0, "2026-08-21") is None


def test_extract_delta_tolerates_junk():
    assert calc.extract_delta(None, "put", 660.0, None) is None
    assert calc.extract_delta({}, "call", None, None) is None


def test_position_delta_flips_sign_for_a_short_leg():
    assert calc.position_delta(-0.31, "long") == -0.31
    assert calc.position_delta(-0.31, "short") == 0.31
    assert calc.position_delta(None, "short") is None


def test_net_premium_is_positive_for_a_net_credit():
    legs = [{"side": "short", "premium": 3.0, "qty": 1},
            {"side": "long", "premium": 1.2, "qty": 1}]
    assert calc.net_premium(legs) == 180.0          # (3.0 - 1.2) * 1 * 100


def test_net_premium_is_negative_for_a_net_debit():
    legs = [{"side": "long", "premium": 3.0, "qty": 2},
            {"side": "short", "premium": 1.0, "qty": 2}]
    assert calc.net_premium(legs) == -400.0


def test_net_premium_is_none_while_a_leg_is_unpriced():
    # build_default_legs sets premium=None until Fetch premiums runs. Counting
    # an unpriced leg as zero would print "NET $0" over a position whose cash
    # is simply not known yet — and "MAX LOSS $0" beside it.
    assert calc.net_premium([{"side": "long"}]) is None
    assert calc.net_premium([{"side": "short", "premium": 3.0, "qty": 1},
                             {"side": "long", "premium": None, "qty": 1}]) is None
    assert calc.net_premium([{"side": "long", "premium": 3.0, "qty": "x"}]) is None


def test_net_premium_is_zero_for_no_legs():
    assert calc.net_premium([]) == 0.0
    assert calc.net_premium(None) == 0.0


def test_max_loss_estimate_for_a_credit_spread_is_width_less_credit():
    legs = [{"option_type": "put", "side": "short", "strike": 660, "premium": 3.0, "qty": 1},
            {"option_type": "put", "side": "long", "strike": 655, "premium": 1.2, "qty": 1}]
    # width 5 * 100 * 1 contract = 500, less the 180 credit
    assert calc.max_loss_estimate(legs) == 320.0


def test_max_loss_estimate_for_a_net_debit_is_the_debit():
    legs = [{"option_type": "call", "side": "long", "strike": 670, "premium": 4.0, "qty": 1}]
    assert calc.max_loss_estimate(legs) == 400.0


def test_max_loss_estimate_for_an_iron_condor_risks_one_side_not_both():
    # Only one wing can be breached, so the risk is the WIDER side less the
    # whole credit — never the two widths added together.
    legs = [{"option_type": "put", "side": "short", "strike": 660, "premium": 3.0, "qty": 1},
            {"option_type": "put", "side": "long", "strike": 655, "premium": 1.2, "qty": 1},
            {"option_type": "call", "side": "short", "strike": 680, "premium": 2.5, "qty": 1},
            {"option_type": "call", "side": "long", "strike": 685, "premium": 1.0, "qty": 1}]
    assert calc.net_premium(legs) == 330.0
    assert calc.max_loss_estimate(legs) == 170.0        # 500 - 330


def test_max_loss_estimate_for_an_iron_butterfly():
    legs = [{"option_type": "put", "side": "short", "strike": 660, "premium": 3.0, "qty": 1},
            {"option_type": "put", "side": "long", "strike": 655, "premium": 1.0, "qty": 1},
            {"option_type": "call", "side": "short", "strike": 660, "premium": 2.5, "qty": 1},
            {"option_type": "call", "side": "long", "strike": 665, "premium": 0.9, "qty": 1}]
    assert calc.net_premium(legs) == 360.0
    assert calc.max_loss_estimate(legs) == 140.0        # 500 - 360


def test_max_loss_estimate_for_a_1_2_1_butterfly_is_the_debit():
    # The middle leg carries qty 2, so any "max(qty)" scaling is wrong here.
    legs = [{"option_type": "call", "side": "long", "strike": 655, "premium": 6.0, "qty": 1},
            {"option_type": "call", "side": "short", "strike": 660, "premium": 3.0, "qty": 2},
            {"option_type": "call", "side": "long", "strike": 665, "premium": 1.2, "qty": 1}]
    assert calc.net_premium(legs) == -120.0
    assert calc.max_loss_estimate(legs) == 120.0


def test_max_loss_estimate_for_a_net_credit_ratio_spread_is_the_naked_leg():
    # The case a strike-width estimate cannot see: two short puts against one
    # long leaves a naked put underneath, so the risk is the uncovered strike
    # down to zero (~$66k), not the 5-wide spread (~$520) the strikes suggest.
    legs = [{"option_type": "put", "side": "short", "strike": 660, "premium": 3.0, "qty": 2},
            {"option_type": "put", "side": "long", "strike": 655, "premium": 1.2, "qty": 1}]
    assert calc.net_premium(legs) == 480.0
    assert calc.max_loss_estimate(legs) == 66020.0     # 2*660*100 - 655*100 - 480


def test_max_loss_estimate_for_a_calendar_is_the_debit():
    legs = [{"option_type": "call", "side": "short", "strike": 660, "premium": 2.0,
             "qty": 1, "expiry": "2026-08-21"},
            {"option_type": "call", "side": "long", "strike": 660, "premium": 3.5,
             "qty": 1, "expiry": "2026-09-18"}]
    assert calc.max_loss_estimate(legs) == 150.0


def test_max_loss_estimate_for_a_naked_put_is_not_a_negative_number():
    # A lone short put is a real, finite, LARGE risk: the strike less the
    # credit. A width-based estimate has no width to work with here and would
    # hand back the negated credit.
    legs = [{"option_type": "put", "side": "short", "strike": 660, "premium": 3.0, "qty": 1}]
    assert calc.max_loss_estimate(legs) == 65700.0


def test_max_loss_estimate_is_none_when_the_upside_is_uncapped():
    naked = [{"option_type": "call", "side": "short", "strike": 680, "premium": 2.5, "qty": 1}]
    assert calc.max_loss_estimate(naked) is None
    ratio = [{"option_type": "call", "side": "short", "strike": 660, "premium": 5.0, "qty": 2},
             {"option_type": "call", "side": "long", "strike": 665, "premium": 3.0, "qty": 1}]
    assert calc.max_loss_estimate(ratio) is None


def test_max_loss_estimate_is_none_when_a_short_outlives_a_long():
    # A reverse calendar: settling every leg at one date would model the short
    # back-month as expired and report the credit as riskless.
    legs = [{"option_type": "call", "side": "long", "strike": 660, "premium": 2.0,
             "qty": 1, "expiry": "2026-08-21"},
            {"option_type": "call", "side": "short", "strike": 660, "premium": 3.5,
             "qty": 1, "expiry": "2026-09-18"}]
    assert calc.max_loss_estimate(legs) is None


def test_max_loss_estimate_is_none_without_prices_or_strikes():
    assert calc.max_loss_estimate([{"option_type": "put", "side": "short",
                                    "strike": 660, "qty": 1}]) is None
    assert calc.max_loss_estimate([{"option_type": "put", "side": "short",
                                    "premium": 3.0, "qty": 1}]) is None
    assert calc.max_loss_estimate([]) == 0.0
    assert calc.max_loss_estimate(None) == 0.0


def test_matrix_pct_of_max_is_a_share_of_max_return():
    assert calc.matrix_pct_of_max(90.0, 180.0) == 50.0
    assert calc.matrix_pct_of_max(-90.0, 180.0) == -50.0


def test_matrix_pct_of_max_is_none_without_a_positive_max():
    # No max return means the ratio has no denominator — render an em-dash,
    # never a 0.0% that reads like a real measurement.
    assert calc.matrix_pct_of_max(90.0, 0) is None
    assert calc.matrix_pct_of_max(90.0, None) is None
    assert calc.matrix_pct_of_max(None, 180.0) is None
    assert calc.matrix_pct_of_max(float("nan"), 180.0) is None
    assert calc.matrix_pct_of_max(90.0, float("inf")) is None


def test_matrix_pct_of_max_refuses_the_unlimited_sentinel():
    # A long call's max_profit comes back as the 999999 placeholder. Dividing by
    # it would paint +0.0% down the entire matrix — a fake measurement on every
    # cell. There is no percentage of an uncapped return.
    assert calc.matrix_pct_of_max(90.0, calc.UNLIMITED) is None


def test_is_unlimited_identifies_only_the_sentinel():
    assert calc.is_unlimited(999999) is True
    assert calc.is_unlimited(999998) is False
    assert calc.is_unlimited(None) is False
    assert calc.is_unlimited(True) is False        # bool is an int subclass


def test_chain_status_facts_reports_the_three_phases():
    idle = calc.chain_status_facts(loading=False, symbol="", chain=None)
    assert idle["label"] == "AWAITING SYMBOL" and idle["hint"] == "NOT LOADED"
    assert idle["state"] == "idle"

    busy = calc.chain_status_facts(loading=True, symbol="SPY", chain=None)
    assert busy["label"] == "LOADING CHAIN" and busy["state"] == "loading"

    live = calc.chain_status_facts(loading=False, symbol="SPY", chain=_chain())
    assert live["label"] == "CHAIN LOADED · SPY"
    assert live["hint"] == "LIVE" and live["state"] == "ready"


def test_chain_status_facts_does_not_claim_ready_on_an_empty_chain():
    # An empty dict is a chain that arrived carrying nothing — that is not
    # "loaded", and saying so would paint the frame green over no data.
    facts = calc.chain_status_facts(loading=False, symbol="SPY", chain={})
    assert facts["state"] == "idle"
