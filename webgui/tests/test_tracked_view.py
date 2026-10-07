"""``pages/options/tracked_view`` - the Captured page's tracked section, pure.

The store's numbers are per share and signed (a debit is a negative credit, a
position worth money has a negative cost to close). What is pinned here is that
no cell leaves that sign for the reader to work out.
"""
from pages.options import tracked_view as tv
from pages.options.theme import BADGE_MUTED, BADGE_WARN, TXT_NEG, TXT_POS

FRONT, BACK = "2026-10-16", "2026-11-13"


def _open(**over):
    row = {"signal_id": "s1", "symbol": "SPY", "strategy": "LONG_STRADDLE",
           "family": "STRADDLE", "scanner_type": "SWING_STRUCT", "expiration": FRONT,
           "dte_at_entry": 9, "entry_credit": -5.40, "entry_max_loss": 5.426,
           "entry_max_profit": None, "entry_score": 60.0, "entry_grade": "Good",
           "unbounded": 0, "entry_spans_earnings": 0,
           "first_seen_ts": "2026-10-07T10:00:00-05:00", "current_value": -5.60,
           "unrealized_pnl": 20.0, "recommendation": "HOLD",
           "recommendation_reason": "", "last_mark_ts": "2026-10-07T10:25:00-05:00",
           "legs": [{"kind": "call", "side": "long", "strike": 500.0,
                     "expiration": FRONT, "qty": 1},
                    {"kind": "put", "side": "long", "strike": 500.0,
                     "expiration": FRONT, "qty": 1}]}
    row.update(over)
    return row


# ── labels ──────────────────────────────────────────────────────────────────

def test_every_scanner_code_reads_as_whole_words():
    assert tv.label("LONG_STRADDLE") == "Long straddle"
    assert tv.label("bull_call") == "Bull call spread"
    assert tv.label("CALENDAR_PUT") == "Put calendar"
    assert tv.label("CALL_BACKSPREAD") == "Call backspread"


def test_an_unknown_code_is_shown_in_its_own_words_never_hidden():
    assert tv.label("BROKEN_WING_FLY") == "Broken wing fly"
    assert tv.label(None) == "" and tv.label("  ") == ""


# ── the signed per-share figures ────────────────────────────────────────────

def test_a_debit_is_paid_and_a_credit_is_received():
    assert tv.entry_text(-5.40) == "5.40 paid"
    assert tv.entry_text(1.35) == "1.35 received"
    assert tv.entry_text(None) == "—" and tv.entry_text(float("nan")) == "—"


def test_a_position_worth_money_is_sold_and_a_short_one_is_bought_back():
    assert tv.now_text(-5.60) == "5.60 to sell"
    assert tv.now_text(1.40) == "1.40 to buy back"
    assert tv.now_text(None) == "—"


def test_max_loss_is_dollars_per_contract_or_says_it_is_not_capped():
    assert tv.risk_text({"entry_max_loss": 5.426}) == "$542.60"
    assert tv.risk_text({"entry_max_loss": 20.0, "unbounded": 1}) == "Not capped"
    assert tv.risk_text({}) == "—" and tv.risk_text(None) == "—"


# ── open rows ───────────────────────────────────────────────────────────────

def test_an_open_row_carries_words_for_every_signed_figure():
    (row,) = tv.open_rows({"open": [_open()]})
    assert row["id"] == "s1" and row["symbol"] == "SPY"
    assert row["structure"] == "Long straddle"
    assert row["legs"] == "L 500.00C / L 500.00P"
    assert row["opened"] == "2026-10-07 10:00" and row["expiry"] == "10/16"
    assert row["entry"] == "5.40 paid" and row["now"] == "5.60 to sell"
    assert row["max_loss"] == "$542.60"
    assert row["unrealized_pnl"] == 20.0 and row["_pnl_class"] == TXT_POS
    assert row["_pnl_text"] == "+$20.00"
    assert row["status"] == "Holding" and row["notes"] == []


def test_a_calendar_shows_its_back_month_in_the_legs():
    (row,) = tv.open_rows({"open": [_open(strategy="CALENDAR_PUT", legs=[
        {"kind": "put", "side": "short", "strike": 500.0, "expiration": FRONT, "qty": 1},
        {"kind": "put", "side": "long", "strike": 500.0, "expiration": BACK, "qty": 1}])]})
    assert row["legs"] == "S 500.00P / L 500.00P 11/13"
    assert row["expiry"] == "10/16"


def test_a_row_never_marked_says_so_and_is_not_coloured():
    (row,) = tv.open_rows({"open": [_open(current_value=None, unrealized_pnl=None,
                                          recommendation=None)]})
    assert row["now"] == "—" and row["unrealized_pnl"] is None
    assert row["_pnl_text"] == "—"
    assert row["status"] == "Not marked yet" and row["_pnl_class"] == ""


def test_a_losing_row_is_red_and_a_flat_one_is_plain():
    rows = tv.open_rows({"open": [_open(signal_id="a", unrealized_pnl=-35.0),
                                  _open(signal_id="b", unrealized_pnl=0.0)]})
    by = {r["id"]: r for r in rows}
    assert by["a"]["_pnl_class"] == TXT_NEG and by["b"]["_pnl_class"] == ""


