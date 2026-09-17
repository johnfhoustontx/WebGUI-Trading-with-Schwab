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


def test_default_columns_are_delta_oi_volume_bid_ask():
    # Operator's order (2026-09-12), read on the CALL side from the outside in.
    assert cg.DEFAULT_COLUMNS == ["delta", "openInterest", "totalVolume", "bid", "ask"]


def test_column_registry_runs_from_the_outer_edge_to_the_strike():
    # Registry order IS the call side's left-to-right order: optional Greeks on
    # the outer edge, Bid and Ask last so they sit against the strike.
    assert list(cg.GRID_COLUMNS) == ["gamma", "theta", "vega", "volatility", "delta",
                                     "openInterest", "totalVolume", "mark", "bid", "ask"]
    assert cg.parse_columns(cg.DEFAULT_COLUMNS) == cg.DEFAULT_COLUMNS


def test_column_labels_are_whole_words_or_trader_acronyms():
    assert cg.GRID_COLUMNS == {
        "bid": "Bid", "ask": "Ask", "mark": "Mark", "delta": "Delta",
        "volatility": "IV", "gamma": "Gamma", "theta": "Theta", "vega": "Vega",
        "openInterest": "OI", "totalVolume": "Volume"}


def test_parse_columns_keeps_known_in_registry_order_and_falls_back():
    assert cg.parse_columns(["bid", "ask", "openInterest"]) == ["openInterest", "bid", "ask"]
    assert cg.parse_columns(["nope"]) == cg.DEFAULT_COLUMNS
    assert cg.parse_columns(None) == cg.DEFAULT_COLUMNS
    assert cg.parse_columns("bid") == cg.DEFAULT_COLUMNS
    assert cg.parse_columns([]) == cg.DEFAULT_COLUMNS


def test_parse_columns_always_keeps_bid_and_ask():
    # Bid and Ask are the click targets; a grid without them cannot add a leg.
    assert cg.parse_columns(["delta"]) == ["delta", "bid", "ask"]


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


def test_grid_rows_default_to_the_complete_ladder():
    # The grid shows EVERY strike (2026-09-12); the page scrolls it to spot.
    g = cg.chain_grid_rows(CHAIN, "2026-09-19", spot=571.0)
    assert [r["strike"] for r in g["rows"]] == [560.0, 565.0, 570.0, 575.0, 580.0]
    assert g["more_below"] is False and g["more_above"] is False
    assert [r["atm"] for r in g["rows"]] == [False, False, True, False, False]


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


# ── the grid body as ONE html block (2026-09-12) ─────────────────────────────
import re as _re
import pathlib as _pathlib

_TK = {"strike": "t-strike", "strike_atm": "t-atm", "itm": "t-itm", "cell": "t-cell",
       "pick": "t-pick", "bid": "t-bid", "ask": "t-ask"}


def _html():
    g = cg.chain_grid_rows(CHAIN, "2026-09-19", spot=571.0)
    return cg.grid_body_html(g["rows"], ["delta", "bid", "ask"], ["bid", "ask", "delta"],
                             "grid grid-cols-[x]", _TK)


def test_grid_body_html_is_one_row_per_strike_with_the_atm_marked():
    out = _html()
    rows = _re.findall(r'<div class="entry-grow[^"]*"[^>]*data-strike="([^"]+)"', out)
    assert rows == ["560", "565", "570", "575", "580"]
    assert out.count("data-atm") == 1
    assert _re.search(r'data-strike="570"[^>]*data-atm', out)


def test_grid_body_html_bid_and_ask_cells_carry_what_a_click_needs():
    out = _html()
    picks = _re.findall(r'data-pick="(bid|ask)" data-side="(call|put)" data-strike="([^"]+)"', out)
    assert ("bid", "put", "570") in picks and ("ask", "call", "560") in picks
    assert len(picks) == 5 * 4                     # 2 click targets per side per strike
    assert "t-pick" in out and "t-bid" in out and "t-ask" in out


def test_grid_body_html_shades_only_the_in_the_money_side():
    out = _html()
    row560 = _re.search(r'data-strike="560".*?(?=<div class="entry-grow|$)', out, _re.S).group(0)
    call_cells = row560.split("entry-gstrike")[0]
    put_cells = row560.split("entry-gstrike")[1]
    assert "t-itm" in call_cells and "t-itm" not in put_cells


def test_grid_body_html_escapes_and_stays_total_on_no_rows():
    assert cg.grid_body_html([], ["bid", "ask"], ["bid", "ask"], "grid", _TK) == ""


