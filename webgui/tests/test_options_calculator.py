"""Tests for the Calculator pure transforms (banding, grid mapping, formatting)."""
import datetime as dt
import re

from pages.options import calculator as calc


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


def test_the_retired_summary_tile_helpers_are_gone():
    # The three-hex summary-tile palette existed only for ``_render_summary``,
    # which the six metric cards replaced outright. Left behind it would be dead
    # code with a test pinning nothing the page renders.
    for dead in ("tile_color_class", "_TILE_COLOR_CLASSES", "_render_summary"):
        assert not hasattr(calc, dead), dead


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


def test_matrix_pct_of_divides_by_whatever_basis_was_resolved():
    assert calc.matrix_pct_of(90.0, 180.0) == 50.0
    assert calc.matrix_pct_of(-90.0, 180.0) == -50.0
    assert calc.matrix_pct_of(500.0, 400.0) == 125.0


def test_matrix_pct_of_is_none_without_a_usable_denominator():
    # No denominator means the ratio has none — render an em-dash, never a 0.0%
    # that reads like a real measurement.
    assert calc.matrix_pct_of(90.0, 0) is None
    assert calc.matrix_pct_of(90.0, None) is None
    assert calc.matrix_pct_of(None, 180.0) is None
    assert calc.matrix_pct_of(float("nan"), 180.0) is None
    assert calc.matrix_pct_of(90.0, float("inf")) is None


def test_usable_denominator_refuses_the_unlimited_sentinel():
    # A long call's max_profit comes back as the 999999 placeholder. Dividing by
    # it would paint +0.0% down the entire matrix — a fake measurement on every
    # cell. There is no percentage of an uncapped return.
    assert calc.usable_denominator(180.0) == 180.0
    assert calc.usable_denominator(calc.UNLIMITED) is None
    assert calc.matrix_pct_of(90.0, calc.UNLIMITED) is None


def test_usable_denominator_refuses_anything_that_is_not_a_positive_figure():
    for junk in (0, -5, None, "", True, float("nan"), float("inf")):
        assert calc.usable_denominator(junk) is None, junk


# ── the % column's basis, resolved once per render ──────────────────────────

def test_matrix_basis_uses_max_return_for_a_credit_spread():
    # A PCS's max_profit IS its entry credit, so this column is numerically
    # identical to the service's old share-of-premium: the change is a no-op for
    # every credit structure, and only ever adds meaning elsewhere.
    b = calc.matrix_basis({"max_profit": 180.0, "max_loss": 320.0,
                           "entry_credit": 180.0})
    assert b == {"kind": "max", "denominator": 180.0, "heading": "% MAX"}
    assert calc.matrix_cell_facts(90.0, b, g_max=180.0, g_min=-320.0)["pct"] == "+50.0%"


def test_matrix_basis_falls_back_to_cost_for_a_long_call():
    # max_profit is the uncapped sentinel, so there is no percentage of max
    # return — but the position was BOUGHT, and "+125% of cost" is exactly what
    # the old column showed. An em-dash on every cell would be a regression.
    b = calc.matrix_basis({"max_profit": calc.UNLIMITED, "max_loss": 400.0,
                           "entry_credit": -400.0})
    assert b == {"kind": "cost", "denominator": 400.0, "heading": "% COST"}
    assert calc.matrix_cell_facts(500.0, b, g_max=500.0, g_min=-400.0)["pct"] == "+125.0%"
    assert calc.matrix_cell_facts(-400.0, b, g_max=500.0, g_min=-400.0)["pct"] == "-100.0%"


def test_matrix_basis_claims_nothing_when_there_is_neither():
    for summary in ({}, None,
                    # uncapped upside taken for a CREDIT: nothing was paid, so
                    # there is no cost to divide by either.
                    {"max_profit": calc.UNLIMITED, "entry_credit": 250.0},
                    {"max_profit": 0.0, "entry_credit": 0.0}):
        b = calc.matrix_basis(summary)
        assert b == {"kind": "none", "denominator": None, "heading": "%"}, summary
        assert calc.matrix_cell_facts(90.0, b, g_max=100.0, g_min=-50.0)["pct"] == "—"


def test_matrix_basis_prefers_the_capped_return_over_the_cost():
    # A debit vertical has BOTH. The cap wins — it is what the MAX RETURN tile
    # above the matrix shows, and agreeing with that tile is the whole point.
    b = calc.matrix_basis({"max_profit": 300.0, "entry_credit": -200.0})
    assert b["kind"] == "max" and b["denominator"] == 300.0


