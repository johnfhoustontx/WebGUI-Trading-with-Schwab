"""One table per tab on the Market Scanner's 0-DTE and Swing tabs.

Until 2026-10-07 each of those tabs held two tables behind a switch: the
credit spreads, and everything else the scan builds. The owner combined them:
credit spreads are rows in the SAME table as the other structures, in that
table's columns, under one more family checkbox, and the table is ranked by
score across both kinds.

⚠ The two kinds are scored on different scales - a credit spread as a trade
that sells premium, every other structure on fit and quality - and the owner
chose the single ranking knowing it. So the page SAYS it: a note under the
checkboxes, and a hover on every score that names its scale.
"""
import pathlib

import bus_client
import pytest

from pages.options import checks_feed, scanner, scanner_shared
from pages.options import scanner_structures as ssx

REPO = pathlib.Path(__file__).resolve().parents[2]


def _pcs(**over):
    sig = {"id": "ORCL_PCS_2026-10-17_100_97.5", "symbol": "ORCL", "type": "PCS",
           "trade_type": "SWING", "expiration": "2026-10-17", "dte": 12,
           "short_strike": 100.0, "long_strike": 97.5, "width": 2.5,
           "credit": 0.60, "max_loss": 1.90, "rr_pct": 31.6, "pop_pct": 72.0,
           "iv_rank": 55.4, "breakeven": 99.4, "composite_score": 70,
           "grade": "Good", "live": True, "stale_since": None}
    sig.update(over)
    return sig


def _ic(**over):
    return _pcs(id="ORCL_IC_2026-10-17_100_97.5_120_122.5", type="IC",
                call_short=120.0, call_long=122.5, credit=1.10, max_loss=1.40,
                rr_pct=78.6, pop_pct=61.0, breakeven="98.90/121.10",
                composite_score=64, **over)


def _leg(kind, side, strike, qty=1):
    return {"kind": kind, "side": side, "strike": strike,
            "expiration": "2026-10-17", "qty": qty}


def _fly(**over):
    sig = {"id": "ORCL_BUTTERFLY_CALL_2026-10-17", "symbol": "ORCL",
           "type": "BUTTERFLY_CALL", "group": "BUTTERFLY", "family": "NEUTRAL",
           "strategy_label": "Call Butterfly", "bias": "neutral",
           "legs": [_leg("call", "long", 105.0), _leg("call", "short", 110.0, 2),
                    _leg("call", "long", 115.0)],
           "expiration": "2026-10-17", "dte": 12, "net_debit": 120.0,
           "net_credit": None, "max_profit": 380.0, "max_loss": 121.3,
           "breakevens": [106.2, 113.8], "rr": 3.1, "pop_pct": 34.0,
           "iv_rank": 41.0, "composite_score": 76.0, "grade": "Good",
           "live": True, "stale_since": None}
    sig.update(over)
    return sig


def _day(swing=(), structures=(), zero=(), directional=()):
    return {"date": scanner.today_ct(), "signals_0dte": list(zero),
            "signals_swing": list(swing), "signals_directional": list(directional),
            "structures_0dte": [], "structures_swing": list(structures)}


# ── a credit spread in the shared columns ─────────────────────────────────────

def test_credit_spreads_are_a_family_and_lead_the_checkboxes():
    assert ssx.GROUPS[0] == (ssx.CREDIT_GROUP, "Credit spreads")
    assert ssx.CREDIT_GROUP in ssx.all_groups()


def test_a_put_credit_spread_reads_in_the_shared_columns():
    row = ssx.credit_rows([_pcs()])[0]
    assert row["id"] == _pcs()["id"] and row["symbol"] == "ORCL"
    assert row["type"] == "PCS"
    assert row["strategy_label"] == "Put Credit Spread" and row["bias"] == "bullish"
    assert row["legs"] == "S 100.00P / L 97.50P"          # the short leg leads
    assert row["expiration"] == "10/17" and row["dte"] == 12
    # Per CONTRACT, as every other row in the table is: 0.60 a share is $60.
    assert row["debit_credit"] == "+60.00 credit"
    assert row["max_profit"] == "60.00" and row["max_loss"] == "190.00"
    assert row["rr"] == "0.32"                              # 0.60 / 1.90, a ratio
    assert row["pop_pct"] == "72.00" and row["breakevens"] == "99.40"
    assert row["iv_rank"] == 55 and row["composite_score"] == 70
    assert row["grade"] == "Good"
    assert row["_group"] == ssx.CREDIT_GROUP and row["group_label"] == "Credit spreads"
    assert row["_undefined_risk"] is False and row["_earnings"] == ""


def test_a_call_credit_spread_is_bearish_calls():
    row = ssx.credit_rows([_pcs(type="CCS", short_strike=120.0, long_strike=122.5)])[0]
    assert row["strategy_label"] == "Call Credit Spread" and row["bias"] == "bearish"
    assert row["legs"] == "S 120.00C / L 122.50C"