def test_grid_body_html_emits_nothing_dompurify_would_strip():
    """ui.html sanitizes through the bundled DOMPurify (CLAUDE.md): a stripped
    attribute is invisible server-side, so the allow-list is checked here.
    data-* attributes survive because DOMPurify's ALLOW_DATA_ATTR defaults on —
    pinned against the shipped bundle so a NiceGUI upgrade that flips it fails."""
    from nicegui import ui
    src = (_pathlib.Path(ui.__file__).parent / "static" / "dompurify.mjs") \
        .read_text(encoding="utf-8", errors="replace")
    allow = set()
    for run in _re.findall(r'(?:"[a-z][a-z0-9-]*",){19,}"[a-z][a-z0-9-]*"', src):
        tokens = set(_re.findall(r'"([a-z][a-z0-9-]*)"', run))
        if "script" not in tokens:
            allow |= tokens
    assert "!1!==e.ALLOW_DATA_ATTR" in src, "DOMPurify's data-* default changed"
    out = _html()
    tags = set(_re.findall(r"<([a-zA-Z][\w-]*)", out))
    attrs = {a for a in _re.findall(r'([a-zA-Z][\w-]*)="', out) if not a.startswith("data-")}
    assert not sorted(n for n in tags | attrs if n.lower() not in allow)
    assert tags == {"div"} and "class" in attrs


# ── a leg priced from a chosen side of the quote (2026-09-12) ───────────────

_Q_EXP = "2026-09-19"
_Q_CHAIN = {"putExpDateMap": {f"{_Q_EXP}:7": {
    "565.0": [{"bid": 1.9, "ask": 2.1, "mark": 2.0}],
    "570.0": [{"bid": 0.0, "ask": 2.6, "mark": 0.0}]}}}


def test_extract_price_reads_the_chosen_side_of_the_quote():
    assert cg.extract_price(_Q_CHAIN, "put", 565.0, _Q_EXP, "bid") == 1.9
    assert cg.extract_price(_Q_CHAIN, "put", 565.0, _Q_EXP, "ask") == 2.1
    assert cg.extract_price(_Q_CHAIN, "put", 565.0, _Q_EXP, "mark") == 2.0


def test_extract_price_mark_is_extract_premium_and_unknown_source_is_the_mark():
    assert cg.extract_price(_Q_CHAIN, "put", 565.0, _Q_EXP, None) == 2.0
    assert cg.extract_price(_Q_CHAIN, "put", 565.0, _Q_EXP, "junk") == 2.0


def test_extract_price_a_zero_side_is_no_reading_not_a_zero_price():
    assert cg.extract_price(_Q_CHAIN, "put", 570.0, _Q_EXP, "bid") is None
    assert cg.extract_price(_Q_CHAIN, "put", 575.0, _Q_EXP, "ask") is None
# -- a half-strike ladder (2026-09-17) --------------------------------------
# Measured on UBER's 2026-09-18 expiry, which lists 72.0 AND 72.5: the old
# "within 0.51" match answered a 72.5 leg with 72.0's quote, delta and mark.

_H_EXP = "2026-09-18"
_H_CALLS = {
    "72.0": [{"bid": 0.18, "ask": 0.20, "mark": 0.19, "delta": 0.217}],
    "72.5": [{"bid": 0.12, "ask": 0.14, "mark": 0.13, "delta": 0.152}],
}


def _half_chain(reverse=False):
    keys = sorted(_H_CALLS, reverse=reverse)
    return {"callExpDateMap": {f"{_H_EXP}:1": {k: _H_CALLS[k] for k in keys}}}


def test_a_half_strike_reads_its_own_contract_whatever_order_the_chain_is_in():
    """Dict order decided the old match, so both orders are pinned."""
    for reverse in (False, True):
        chain = _half_chain(reverse)
        assert cg.extract_premium(chain, "call", 72.5, _H_EXP) == 0.13
        assert cg.extract_premium(chain, "call", 72.0, _H_EXP) == 0.19
        assert cg.extract_price(chain, "call", 72.5, _H_EXP, "bid") == 0.12
        assert cg.extract_price(chain, "call", 72.0, _H_EXP, "bid") == 0.18
        assert cg.extract_delta(chain, "call", 72.5, _H_EXP) == 0.152
        assert cg.extract_delta(chain, "call", 72.0, _H_EXP) == 0.217


def test_a_strike_the_expiry_does_not_list_is_no_reading():
    """A leg carried onto an expiry with a coarser ladder - UBER's weeklies list
    whole dollars only - shows an em-dash, never the next strike's price."""
    chain = {"callExpDateMap": {"2026-09-25:8": {
        "72.0": [{"mark": 0.40, "delta": 0.30}]}}}
    assert cg.extract_premium(chain, "call", 72.5, "2026-09-25") is None
    assert cg.extract_delta(chain, "call", 72.5, "2026-09-25") is None
    assert cg.leg_delta(chain, {"option_type": "call", "side": "long",
                                "strike": 72.5, "expiry": "2026-09-25"}) is None