def test_matrix_basis_never_reads_a_credit_as_a_cost():
    # entry_credit is NEGATIVE for a debit. A credit structure with no capped
    # return must not divide by the credit received and label it cost.
    assert calc.matrix_basis({"max_profit": calc.UNLIMITED,
                              "entry_credit": 180.0})["kind"] == "none"


def test_matrix_basis_ignores_junk_in_the_summary():
    for summary in ({"max_profit": "n/a", "entry_credit": None},
                    {"max_profit": None, "entry_credit": "n/a"},
                    {"max_profit": float("nan"), "entry_credit": float("-inf")}):
        assert calc.matrix_basis(summary)["kind"] == "none", summary


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


# ── the P&L matrix: % of max return, on the design's palette ─────────────────

def _basis(max_profit=None, entry_credit=None):
    """A basis via the REAL resolver, so these tests exercise the shipping path
    rather than a hand-built dict that could drift from it."""
    return calc.matrix_basis({"max_profit": max_profit,
                              "entry_credit": entry_credit})


def test_matrix_cell_facts_tints_by_magnitude_against_the_grid_extremes():
    hot = calc.matrix_cell_facts(100.0, _basis(180.0), g_max=100.0, g_min=-50.0)
    cool = calc.matrix_cell_facts(10.0, _basis(180.0), g_max=100.0, g_min=-50.0)
    assert hot["bg"].startswith("rgba(45,212,167,")
    assert cool["bg"].startswith("rgba(45,212,167,")
    assert hot["alpha"] > cool["alpha"]


def test_matrix_cell_facts_uses_the_loss_hue_below_zero():
    cell = calc.matrix_cell_facts(-40.0, _basis(180.0), g_max=100.0, g_min=-50.0)
    assert cell["bg"].startswith("rgba(251,95,124,")


def test_matrix_cell_facts_scales_each_side_on_its_own_extreme():
    # Profit is measured against g_max and loss against g_min. A shared scale
    # would wash out the profit zone of every credit structure, where the risk
    # is several times the reward. Both cells here sit HALF way to their own
    # extreme and must therefore tint identically — comparing the two EXTREMES
    # would not show it, since a shared scale clamps both to 1.0 as well.
    half_up = calc.matrix_cell_facts(50.0, _basis(180.0), g_max=100.0, g_min=-900.0)
    half_down = calc.matrix_cell_facts(-450.0, _basis(180.0), g_max=100.0, g_min=-900.0)
    assert half_up["alpha"] == half_down["alpha"]
    # …and neither is saturated, so the assertion above is about the scale.
    assert half_up["alpha"] < calc.matrix_cell_facts(
        100.0, _basis(180.0), g_max=100.0, g_min=-900.0)["alpha"]


def test_matrix_cell_facts_clamps_a_cell_beyond_the_grid_extreme():
    over = calc.matrix_cell_facts(500.0, _basis(180.0), g_max=100.0, g_min=-50.0)
    at = calc.matrix_cell_facts(100.0, _basis(180.0), g_max=100.0, g_min=-50.0)
    assert over["alpha"] == at["alpha"]
    assert 0.0 < over["alpha"] <= 1.0


def test_matrix_cell_facts_survives_a_degenerate_grid():
    # An all-zero grid has no extreme to scale against; the ramp must not divide
    # by it, and the alpha must stay a legal CSS value.
    flat = calc.matrix_cell_facts(0.0, _basis(180.0), g_max=0.0, g_min=0.0)
    assert 0.0 < flat["alpha"] <= 1.0
    assert flat["dollars"] == "+0"


def test_matrix_cell_facts_never_renders_a_real_reading_as_no_reading():
    # The ramp carries a floor: the faintest REAL cell must still be tinted, or
    # a genuine $0 is indistinguishable from a cell that carries no P&L at all.
    missing = calc.matrix_cell_facts(None, _basis(180.0), g_max=1e9, g_min=-1e9)
    for pnl in (0.0, 0.4, -0.4):
        cell = calc.matrix_cell_facts(pnl, _basis(180.0), g_max=1e9, g_min=-1e9)
        assert cell["alpha"] > missing["alpha"], pnl
        assert cell["bg"] != missing["bg"], pnl


def test_matrix_cell_pct_is_a_share_of_max_return():
    cell = calc.matrix_cell_facts(90.0, _basis(180.0), g_max=100.0, g_min=-50.0)
    assert cell["pct"] == "+50.0%"


def test_matrix_cell_pct_is_an_em_dash_without_a_max_return():
    cell = calc.matrix_cell_facts(90.0, _basis(0.0), g_max=100.0, g_min=-50.0)
    assert cell["pct"] == "—"