def test_an_iron_condor_shows_both_wings_and_both_breakevens():
    row = ssx.credit_rows([_ic()])[0]
    assert row["strategy_label"] == "Iron Condor" and row["bias"] == "neutral"
    assert row["legs"] == "S 100.00P / L 97.50P / S 120.00C / L 122.50C"
    assert row["breakevens"] == "98.90 / 121.10"
    assert row["debit_credit"] == "+110.00 credit" and row["max_loss"] == "140.00"


def test_the_labels_are_the_engines_own():
    """A credit spread reaches the Strategy Finder through the engine's
    adapter, which names it. The same trade must not read as two different
    things on two pages."""
    engine = (REPO / "options-scanner" / "strategy_scanner.py").read_text(
        encoding="utf-8")
    for label in ("Put Credit Spread", "Call Credit Spread", "Iron Condor"):
        assert f'"{label}"' in engine, f"the engine no longer says {label!r}"
    assert {r["strategy_label"] for r in ssx.credit_rows(
        [_pcs(), _pcs(id="b", type="CCS"), _ic()])} == {
        "Put Credit Spread", "Call Credit Spread", "Iron Condor"}


@pytest.mark.parametrize("field", ["credit", "max_loss", "pop_pct", "breakeven",
                                   "short_strike", "long_strike", "iv_rank",
                                   "composite_score", "grade", "expiration"])
def test_a_missing_figure_is_a_dash_and_never_a_zero(field):
    sig = _pcs()
    del sig[field]
    row = ssx.credit_rows([sig])[0]                # must not raise
    blank = {"credit": ("debit_credit", "max_profit", "rr"),
             "max_loss": ("max_loss", "rr"), "pop_pct": ("pop_pct",),
             "breakeven": ("breakevens",), "short_strike": ("legs",),
             "long_strike": ()}.get(field, ())
    for cell in blank:
        assert row[cell] == "—", f"{cell} with no {field}: {row[cell]!r}"
    for cell in ("debit_credit", "max_profit", "max_loss", "rr"):
        assert not str(row[cell]).startswith(("0.00", "+0.00")), row[cell]


def test_junk_figures_do_not_raise():
    row = ssx.credit_rows([_pcs(credit="x", max_loss=float("nan"), rr_pct=None,
                                breakeven="junk", pop_pct=True)])[0]
    assert (row["debit_credit"], row["max_loss"], row["rr"], row["breakevens"],
            row["pop_pct"]) == ("—", "—", "—", "—", "—")
    assert ssx.credit_rows(None) == [] and ssx.credit_rows([]) == []


def test_credit_rows_come_back_best_first():
    rows = ssx.credit_rows([_pcs(id="a", composite_score=55),
                            _pcs(id="b", composite_score=None),
                            _pcs(id="c", composite_score=81)])
    assert [r["id"] for r in rows] == ["c", "a", "b"]


def test_a_live_credit_spread_may_be_papered_and_a_dropped_one_may_not():
    """``stamp_stale`` only ever NARROWS the gate, so it has to start open."""
    live, gone = _pcs(id="a"), _pcs(id="b", live=False,
                                    stale_since="2026-10-07T10:15:00-05:00")
    rows = scanner.stamp_stale(ssx.credit_rows([live, gone]), [live, gone])
    assert {r["id"]: r["_allow_paper"] for r in rows} == {"a": True, "b": False}


# ── the two scales, said out loud ─────────────────────────────────────────────

def test_every_score_names_the_scale_it_is_on():
    credit = ssx.credit_rows([_pcs()])[0]
    other = ssx.structure_rows([_fly()])[0]
    assert credit["_score_tip"] == ssx.SCORE_TIP_CREDIT
    assert other["_score_tip"] == ssx.SCORE_TIP_OTHER
    assert ssx.SCORE_TIP_CREDIT != ssx.SCORE_TIP_OTHER


def test_the_note_says_the_two_scores_are_not_comparable():
    note = ssx.SCALE_NOTE.lower()
    assert "credit spread" in note and "different scale" in note


def test_the_score_cell_shows_its_tip():
    assert "_score_tip" in scanner._SCORE_SLOT and "q-tooltip" in scanner._SCORE_SLOT


# ── one ranking ──────────────────────────────────────────────────────────────

def test_merged_rows_are_one_ranking_by_score():
    a = [{"id": "a", "composite_score": 91}, {"id": "b", "composite_score": 60}]
    b = [{"id": "c", "composite_score": 76.5}, {"id": "d", "composite_score": None}]
    merged = ssx.merged(a, b)
    assert [r["id"] for r in merged] == ["a", "c", "b", "d"]
    assert merged[0] is a[0]                       # the SAME rows, not copies
    assert ssx.merged() == [] and ssx.merged([], None) == []


def test_a_tie_keeps_the_credit_spread_first():
    """Stable, and the credit list is handed in first: an order that flipped
    between repaints would move rows under the reader."""
    a = [{"id": "credit", "composite_score": 70}]
    b = [{"id": "other", "composite_score": 70.0}]
    assert [r["id"] for r in ssx.merged(a, b)] == ["credit", "other"]


