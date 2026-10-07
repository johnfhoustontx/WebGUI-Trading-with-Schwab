"""``pages/options/scanner_structures`` - the Market Scanner's "Other structures"
tables: everything the 0-DTE and Swing scans build that is not a credit spread.

PURE builders only. The rows are the Directional tab's (the same normalized
candidate shape) plus the build family, for the chips, and the earnings note.
"""
from pages.options import scanner_structures as ssx


def _leg(kind, side, strike, exp="2099-01-01", qty=1):
    return {"kind": kind, "side": side, "strike": strike, "expiration": exp,
            "qty": qty}


def _sig(structure, group, **over):
    s = {"id": f"T_{structure}", "symbol": "T", "type": structure, "group": group,
         "family": "NEUTRAL", "strategy_label": structure.replace("_", " ").title(),
         "bias": "neutral", "legs": [_leg("call", "long", 100.0)],
         "expiration": "2099-01-01", "dte": 9, "net_debit": 120.0,
         "net_credit": None, "max_profit": 380.0, "max_loss": 121.3,
         "breakevens": [101.2], "rr": 3.1, "pop_pct": 34.0, "iv_rank": 41.0,
         "composite_score": 61.0, "grade": "Good"}
    s.update(over)
    return s


# ── rows ────────────────────────────────────────────────────────────────────

def test_rows_carry_the_symbol_the_type_and_the_family():
    row = ssx.structure_rows([_sig("BUTTERFLY_CALL", "BUTTERFLY")])[0]
    assert row["symbol"] == "T" and row["type"] == "BUTTERFLY_CALL"
    assert row["_group"] == "BUTTERFLY"
    assert row["group_label"] == "Butterflies and condors"
    # ... and everything the Directional tab's row carries.
    assert row["strategy_label"] == "Butterfly Call" and row["composite_score"] == 61.0
    assert row["legs"] != "—"


def test_every_group_the_service_can_send_has_a_label():
    for group, label in ssx.GROUPS:
        row = ssx.structure_rows([_sig("X", group)])[0]
        assert row["group_label"] == label and label


def test_an_unknown_group_gets_no_label_and_does_not_raise():
    rows = ssx.structure_rows([_sig("X", "MYSTERY"), _sig("Y", None, id="T_Y")])
    assert [r["group_label"] for r in rows] == ["", ""]
    assert {r["_group"] for r in rows} == {"MYSTERY", ""}


def test_a_row_held_through_a_report_names_the_date():
    row = ssx.structure_rows([_sig("LONG_STRADDLE", "STRADDLE",
                                   spans_earnings=True,
                                   earnings_date="2099-01-05")])[0]
    assert row["_earnings"] == "Earnings 01/05"


def test_a_flag_with_no_readable_date_still_says_earnings():
    row = ssx.structure_rows([_sig("LONG_STRADDLE", "STRADDLE",
                                   spans_earnings=True, earnings_date=None)])[0]
    assert row["_earnings"] == "Earnings"


def test_a_row_with_no_report_has_no_earnings_text():
    """``earnings_date`` alone is the checklist's stamp, present on every row of
    a symbol with a scheduled report. Only ``spans_earnings`` is the flag."""
    rows = ssx.structure_rows([
        _sig("BULL_CALL", "VERTICAL"),
        _sig("BEAR_PUT", "VERTICAL", earnings_date="2099-03-01"),
        _sig("LONG_PUT", "VERTICAL", spans_earnings=False, earnings_date="2099-03-01")])
    assert [r["_earnings"] for r in rows] == ["", "", ""]


def test_paper_is_offered_only_where_the_ledger_books_it():
    rows = ssx.structure_rows([
        _sig("BUTTERFLY_CALL", "BUTTERFLY"), _sig("BULL_CALL", "VERTICAL"),
        _sig("LONG_STRADDLE", "STRADDLE"), _sig("SHORT_STRANGLE", "STRADDLE"),
        _sig("IRON_BUTTERFLY", "BUTTERFLY"), _sig("CALENDAR_CALL", "CALENDAR")])
    assert {r["type"]: r["_allow_paper"] for r in rows} == {
        "BUTTERFLY_CALL": True, "BULL_CALL": True, "LONG_STRADDLE": False,
        "SHORT_STRANGLE": False, "IRON_BUTTERFLY": False, "CALENDAR_CALL": False}