def test_matrix_cell_pct_is_an_em_dash_for_the_unlimited_sentinel():
    # A long call's max_profit is the 999999 placeholder. A percentage of an
    # uncapped return does not exist; +0.0% on every cell would be a fake
    # measurement stated confidently.
    cell = calc.matrix_cell_facts(90.0, _basis(calc.UNLIMITED), g_max=100.0, g_min=-50.0)
    assert cell["pct"] == "—"
    assert cell["dollars"] == "+90"          # the dollar figure is still real


def test_matrix_cell_pct_clamps_at_both_ends_not_just_the_top():
    # The overflow that actually happens is the LOSS side: a credit spread's max
    # loss is routinely several times its max return, and a naked put's is
    # hundreds of times. "-21,900.0%" would blow the column width open.
    assert calc.matrix_cell_facts(-40000.0, _basis(180.0), g_max=180.0,
                                  g_min=-40000.0)["pct"] == "<-999%"
    assert calc.matrix_cell_facts(400000.0, _basis(180.0), g_max=400000.0,
                                  g_min=-50.0)["pct"] == ">999%"


def test_matrix_cell_facts_renders_a_missing_pnl_as_an_em_dash():
    for junk in (None, "", float("nan"), float("inf")):
        cell = calc.matrix_cell_facts(junk, _basis(180.0), g_max=100.0, g_min=-50.0)
        assert cell["dollars"] == "—", junk
        assert cell["pct"] == "—", junk
        assert cell["bg"] == "transparent", junk
        assert cell["alpha"] == 0.0, junk


def test_matrix_cell_facts_rejects_a_bool_pnl():
    # ``bool`` is an ``int`` subclass, so an unguarded isinstance check would
    # price ``True`` as a $1 profit.
    assert calc.matrix_cell_facts(True, _basis(180.0), g_max=100.0, g_min=-50.0)["dollars"] == "—"


def test_matrix_headers_flag_the_expiry_column():
    hdrs = calc.matrix_headers(["Now", "08/21", "08/23", "Exp"], _basis(180.0))
    assert [h["expiry"] for h in hdrs] == [False, False, False, True]
    assert hdrs[0]["label"] == "NOW $"
    assert hdrs[1]["label"] == "08/21 $"
    assert {h["pct_label"] for h in hdrs} == {"% MAX"}


def test_matrix_headers_on_an_empty_grid():
    assert calc.matrix_headers([], _basis(180.0)) == []
    assert calc.matrix_headers(None, _basis(180.0)) == []


def test_matrix_headers_on_a_single_column_flags_it():
    assert calc.matrix_headers(["Now"], _basis(180.0)) == [
        {"label": "NOW $", "pct_label": "% MAX", "expiry": True}]


def test_matrix_headers_name_the_basis_they_were_given():
    for basis, heading in ((_basis(180.0), "% MAX"),
                           (_basis(calc.UNLIMITED, -400.0), "% COST"),
                           (_basis(), "%"),
                           (None, "%")):
        hdrs = calc.matrix_headers(["Now", "Exp"], basis)
        assert {h["pct_label"] for h in hdrs} == {heading}, heading


# ── the rendered matrix fragment ─────────────────────────────────────────────

def _matrix_data():
    return [{"price": 440.0, "pnl": [-120.0, -300.0], "pnl_pct": [0, 0]},
            {"price": 450.0, "pnl": [10.0, 90.0], "pnl_pct": [0, 0]},
            {"price": 460.0, "pnl": [60.0, None], "pnl_pct": [0, 0]}]


def _spot_row(html):
    """The one <tr> carrying the spot-row id, sliced out of the fragment."""
    start = html.index('<tr id="calc-spot-row"')
    return html[start:html.index("</tr>", start)]


def test_matrix_html_marks_the_spot_row_amber():
    html = calc.matrix_html(["Now", "Exp"], _matrix_data(), spot=450.0, summary={"max_profit": 180.0})
    # Scoped to the spot row: the expiry header is amber too, so a bare
    # "the colour appears somewhere" assertion could not fail.
    assert calc.MATRIX_SPOT == "#f5b841"
    row = _spot_row(html)
    assert "450.00" in row
    # Both marks, asserted separately: the price cell's amber ground and the
    # amber rule around the row would otherwise each hide the other's loss.
    assert f"background:{calc.MATRIX_SPOT}" in row
    assert f"border-top:1px solid {calc.MATRIX_SPOT}" in row
    assert f"border-bottom:1px solid {calc.MATRIX_SPOT}" in row
    body_before_spot = html.split("</thead>")[1].split('<tr id="calc-spot-row"')[0]
    assert calc.MATRIX_SPOT not in body_before_spot