# ── the build ────────────────────────────────────────────────────────────────

def test_the_build_hands_each_tab_one_list():
    built = scanner._build_populate(
        _day(swing=[_pcs()], structures=[_fly()]), {}, None)
    assert set(built["tables"]) == {"0-DTE", "Swing", "Directional"}
    swing = built["tables"]["Swing"]
    assert [r["id"] for r in swing] == [_fly()["id"], _pcs()["id"]]      # 76 > 70
    assert {r["_group"] for r in swing} == {ssx.CREDIT_GROUP, "BUTTERFLY"}
    assert built["tables"]["0-DTE"] == [] and built["tables"]["Directional"] == []


def test_a_tabs_rows_are_the_same_rows_its_lists_hold():
    """The lifecycle and checklist stamps are written per list. A table built
    from COPIES would show none of them."""
    built = scanner._build_populate(
        _day(swing=[_pcs()], structures=[_fly()]), {}, None)
    by_id = {r["id"]: r for r in built["tables"]["Swing"]}
    assert by_id[_pcs()["id"]] is built["rows"]["signals_swing"][0]
    assert by_id[_fly()["id"]] is built["rows"]["structures_swing"][0]


def test_a_tabs_signals_are_both_of_its_lists():
    """What a re-stamp joins the table's rows back to, by id."""
    built = scanner._build_populate(
        _day(swing=[_pcs()], structures=[_fly()]), {}, None)
    assert {s["id"] for s in built["table_sigs"]["Swing"]} == {
        _pcs()["id"], _fly()["id"]}
    assert built["table_sigs"]["Directional"] == []


def test_a_merged_row_is_stamped_like_any_other():
    gone = _pcs(live=False, stale_since="2026-10-07T10:15:00-05:00")
    row = scanner._build_populate(_day(swing=[gone]), {}, None)["tables"]["Swing"][0]
    assert row["_stale"] is True and row["_allow_paper"] is False
    assert row["stale_since"] and "_checks_state" in row and "seen_since" in row


def test_every_family_checkbox_can_hide_its_rows():
    built = scanner._build_populate(
        _day(swing=[_pcs()], structures=[_fly()]), {}, None)
    rows = built["tables"]["Swing"]
    only_other = ssx.filter_groups(rows, ssx.all_groups() - {ssx.CREDIT_GROUP})
    assert [r["id"] for r in only_other] == [_fly()["id"]]
    assert [c["text"] for c in ssx.chips(rows)] == [
        "Credit spreads · 1", "Butterflies and condors · 1"]


# ── the public origin keeps what it keeps ────────────────────────────────────

@pytest.fixture
def published():
    import live_screens
    import shell
    bus_client.reset()
    scanner_shared.reset()
    scanner._reset_shared_reads()
    for memo in checks_feed._memos.values():
        memo.clear()
    shell.publish(live_screens.PUBLIC_ROUTES)
    yield
    shell.unpublish()
    bus_client.reset()
    scanner_shared.reset()
    scanner._reset_shared_reads()
    for memo in checks_feed._memos.values():
        memo.clear()


def test_the_shared_build_carries_the_merged_tables(published):
    bus = bus_client.bus()
    bus.cache_set("cache:options:scan_day", _day(swing=[_pcs()], structures=[_fly()]))
    bus.cache_set("cache:options:scan", {"timestamp": "x"})
    first = scanner._read_and_build_shared()
    assert [r["id"] for r in first["tables"]["Swing"]] == [_fly()["id"], _pcs()["id"]]
    assert all(r["_allow_paper"] is False for r in first["tables"]["Swing"])
    assert scanner._read_and_build_shared()["tables"] is first["tables"]


# ── the page ─────────────────────────────────────────────────────────────────

def _render():
    from nicegui import ui
    bus_client.reset()
    with ui.column() as box:
        scanner.render()
    return list(box.descendants())


def _count(elements, kind):
    return sum(type(e).__name__ == kind for e in elements)


def test_each_tab_holds_one_table_and_there_is_no_switch():
    elements = _render()
    assert _count(elements, "Table") == 3
    assert _count(elements, "Toggle") == 0
    # The two family-filtered tabs carry a checkbox per family; Directional
    # has one kind of row and no filter.
    assert _count(elements, "Checkbox") == 2 * len(ssx.GROUPS)


def test_the_page_prints_the_note_once_per_filtered_tab():
    from nicegui import ui
    texts = [str(e.text) for e in _render() if isinstance(e, ui.label)]
    assert texts.count(ssx.SCALE_NOTE) == 2


def test_the_checkboxes_name_every_family_credit_spreads_first():
    labels = [str(e.text) for e in _render() if type(e).__name__ == "Checkbox"]
    want = [label for _group, label in ssx.GROUPS]
    assert labels == want + want and want[0] == "Credit spreads"
