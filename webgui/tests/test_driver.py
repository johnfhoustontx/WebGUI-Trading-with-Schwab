"""Tests for the Driver page pure display builders + render API.

The orchestration (autonomous decision layer, order execution, perf aggregation)
lives in ``services/driver_svc`` / ``services/options_svc``; this page is a Tier-3
reader, so only its pure transforms (P&L coloring, closed-trade rows, the
autonomous-monitor builders) and the ``render`` callable are exercised here.
"""
from pages import driver


def test_pnl_color():
    assert driver.pnl_color(5) == "#66bb6a"
    assert driver.pnl_color(-5) == "#ef5350"
    assert driver.pnl_color(0) == "#bdbdbd"
    assert driver.pnl_color(None) == "#bdbdbd"


def test_pnl_class_maps_sign():
    assert driver.pnl_class(5) == "text-[#66bb6a]"
    assert driver.pnl_class(-5) == "text-[#ef5350]"
    assert driver.pnl_class(0) == "text-[#bdbdbd]"
    assert driver.pnl_class(None) == "text-[#bdbdbd]"


def test_position_rows_carry_pnl_class():
    rows = driver.position_rows([{"position_id": "p1", "unrealized_pnl": -12.0}])
    assert rows[0]["_pnl_class"] == "text-[#ef5350]"


# ── driver realized-performance (closed trades from the isolated paper account) ──
def _closed(symbol="RKLB", strategy="PCS", pnl=15.0, exit_ts="2026-07-02T11:00:00-05:00",
            reason="TARGET_HIT", qty=2, pid=7):
    return {"position_id": pid, "symbol": symbol, "strategy": strategy, "quantity": qty,
            "entry_credit": 0.33, "realized_pnl": pnl, "exit_reason": reason, "exit_ts": exit_ts,
            "status": "CLOSED"}


def test_closed_summary_text_computes_realized_and_winrate():
    txt = driver.closed_summary_text([
        _closed(pnl=120.0), _closed(pnl=-60.0), _closed(pnl=40.0)])
    assert "Closed: 3" in txt
    assert "2W" in txt and "1L" in txt and "67% win" in txt   # 2 of 3 winners
    assert "+$100.00" in txt                                   # 120 - 60 + 40


def test_closed_summary_text_empty_is_friendly():
    for empty in ([], None, [{"status": "CLOSED"}]):          # no realized_pnl → friendly note
        assert "No closed trades yet" in driver.closed_summary_text(empty)


def test_closed_trade_rows_newest_first_and_reader_friendly():
    rows = driver.closed_trade_rows([
        _closed(symbol="MU", exit_ts="2026-07-01T09:30:00-05:00", pnl=-18.0, reason="MONEY_STOP"),
        _closed(symbol="SPY", exit_ts="2026-07-02T13:00:00-05:00", pnl=16.4, reason="TARGET_HIT"),
    ])
    assert [r["symbol"] for r in rows] == ["SPY", "MU"]        # newest (07-02) first
    assert rows[0]["reason"] == "Target hit" and rows[1]["reason"] == "Money stop"  # humanized
    assert rows[0]["closed"] == "2026-07-02 13:00"            # compact date+time
    assert rows[0]["pnl"] == "+$16.40" and rows[1]["pnl"] == "-$18.00"
    assert rows[0]["_pnl_class"] and rows[1]["_pnl_class"]    # colored


def test_closed_trade_rows_tolerates_junk():
    rows = driver.closed_trade_rows([None, {}, _closed()])    # None dropped; {} tolerated
    assert len(rows) == 2                                      # None filtered out, no crash
    assert rows[0]["symbol"] == "RKLB" and rows[-1]["symbol"] == ""


def test_closed_cols_are_clean_reader_friendly_set():
    labels = [c["label"] for c in driver._CLOSED_COLS]
    assert labels == ["Closed", "Symbol", "Strategy", "Qty", "Exit reason", "Realized P&L"]
    # the useless legacy columns are gone
    assert "Bucket" not in labels and "Source" not in labels and "Status" not in labels