def _header_cells(html):
    """The <th> elements of the fragment, in order.

    ⚠ NOT ``head.split("<th")`` — that also splits on ``<thead>`` and shifts
    every index by one, which is how the "these columns are NOT amber"
    assertions below were once pointed at markup that could never be amber.
    """
    head = html[html.index("<thead>"):html.index("</thead>")]
    return re.findall(r"<th\b.*?</th>", head)


def test_matrix_html_colours_only_the_expiry_header_amber():
    html = calc.matrix_html(["Now", "08/21", "Exp"], _matrix_data(),
                            spot=450.0, summary={"max_profit": 180.0})
    cells = _header_cells(html)
    assert len(cells) == 7                        # PRICE + 3 columns x ($, %)
    assert "PRICE" in cells[0] and "NOW $" in cells[1] and "EXP $" in cells[5]
    assert calc.MATRIX_SPOT in cells[5]           # the expiry $ column
    assert calc.MATRIX_SPOT in cells[6]           # …and its % column
    assert calc.MATRIX_SPOT not in cells[0]       # PRICE
    assert calc.MATRIX_SPOT not in cells[1]       # NOW $
    assert calc.MATRIX_SPOT not in cells[2]       # …and its % column


def test_matrix_html_names_the_percentage_column_after_its_denominator():
    # The column changed meaning (share of premium received -> share of MAX
    # RETURN). A bare "%" heading would still read as the old one.
    html = calc.matrix_html(["Now", "Exp"], _matrix_data(), spot=450.0,
                            summary={"max_profit": 180.0})
    assert [c for c in _header_cells(html) if ">% MAX<" in c]
    assert not [c for c in _header_cells(html) if ">%<" in c]


def test_matrix_html_prints_the_percentage_against_max_return():
    html = calc.matrix_html(["Now", "Exp"], _matrix_data(), spot=450.0, summary={"max_profit": 180.0})
    assert "+50.0%" in html          # the 90.0 cell against a 180 max return
    assert "+90" in html


def test_matrix_html_renders_a_missing_cell_as_an_em_dash():
    html = calc.matrix_html(["Now", "Exp"], _matrix_data(), spot=450.0, summary={"max_profit": 180.0})
    assert ">+0<" not in html and ">+0.0%<" not in html
    assert ">—<" in html


def test_matrix_html_percentages_are_em_dashes_with_no_basis_at_all():
    # An uncapped return taken for a CREDIT: no cap to divide by and nothing
    # paid either, so the column states nothing.
    html = calc.matrix_html(["Now", "Exp"], _matrix_data(), spot=450.0,
                            summary={"max_profit": calc.UNLIMITED,
                                     "entry_credit": 250.0})
    assert "%" not in html.split("</thead>")[1]
    assert "+90" in html            # the dollars are untouched


def test_matrix_html_gives_a_long_call_real_percentages_not_em_dashes():
    # The regression this basis exists to prevent: before it, a long call's
    # whole percentage column was em-dashes, where the page it replaced showed
    # a return on cost.
    html = calc.matrix_html(["Now", "Exp"], _matrix_data(), spot=450.0,
                            summary={"max_profit": calc.UNLIMITED,
                                     "max_loss": 400.0, "entry_credit": -400.0})
    body = html.split("</thead>")[1]
    assert "+22.5%" in body         # the +90 cell against a 400 cost
    assert "-75.0%" in body         # …and the -300 cell
    # The ONLY em-dashes left are the two belonging to the one cell that
    # genuinely carries no P&L — not a column-wide "no basis" wipe.
    assert body.count("—") == 2


def test_matrix_html_heads_the_percentage_column_with_its_own_basis():
    # One meaning per column, and the heading always names it.
    for summary, heading in (({"max_profit": 180.0}, "% MAX"),
                             ({"max_profit": calc.UNLIMITED,
                               "entry_credit": -400.0}, "% COST"),
                             ({}, "%")):
        cells = _header_cells(calc.matrix_html(["Now", "Exp"], _matrix_data(),
                                               spot=450.0, summary=summary))
        assert [c for c in cells if f">{heading}<" in c], (summary, heading)


def test_matrix_html_uses_tabular_figures():
    html = calc.matrix_html(["Now", "Exp"], _matrix_data(), spot=450.0, summary={"max_profit": 180.0})
    assert "tabular-nums" in html


def test_matrix_html_carries_the_scroll_and_spot_anchors():
    # ``_CENTER_SPOT_JS`` looks these two ids up by name; losing either leaves
    # the grid opening scrolled away from spot.
    html = calc.matrix_html(["Now", "Exp"], _matrix_data(), spot=450.0, summary={"max_profit": 180.0})
    assert 'id="calc-grid-scroll"' in html and 'id="calc-spot-row"' in html
    assert "calc-grid-scroll" in calc._CENTER_SPOT_JS
    assert "calc-spot-row" in calc._CENTER_SPOT_JS