def test_the_uncapped_and_earnings_notes():
    (row,) = tv.open_rows({"open": [_open(strategy="SHORT_STRANGLE", entry_credit=1.35,
                                          current_value=1.40, unbounded=1,
                                          entry_spans_earnings=1)]})
    assert row["max_loss"] == "Not capped"
    assert row["notes"] == ["Loss not capped", "Open through earnings"]
    assert row["entry"] == "1.35 received" and row["now"] == "1.40 to buy back"


def test_open_rows_are_newest_first_and_skip_junk():
    rows = tv.open_rows({"open": [
        _open(signal_id="old", first_seen_ts="2026-10-06T09:00:00-05:00"),
        None, "junk", {"symbol": "no id"},
        _open(signal_id="new", first_seen_ts="2026-10-07T11:00:00-05:00")]})
    assert [r["id"] for r in rows] == ["new", "old"]
    assert tv.open_rows(None) == [] and tv.open_rows({}) == []


def test_the_manage_loops_codes_are_put_in_words():
    codes = {"TARGET_HIT": "Target reached", "MONEY_STOP": "Stopped",
             "FRONT_EXPIRY": "Front leg expiring", "HOLD": "Holding"}
    for code, words in codes.items():
        (row,) = tv.open_rows({"open": [_open(recommendation=code)]})
        assert row["status"] == words


# ── results ─────────────────────────────────────────────────────────────────

def _stat(**over):
    s = {"strategy": "LONG_STRADDLE", "family": "STRADDLE", "n": 4, "wins": 3,
         "total_pnl": 412.5, "unmarkable": 0, "unbounded": False,
         "through_earnings": 0, "win_pct": 75.0, "avg_r": 0.19}
    s.update(over)
    return s


def test_a_result_row_keeps_numbers_numeric_so_the_columns_sort():
    (row,) = tv.result_rows({"stats": [_stat()]})
    assert row == {"id": "LONG_STRADDLE", "structure": "Long straddle", "n": 4,
                   "win_pct": 75.0, "total_pnl": 412.5, "avg_r": 0.19,
                   "unmarkable": 0, "notes": [], "_pnl_class": TXT_POS,
                   "_pnl_text": "+$412.50", "_win_text": "75.00%",
                   "_r_text": "+19.00%"}


def test_a_structure_with_no_valued_close_shows_no_rate_and_no_zero():
    (row,) = tv.result_rows({"stats": [_stat(strategy="CALENDAR_PUT", n=0, wins=0,
                                             total_pnl=0.0, unmarkable=2,
                                             win_pct=None, avg_r=None)]})
    assert row["n"] == 0 and row["unmarkable"] == 2
    assert row["win_pct"] is None and row["avg_r"] is None
    assert row["_pnl_class"] == ""
    assert row["_win_text"] == "—" and row["_r_text"] == "—"


def test_result_notes_say_when_the_return_on_risk_is_an_estimate():
    (row,) = tv.result_rows({"stats": [_stat(strategy="SHORT_STRANGLE", unbounded=True,
                                             through_earnings=2, total_pnl=-80.0)]})
    assert row["notes"] == ["Loss not capped: return on risk uses a margin estimate",
                            "2 open through earnings"]
    assert row["_pnl_class"] == TXT_NEG


def test_result_rows_skip_junk():
    assert tv.result_rows({"stats": [None, "x", {"n": 3}]}) == []
    assert tv.result_rows(None) == []


def test_the_decimal_columns_are_the_ones_that_are_money_or_ratios():
    names = {c["name"] for c in tv.result_columns()}
    assert set(tv.RESULT_NUMERIC) <= names
    assert set(tv.OPEN_NUMERIC) <= {c["name"] for c in tv.open_columns()}
    assert all(c["sortable"] is (c["name"] != "notes")
               for c in tv.open_columns() + tv.result_columns())


# ── today's closes and the count line ───────────────────────────────────────

def test_todays_closes_read_as_sentences_in_the_order_they_closed():
    lines = tv.closed_today_lines({"closed_today": [
        {"symbol": "IWM", "strategy": "CALENDAR_PUT", "exit_reason": "UNMARKABLE",
         "realized_pnl": None, "close_ts": "2026-10-16T15:10:00-05:00"},
        {"symbol": "SPY", "strategy": "LONG_STRADDLE", "exit_reason": "TARGET_HIT",
         "realized_pnl": 272.5, "close_ts": "2026-10-16T10:25:00-05:00"},
        {"symbol": "QQQ", "strategy": "SHORT_STRANGLE", "exit_reason": "MONEY_STOP",
         "realized_pnl": -277.5, "close_ts": "2026-10-16T11:40:00-05:00"}, None]})
    assert lines == [
        "SPY Long straddle: target reached, +$272.50",
        "QQQ Short strangle: stopped, -$277.50",
        "IWM Put calendar: could not be valued, no result"]
    assert tv.closed_today_lines(None) == []


def test_the_count_line():
    assert tv.status_text({"counts": {"open": 3, "closed": 12, "closed_today": 2}}) \
        == "3 open · 12 closed · 2 closed today"
    assert tv.status_text({}) == "" and tv.status_text(None) == ""


def test_notes_become_badges_with_a_fixed_class_each():
    rows = tv.with_note_classes([{"notes": ["Loss not capped", "Open through earnings"]},
                                 {"notes": []}, {}])
    assert rows[0]["notes"] == [{"text": "Loss not capped", "cls": BADGE_WARN},
                                {"text": "Open through earnings", "cls": BADGE_MUTED}]
    assert rows[1]["notes"] == [] and rows[2]["notes"] == []
