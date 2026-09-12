"""The shared chain readers + the entry panel's chain-grid builders (pure)."""
import inspect

from pages.options import calculator as calc
from pages.options import chain_grid as cg

_READERS = ("extract_atm_iv", "_find_contract", "_finite", "extract_premium",
            "extract_delta", "leg_delta", "position_delta", "chain_expiries",
            "chain_strikes")


def test_calculator_reexports_the_readers():
    # Moved so the Simulator reads the same chain without importing another PAGE.
    for name in _READERS:
        assert getattr(calc, name) is getattr(cg, name), name


def test_chain_grid_holds_no_engine_or_ui_imports():
    src = inspect.getsource(cg)
    for forbidden in ("scanner_engine", "options_calculator", "import proxy",
                      "from nicegui", "import nicegui"):
        assert forbidden not in src, forbidden


# ── the chain grid (Task 5) ─────────────────────────────────────────────────
import datetime as dt
import math


def _c(**kw):
    base = {"bid": 1.0, "ask": 1.2, "mark": 1.1, "delta": 0.4, "volatility": 20.0,
            "gamma": 0.03, "theta": -0.05, "vega": 0.1, "openInterest": 4120,
            "totalVolume": 88}
    base.update(kw)
    return [base]


CHAIN = {
    "callExpDateMap": {"2026-09-19:7": {f"{k}.0": _c(delta=0.5) for k in range(560, 581, 5)}},
    "putExpDateMap": {"2026-09-19:7": {f"{k}.0": _c(delta=-0.3) for k in range(560, 581, 5)}},
}


def test_default_columns_are_bid_ask_delta_oi():
    assert cg.DEFAULT_COLUMNS == ["bid", "ask", "delta", "openInterest"]


def test_column_labels_are_whole_words_or_trader_acronyms():
    assert cg.GRID_COLUMNS == {
        "bid": "Bid", "ask": "Ask", "mark": "Mark", "delta": "Delta",
        "volatility": "IV", "gamma": "Gamma", "theta": "Theta", "vega": "Vega",
        "openInterest": "OI", "totalVolume": "Volume"}


def test_parse_columns_keeps_known_in_registry_order_and_falls_back():
    assert cg.parse_columns(["openInterest", "bid", "ask"]) == ["bid", "ask", "openInterest"]
    assert cg.parse_columns(["nope"]) == cg.DEFAULT_COLUMNS
    assert cg.parse_columns(None) == cg.DEFAULT_COLUMNS
    assert cg.parse_columns("bid") == cg.DEFAULT_COLUMNS
    assert cg.parse_columns([]) == cg.DEFAULT_COLUMNS


def test_parse_columns_always_keeps_bid_and_ask():
    # Bid and Ask are the click targets; a grid without them cannot add a leg.
    assert cg.parse_columns(["delta"]) == ["bid", "ask", "delta"]


def test_cell_text_formats_by_field_and_dashes_non_readings():
    assert cg.cell_text("bid", 2.05) == "2.05"
    assert cg.cell_text("bid", 0) == "—"            # no market
    assert cg.cell_text("mark", -1) == "—"
    assert cg.cell_text("delta", -0.312) == "-0.31"
    assert cg.cell_text("delta", -999.0) == "—"     # Schwab's missing-greek sentinel
    assert cg.cell_text("theta", -999.0) == "—"
    assert cg.cell_text("gamma", 0.0312) == "0.031"
    assert cg.cell_text("vega", 0.104) == "0.10"
    assert cg.cell_text("volatility", 22.46) == "22.5"
    assert cg.cell_text("volatility", -999.0) == "—"
    assert cg.cell_text("openInterest", 4120) == "4.1k"
    assert cg.cell_text("openInterest", 0) == "0"   # zero OI is a real reading
    assert cg.cell_text("openInterest", 950) == "950"
    assert cg.cell_text("totalVolume", 1_260_000) == "1.3M"
    assert cg.cell_text("bid", None) == "—"
    assert cg.cell_text("bid", math.nan) == "—"
    assert cg.cell_text("bid", True) == "—"
    assert cg.cell_text("bid", "2.0") == "—"


def test_grid_rows_window_around_spot_and_flag_itm():
    g = cg.chain_grid_rows(CHAIN, "2026-09-19", spot=571.0, above=1, below=1)
    assert [r["strike"] for r in g["rows"]] == [570.0, 575.0]
    assert g["more_below"] is True and g["more_above"] is True
    r570, r575 = g["rows"]
    assert r570["call_itm"] is True and r570["put_itm"] is False
    assert r575["call_itm"] is False and r575["put_itm"] is True
    assert r570["atm"] is True and r575["atm"] is False
    assert r570["call"]["openInterest"] == 4120
    assert r570["put"]["delta"] == -0.3


def test_grid_rows_the_whole_ladder_fits_a_wide_window():
    g = cg.chain_grid_rows(CHAIN, "2026-09-19", spot=571.0, above=50, below=50)
    assert len(g["rows"]) == 5
    assert g["more_below"] is False and g["more_above"] is False


def test_grid_rows_do_not_alias_the_chain():
    g = cg.chain_grid_rows(CHAIN, "2026-09-19", spot=571.0)
    g["rows"][0]["call"]["bid"] = 99.0
    assert CHAIN["callExpDateMap"]["2026-09-19:7"]["560.0"][0]["bid"] == 1.0


def test_grid_rows_missing_side_is_an_empty_dict():
    chain = {"callExpDateMap": {"2026-09-19:7": {"570.0": _c()}}, "putExpDateMap": {}}
    g = cg.chain_grid_rows(chain, "2026-09-19", spot=570.0)
    assert g["rows"][0]["put"] == {}


def test_grid_rows_total_on_junk():
    empty = {"rows": [], "more_above": False, "more_below": False}
    assert cg.chain_grid_rows(None, "2026-09-19", 570.0) == empty
    assert cg.chain_grid_rows(CHAIN, "2031-01-01", 570.0) == empty
    junk = {"callExpDateMap": {"2026-09-19:7": {"x": _c(), "575.0": "nope"}}}
    assert cg.chain_grid_rows(junk, "2026-09-19", 570.0) == empty


def test_grid_rows_with_no_spot_start_at_the_bottom_and_flag_nothing():
    g = cg.chain_grid_rows(CHAIN, "2026-09-19", None, above=1, below=1)
    assert [r["strike"] for r in g["rows"]] == [560.0, 565.0]
    assert not any(r["call_itm"] or r["put_itm"] or r["atm"] for r in g["rows"])
    assert g["more_above"] is True and g["more_below"] is False


def test_expiry_pills_label_and_dte():
    pills = cg.expiry_pills(["2026-09-12", "2026-09-19", "junk"], today=dt.date(2026, 9, 12))
    assert pills == [{"value": "2026-09-12", "label": "Sep 12", "dte": 0},
                     {"value": "2026-09-19", "label": "Sep 19", "dte": 7}]
    assert cg.expiry_pills(["2026-10-03"], today=dt.date(2026, 9, 12))[0]["label"] == "Oct 3"