def test_matrix_html_on_an_empty_grid_is_empty():
    assert calc.matrix_html([], [], spot=450.0, summary={"max_profit": 180.0}) == ""
    assert calc.matrix_html(["Now"], None, spot=450.0,
                            summary={"max_profit": 180.0}) == ""


def test_matrix_html_wears_the_designs_grounds_not_the_old_navy():
    html = calc.matrix_html(["Now", "Exp"], _matrix_data(), spot=450.0, summary={"max_profit": 180.0})
    assert "#141a30" not in html and "#2a2a2a" not in html
    assert calc.MATRIX_HEAD_BG in html and calc.MATRIX_HEAD_RULE in html
    assert calc.MATRIX_ROW_RULE in html


def test_the_retired_band_helpers_are_gone():
    # ``matrix_cell_facts`` replaced the p1..p5/l1..l5 class ramp outright; the
    # old helpers had no other caller.
    for dead in ("pnl_cell_class", "_band", "_CELL_COLORS", "matrix_pct_of_max"):
        assert not hasattr(calc, dead), dead


# ── the six metric cards, and the rest of the rebuilt screen's pure surface ──

def test_metric_cards_lead_with_credit_or_debit():
    credit = calc.metric_cards({"entry_credit": 180.0, "max_loss": 320.0,
                                "max_profit": 180.0, "return_on_risk": 56.3,
                                "breakevens": [658.2], "pop": 71.4},
                               legs=[], spot=668.41, max_dte=9)
    assert credit[0]["label"] == "ENTRY CREDIT"
    assert credit[0]["accent"] == "pos"

    debit = calc.metric_cards({"entry_credit": -400.0}, legs=[], spot=100.0, max_dte=9)
    assert debit[0]["label"] == "ENTRY DEBIT"
    assert debit[0]["accent"] == "accent"


def test_metric_card_values_carry_the_dollar_figures():
    cards = calc.metric_cards({"entry_credit": 180.0, "max_loss": 320.0,
                               "max_profit": 180.0, "return_on_risk": 56.3,
                               "breakevens": [658.2], "pop": 71.4},
                              legs=[], spot=668.41, max_dte=9)
    by_label = {c["label"]: c["value"] for c in cards}
    assert by_label["ENTRY CREDIT"] == "$180"
    assert by_label["MAX RISK"] == "$320"
    assert by_label["MAX RETURN"] == "$180"
    assert by_label["RETURN ON RISK"] == "56.3%"
    assert by_label["BREAKEVEN(S)"] == "658.20"
    assert by_label["PROB OF PROFIT"] == "71.4%"


def test_metric_cards_keep_the_designs_order():
    labels = [c["label"] for c in calc.metric_cards({}, legs=[], spot=0, max_dte=0)]
    assert labels == ["ENTRY CREDIT", "MAX RISK", "MAX RETURN",
                      "RETURN ON RISK", "BREAKEVEN(S)", "PROB OF PROFIT"]


def test_unlimited_max_return_reads_as_unlimited_not_as_a_number():
    # $999,999 is the service's placeholder, not a dollar figure. Printing it
    # would be a wrong number stated confidently.
    cards = calc.metric_cards({"max_profit": calc.UNLIMITED}, legs=[], spot=1.0, max_dte=9)
    ret = next(c for c in cards if c["label"] == "MAX RETURN")
    assert ret["value"] == "Unlimited"


def test_unlimited_max_risk_reads_as_unlimited():
    cards = calc.metric_cards({"max_loss": calc.UNLIMITED}, legs=[], spot=1.0, max_dte=9)
    risk = next(c for c in cards if c["label"] == "MAX RISK")
    assert risk["value"] == "Unlimited"


def test_return_on_risk_is_an_em_dash_when_either_side_is_uncapped():
    # calc_summary already zeroes return_on_risk in this case; a bare "0.0%"
    # would read as a measured zero return rather than "not defined".
    cards = calc.metric_cards({"max_profit": calc.UNLIMITED, "return_on_risk": 0.0},
                              legs=[], spot=1.0, max_dte=9)
    ror = next(c for c in cards if c["label"] == "RETURN ON RISK")
    assert ror["value"] == "—"

    uncapped_loss = calc.metric_cards({"max_loss": calc.UNLIMITED, "return_on_risk": 12.0},
                                      legs=[], spot=1.0, max_dte=9)
    ror2 = next(c for c in uncapped_loss if c["label"] == "RETURN ON RISK")
    assert ror2["value"] == "—"