def test_rows_come_back_best_first_and_empty_is_empty():
    rows = ssx.structure_rows([_sig("A", "VERTICAL", composite_score=55.0),
                               _sig("B", "STRADDLE", composite_score=72.0)])
    assert [r["type"] for r in rows] == ["B", "A"]
    assert ssx.structure_rows([]) == [] and ssx.structure_rows(None) == []


# ── the family chips ────────────────────────────────────────────────────────

def _rows():
    return ssx.structure_rows([
        _sig("LONG_STRADDLE", "STRADDLE"), _sig("BULL_CALL", "VERTICAL"),
        _sig("BEAR_PUT", "VERTICAL"), _sig("CALENDAR_PUT", "CALENDAR")])


def test_chips_list_only_groups_that_have_rows_in_the_fixed_order():
    chips = ssx.chips(_rows())
    assert [c["group"] for c in chips] == ["VERTICAL", "STRADDLE", "CALENDAR"]
    assert [c["text"] for c in chips] == [
        "Debit spreads · 2", "Straddles and strangles · 1", "Calendars · 1"]
    assert [c["count"] for c in chips] == [2, 1, 1]


def test_no_rows_no_chips():
    assert ssx.chips([]) == []


def test_filter_keeps_only_the_chosen_groups_in_their_order():
    rows = _rows()
    kept = ssx.filter_groups(rows, {"VERTICAL", "CALENDAR"})
    assert [r["type"] for r in kept] == [r["type"] for r in rows
                                         if r["_group"] != "STRADDLE"]
    assert ssx.filter_groups(rows, set()) == []
    assert ssx.filter_groups(rows, ssx.all_groups()) == rows


def test_a_row_of_an_unknown_group_is_never_hidden_by_the_chips():
    """It has no chip to turn it back on with."""
    rows = ssx.structure_rows([_sig("X", "MYSTERY"), _sig("BULL_CALL", "VERTICAL")])
    assert [r["type"] for r in ssx.filter_groups(rows, set())] == ["X"]


def test_toggling_a_group_returns_a_new_set():
    on = ssx.all_groups()
    off = ssx.toggled(on, "STRADDLE")
    assert "STRADDLE" not in off and "STRADDLE" in on
    assert ssx.toggled(off, "STRADDLE") == on


# ── the tab header and the two-way switch ───────────────────────────────────

def test_a_tab_counts_both_of_its_tables():
    full = {"signals_0dte": [1, 2, 3], "structures_0dte": [1, 2],
            "signals_swing": [1], "structures_swing": [],
            "signals_directional": [1, 2, 3, 4]}
    shown = {"signals_0dte": [1, 2], "structures_0dte": [1],
             "signals_swing": [1], "structures_swing": [],
             "signals_directional": [1]}
    assert ssx.tab_totals(full, shown) == {
        "0-DTE": (5, 3), "Swing": (1, 1), "Directional": (4, 1)}


def test_tab_totals_tolerate_a_missing_list():
    assert ssx.tab_totals({}, {}) == {"0-DTE": (0, 0), "Swing": (0, 0),
                                      "Directional": (0, 0)}


def test_every_day_list_belongs_to_exactly_one_tab():
    from pages.options import scanner
    owned = [key for keys in ssx.TAB_LISTS.values() for key in keys]
    assert sorted(owned) == sorted(scanner.DAY_LISTS)
    assert set(ssx.STRUCTURE_LISTS) == {"structures_0dte", "structures_swing"}


def test_the_switch_shows_where_the_rows_are():
    assert ssx.view_options(12, 7, have=True) == {
        "credit": "Credit spreads · 12", "other": "Other structures · 7"}
    # No count before today's first scan: a "0" there is a zero nobody read.
    assert ssx.view_options(0, 0, have=False) == {
        "credit": "Credit spreads", "other": "Other structures"}