def test_current_day_decisions_keeps_only_today():
    import datetime as dt
    today = dt.date(2026, 6, 26)
    decisions = [
        {"ts": "2026-06-26T18:00:00+00:00", "thesis": "today-a"},   # 13:00 CT 06-26
        {"ts": "2026-06-25T18:00:00+00:00", "thesis": "yesterday"},  # 06-25
        {"ts": "2026-06-26T02:00:00+00:00", "thesis": "today-early"},  # 21:00 CT 06-25 -> 06-25!
        {"thesis": "no-ts"},
    ]
    kept = driver.current_day_decisions(decisions, today_ct=today)
    theses = [d["thesis"] for d in kept]
    assert "today-a" in theses
    assert "yesterday" not in theses
    assert "no-ts" not in theses          # undateable rows are dropped
    # 02:00 UTC on 06-26 is 21:00 CT on 06-25 — NOT today.
    assert "today-early" not in theses


def test_position_rows_carry_pnl_color():
    rows = driver.position_rows([{"position_id": "p1", "unrealized_pnl": -12.0}])
    assert rows[0]["_pnl_color"] == "#ef5350"


def test_pnl_slot_binds_pnl_class():
    # the P&L cell slot must reference the stamped field, so a future rename of
    # _pnl_class can't silently break the :class binding while row tests stay green.
    assert "_pnl_class" in driver._PNL_CELL_SLOT


# ── R7: stand-down reason observability ─────────────────────────────────────
def test_decision_log_rows_carry_reason():
    """The reason (from the decider) is threaded through onto each log row so the
    page can distinguish an ops-incident stand-down (no_key/api_error) from a
    genuine model stand-down. Missing reason → None (back-compat, renders as today)."""
    rows = driver.decision_log_rows([
        {"ts": "t1", "stand_down": True, "reason": "no_key"},
        {"ts": "t2", "stand_down": True, "reason": "model"},
        {"ts": "t3", "stand_down": True},                       # legacy row, no reason
    ])
    assert rows[0]["reason"] == "no_key"
    assert rows[1]["reason"] == "model"
    assert rows[2]["reason"] is None                            # absent → None


def test_stand_down_reason_label_non_model():
    """A non-model reason gets a short, distinct human tag for the log entry."""
    assert driver.stand_down_reason_label("no_key") == "NO API KEY"
    assert driver.stand_down_reason_label("api_error") == "API ERROR"
    assert driver.stand_down_reason_label("parse_error") == "BAD REPLY"


def test_stand_down_reason_label_model_or_absent_is_none():
    """A genuine model stand-down (or a missing/unknown reason) → no tag: it must
    render exactly as today (back-compat), NOT flag a normal decision as an incident."""
    assert driver.stand_down_reason_label("model") is None
    assert driver.stand_down_reason_label(None) is None
    assert driver.stand_down_reason_label("") is None
    assert driver.stand_down_reason_label("something_new") is None   # unknown → no tag


def test_decision_summary_flags_incident_stand_down():
    """The one-line summary appends the incident tag for a non-model stand-down so
    a no_key/api_error is visible even where the badge isn't rendered."""
    row = driver.decision_log_rows([
        {"ts": "t", "stand_down": True, "reason": "api_error"}])[0]
    summary = driver.decision_summary(row)
    assert "API ERROR" in summary


def test_decision_summary_model_stand_down_unchanged():
    """A genuine model stand-down summary is unchanged (no incident tag) — back-compat."""
    row = driver.decision_log_rows([
        {"ts": "t", "stand_down": True, "reason": "model"}])[0]
    assert driver.decision_summary(row) == "Stood down — no trades"
    # a legacy row with no reason at all is identical
    legacy = driver.decision_log_rows([{"ts": "t", "stand_down": True}])[0]
    assert driver.decision_summary(legacy) == "Stood down — no trades"


def test_render_is_callable():
    assert callable(driver.render)