def test_metric_cards_are_always_six_and_accent_from_a_finite_set():
    cards = calc.metric_cards({}, legs=[], spot=0, max_dte=0)
    assert len(cards) == 6
    assert {c["accent"] for c in cards} <= {"pos", "neg", "accent", "warn", "dim"}


def test_metric_cards_degrade_to_em_dashes_never_to_zeroes():
    # An empty summary is "not calculated yet", not "$0 risk on a 0% return".
    for card in calc.metric_cards({}, legs=[], spot=0, max_dte=0):
        assert card["value"] == "—", card["label"]
        assert card["accent"] == "dim", card["label"]


def test_metric_cards_accents_stay_inside_the_page_map():
    # The page maps ``accent`` onto CALC_EDGE_* / CALC_* classes through a dict
    # lookup — an accent word with no entry would raise mid-render.
    cards = calc.metric_cards({"entry_credit": -1.0, "max_loss": 2.0, "max_profit": 3.0,
                               "return_on_risk": 4.0, "breakevens": [5.0], "pop": 6.0},
                              legs=[], spot=10.0, max_dte=3)
    for card in cards:
        assert card["accent"] in calc.METRIC_ACCENTS


def test_return_on_risk_card_reports_a_per_day_figure():
    cards = calc.metric_cards({"return_on_risk": 56.0}, legs=[], spot=1.0, max_dte=7)
    ror = next(c for c in cards if c["label"] == "RETURN ON RISK")
    assert "8.00% per day" in ror["sub"]


def test_return_on_risk_per_day_does_not_divide_by_zero():
    cards = calc.metric_cards({"return_on_risk": 56.0}, legs=[], spot=1.0, max_dte=0)
    ror = next(c for c in cards if c["label"] == "RETURN ON RISK")
    assert "per day" in ror["sub"]
    assert "56.00% per day" in ror["sub"]


def test_return_on_risk_sub_is_an_em_dash_without_a_horizon():
    # No dated legs -> no per-day figure, rather than one computed against an
    # invented horizon of a single day.
    cards = calc.metric_cards({"return_on_risk": 56.0}, legs=[], spot=1.0, max_dte=None)
    ror = next(c for c in cards if c["label"] == "RETURN ON RISK")
    assert ror["value"] == "56.0%"
    assert ror["sub"] == "—"


def test_breakeven_card_reports_distance_from_spot():
    cards = calc.metric_cards({"breakevens": [658.2]}, legs=[], spot=668.41, max_dte=9)
    be = next(c for c in cards if c["label"] == "BREAKEVEN(S)")
    assert "-1.53%" in be["sub"]


def test_breakeven_card_without_a_crossing():
    cards = calc.metric_cards({"breakevens": []}, legs=[], spot=668.41, max_dte=9)
    be = next(c for c in cards if c["label"] == "BREAKEVEN(S)")
    assert be["value"] == "—"


def test_breakeven_card_lists_both_crossings():
    cards = calc.metric_cards({"breakevens": [640.0, 700.0]}, legs=[], spot=668.41, max_dte=9)
    be = next(c for c in cards if c["label"] == "BREAKEVEN(S)")
    assert be["value"] == "640.00 / 700.00"


def test_breakeven_distance_needs_a_spot_to_measure_from():
    cards = calc.metric_cards({"breakevens": [658.2]}, legs=[], spot=0, max_dte=9)
    be = next(c for c in cards if c["label"] == "BREAKEVEN(S)")
    assert be["sub"] == "—"


def test_entry_card_sub_counts_contracts_and_legs():
    legs = [{"side": "short", "premium": 3.0, "qty": 10},
            {"side": "long", "premium": 1.2, "qty": 10}]
    cards = calc.metric_cards({"entry_credit": 1800.0}, legs=legs, spot=668.41, max_dte=9)
    assert cards[0]["sub"] == "10 contracts · 2 legs"


def test_entry_card_sub_is_singular_for_one_contract_and_one_leg():
    cards = calc.metric_cards({"entry_credit": 180.0},
                              legs=[{"side": "short", "premium": 1.8, "qty": 1}],
                              spot=1.0, max_dte=9)
    assert cards[0]["sub"] == "1 contract · 1 leg"


def test_max_return_keeps_the_sign_of_a_losing_structure():
    # The generic NUMERIC summary's max_profit is max(pnl) over its own grid and
    # CAN be negative. Printing it as "$300" would invert the reading.
    cards = calc.metric_cards({"max_profit": -300.0}, legs=[], spot=1.0, max_dte=9)
    ret = next(c for c in cards if c["label"] == "MAX RETURN")
    assert ret["value"] == "-$300"


