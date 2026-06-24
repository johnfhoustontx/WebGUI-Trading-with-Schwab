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