def test_prob_of_profit_accent_follows_the_reading():
    def _pop_accent(v):
        cards = calc.metric_cards({"pop": v}, legs=[], spot=1.0, max_dte=9)
        return next(c for c in cards if c["label"] == "PROB OF PROFIT")["accent"]
    assert _pop_accent(71.4) == "pos"
    assert _pop_accent(50.0) == "warn"
    assert _pop_accent(20.0) == "neg"


# ── the horizon the metric cards are dated against ──────────────────────────

def test_max_dte_from_legs_takes_the_last_expiry():
    legs = [{"expiry": "2026-08-21"}, {"expiry": "2026-08-28"}]
    assert calc.max_dte_from_legs(legs, today=dt.date(2026, 8, 19)) == 9


def test_max_dte_from_legs_floors_a_past_expiry_at_zero():
    legs = [{"expiry": "2026-08-01"}]
    assert calc.max_dte_from_legs(legs, today=dt.date(2026, 8, 19)) == 0


def test_max_dte_from_legs_is_none_without_a_usable_expiry():
    # The result payload carries MM/DD eval labels and no horizon of its own, so
    # an unparseable expiry means no per-day figure — not a guessed one.
    assert calc.max_dte_from_legs([], today=dt.date(2026, 8, 19)) is None
    assert calc.max_dte_from_legs([{"expiry": None}], today=dt.date(2026, 8, 19)) is None
    assert calc.max_dte_from_legs([{"expiry": "next week"}],
                                  today=dt.date(2026, 8, 19)) is None


def test_max_dte_from_legs_ignores_the_junk_and_keeps_the_rest():
    legs = [{"expiry": "not a date"}, {"expiry": "2026-08-28"}]
    assert calc.max_dte_from_legs(legs, today=dt.date(2026, 8, 19)) == 9


# ── the ③ LEGS strip ────────────────────────────────────────────────────────

def test_compact_money_renders_an_em_dash_for_no_reading():
    assert calc.compact_money(None) == "—"
    assert calc.compact_money("junk") == "—"


def test_compact_money_keeps_small_figures_exact():
    assert calc.compact_money(320.0) == "$320"
    assert calc.compact_money(-400.0) == "-$400"
    assert calc.compact_money(180.0, signed=True) == "+$180"
    assert calc.compact_money(-400.0, signed=True) == "-$400"


def test_compact_money_abbreviates_past_the_frame_width():
    # A naked put on a 660 strike is $65,700 and a naked NDX put is millions —
    # the ③ LEGS strip has ~130px for this, beside two other readings.
    assert calc.compact_money(65_700.0) == "$65,700"
    assert calc.compact_money(120_000.0) == "$120K"
    assert calc.compact_money(2_488_000.0) == "$2.49M"
    assert calc.compact_money(-2_488_000.0, signed=True) == "-$2.49M"


def test_compact_money_zero_is_a_reading_not_a_blank():
    assert calc.compact_money(0.0) == "$0"


def test_leg_strip_facts_reads_the_legs():
    legs = [{"option_type": "put", "side": "short", "strike": 660, "premium": 3.0, "qty": 1},
            {"option_type": "put", "side": "long", "strike": 655, "premium": 1.2, "qty": 1}]
    facts = calc.leg_strip_facts(legs)
    assert facts["count"] == "2 LEGS"
    assert facts["net"] == "NET +$180"
    assert facts["net_tone"] == "pos"
    assert facts["max_loss"] == "MAX LOSS $320"


def test_leg_strip_facts_em_dashes_an_unpriced_template():
    # build_default_legs sets premium=None on every leg, so this is the state of
    # every fresh template before Fetch Premiums. "NET $0" would state a figure
    # the page does not have.
    legs = [{"option_type": "put", "side": "short", "strike": 660, "premium": None, "qty": 1},
            {"option_type": "put", "side": "long", "strike": 655, "premium": None, "qty": 1}]
    facts = calc.leg_strip_facts(legs)
    assert facts["net"] == "NET —"
    assert facts["net_tone"] == "dim"
    assert facts["max_loss"] == "MAX LOSS —"


def test_leg_strip_facts_em_dashes_an_unbounded_loss():
    legs = [{"option_type": "call", "side": "short", "strike": 700, "premium": 2.0, "qty": 1}]
    facts = calc.leg_strip_facts(legs)
    assert facts["count"] == "1 LEG"
    assert facts["net"] == "NET +$200"
    assert facts["max_loss"] == "MAX LOSS —"


def test_leg_strip_facts_marks_a_debit_negative():
    legs = [{"option_type": "call", "side": "long", "strike": 670, "premium": 4.0, "qty": 1}]
    facts = calc.leg_strip_facts(legs)
    assert facts["net"] == "NET -$400"
    assert facts["net_tone"] == "neg"


def test_leg_strip_facts_on_no_legs():
    facts = calc.leg_strip_facts([])
    assert facts["count"] == "0 LEGS"
    assert facts["net"] == "NET +$0"


# ── the two derived copy lines ──────────────────────────────────────────────

def test_results_panel_names_which_wait_it_is():
    idle = calc.chain_status_facts(loading=False, symbol="", chain=None)
    before = calc.results_panel_facts(idle, has_result=False)
    assert before["label"] == "AWAITING CHAIN"

    ready = calc.chain_status_facts(loading=False, symbol="SPY", chain=_chain())
    after = calc.results_panel_facts(ready, has_result=False)
    assert after["label"] == "AWAITING CALCULATION"
    assert after["hint"] != before["hint"]


def test_results_panel_is_none_once_a_result_lands():
    ready = calc.chain_status_facts(loading=False, symbol="SPY", chain=_chain())
    assert calc.results_panel_facts(ready, has_result=True) is None


def test_results_panel_while_loading_still_awaits_the_chain():
    busy = calc.chain_status_facts(loading=True, symbol="SPY", chain=None)
    assert calc.results_panel_facts(busy, has_result=False)["label"] == "AWAITING CHAIN"


def test_chain_line_says_what_to_do_next():
    idle = calc.chain_status_facts(loading=False, symbol="", chain=None)
    assert "tab out" in calc.chain_line(idle, "", 0, 0)

    busy = calc.chain_status_facts(loading=True, symbol="SPY", chain=None)
    assert calc.chain_line(busy, "SPY", 0, 0) == "fetching option chain · SPY"

    ready = calc.chain_status_facts(loading=False, symbol="SPY", chain=_chain())
    assert calc.chain_line(ready, "SPY", 6, 48) == "48 strikes · 6 expiries"


def test_matrix_note_names_the_percentage_basis():
    data = _matrix_data()
    assert calc.matrix_note_text(data, calc.matrix_basis({"max_profit": 180.0})) \
        == f"PRICE × DATE · {len(data)} ROWS · % OF MAX RETURN"
    assert "% OF COST" in calc.matrix_note_text(
        data, calc.matrix_basis({"max_profit": calc.UNLIMITED, "entry_credit": -400.0}))
    assert "NO PERCENTAGE BASIS" in calc.matrix_note_text(data, calc.matrix_basis({}))


def test_tag_tone_colours_only_the_cash_flow_chip():
    assert calc.tag_tone("CREDIT", first=True) == "pos"
    assert calc.tag_tone("DEBIT", first=True) == "warn"
    assert calc.tag_tone("CREDIT", first=False) == "muted"
    assert calc.tag_tone("2 LEGS", first=False) == "muted"


# ── the rebuilt screen ──────────────────────────────────────────────────────

def test_render_wears_the_calc_v3_scope_and_not_the_shared_navy_one():
    import inspect
    src = inspect.getsource(calc.render)
    assert "calc-v3" in src
    assert "calc-v2" not in src, "the Calculator must not restyle the Simulator scope"
    assert "QUASAR_INTERNAL_CSS" not in src, "that block is scoped .calc-v2"


def test_render_mounts_the_card_leg_editor_with_the_calc_palette():
    import inspect
    src = inspect.getsource(calc.render)
    assert 'layout="card"' in src
    assert "delta_for=" in src, "the DELTA column collapses without a source"
    assert "min_legs=" in src
    assert "on_reset=" in src


def test_render_keeps_the_top_level_expiry_inside_the_applying_guard():
    # Programmatic expiry writes must not fire _on_expiry_change, or the legs are
    # re-propagated mid-load. The design drops the top-level Expiry; the real page
    # needs it for calc_compute's expiry argument.
    import inspect
    src = inspect.getsource(calc.render)
    assert src.count('state["applying"] = True') == 2
    assert src.count('state["applying"] = False') == 2
    assert "expiry_sel" in src


def test_render_preserves_every_wired_behaviour():
    import inspect
    src = inspect.getsource(calc.render)
    for fn in ("def _capture", "def _restore", "def _seed_template", "def _scale_leg_qty",
               "def _on_contracts_change", "def _on_expiry_change", "def fetch_premiums",
               "def load_symbol", "def _load_timeout", "def _symbol_submit",
               "def fetch_iv", "def do_calc", "def send_to_em", "def _apply_chain",
               "def _apply_result", "def _apply_iv", "async def _poll_chain",
               "def _poll_result", "def _poll_iv", "def _prefill"):
        assert fn in src, f"render() lost {fn!r}"
    assert "take_pending_calculator()" in src
    assert "take_pending_calculator_legs()" in src
