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
            reason="TARGET_HIT", qty=2, pid=7, entry_ts="2026-07-01T10:15:00-05:00"):
    return {"position_id": pid, "symbol": symbol, "strategy": strategy, "quantity": qty,
            "entry_credit": 0.33, "realized_pnl": pnl, "exit_reason": reason, "exit_ts": exit_ts,
            "entry_ts": entry_ts, "status": "CLOSED"}


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
    assert labels == ["Opened", "Closed", "Symbol", "Strategy", "Qty", "Exit reason",
                      "Realized P&L"]
    # the useless legacy columns are gone
    assert "Bucket" not in labels and "Source" not in labels and "Status" not in labels


def test_closed_trade_rows_carry_opened_datetime():
    """Each closed trade shows WHEN it was opened as well as closed (date + time)."""
    rows = driver.closed_trade_rows([
        _closed(entry_ts="2026-07-01T10:15:00-05:00", exit_ts="2026-07-02T13:00:00-05:00")])
    assert rows[0]["opened"] == "2026-07-01 10:15"
    assert rows[0]["closed"] == "2026-07-02 13:00"


def test_closed_trade_rows_missing_entry_ts_is_dash():
    rows = driver.closed_trade_rows([{"symbol": "X", "exit_ts": "2026-07-02T13:00:00-05:00"}])
    assert rows[0]["opened"] == "—"


# ── open positions: opened time + strikes + expiration ───────────────────────
def _open_pos(strategy="CCS", short_k=7650.0, long_k=7660.0, call_short=None, call_long=None):
    return {"position_id": 21, "symbol": "$SPX", "strategy": strategy, "quantity": 3,
            "unrealized_pnl": -126.0, "status": "OPEN",
            "entry_ts": "2026-07-09T09:30:00-05:00", "expiration": "2026-07-24",
            "short_strike": short_k, "long_strike": long_k,
            "call_short": call_short, "call_long": call_long}


def test_strikes_text_ccs_and_pcs():
    # CCS: short lower / long higher CALLS; PCS: short higher / long lower PUTS.
    assert driver._strikes_text(_open_pos("CCS", 7650.0, 7660.0)) == "7650/7660 C"
    assert driver._strikes_text(_open_pos("PCS", 165.0, 160.0)) == "165/160 P"


def test_strikes_text_iron_condor_shows_both_wings():
    ic = _open_pos("IC", 165.0, 160.0, call_short=185.0, call_long=190.0)
    assert driver._strikes_text(ic) == "165/160 P · 185/190 C"


def test_strikes_text_unknown_is_dash():
    for p in ({}, {"strategy": "CCS"}, {"strategy": "CCS", "short_strike": 100.0}):
        assert driver._strikes_text(p) == "—"


def test_position_rows_carry_opened_strikes_expiration():
    rows = driver.position_rows([_open_pos()])
    assert rows[0]["opened"] == "2026-07-09 09:30"
    assert rows[0]["strikes"] == "7650/7660 C"
    assert rows[0]["expiration"] == "2026-07-24"


def test_position_cols_include_opened_strikes_expiration():
    labels = [c["label"] for c in driver._POSITION_COLS]
    for expected in ("Opened", "Strikes", "Expiration"):
        assert expected in labels


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


def test_shadow_gate_line_shows_would_block_when_inert():
    """An inert gate that would have blocked a fired trade shows the evidence line."""
    row = driver.decision_log_rows([{"ts": "t", "shadow_gate": {
        "posture": "up", "enabled": False, "n": 1,
        "would_block": [{"id": "m0", "symbol": "SPY", "structure": "CCS"}]}}])[0]
    line = driver.shadow_gate_line(row)
    assert "would block 1" in line and "CCS SPY" in line and "up tape" in line


def test_shadow_gate_line_silent_when_live_or_empty_or_legacy():
    """No line when the gate is live (already rejects), when nothing would block, or on
    a legacy row without a shadow_gate."""
    live = driver.decision_log_rows([{"ts": "t", "shadow_gate": {
        "posture": "up", "enabled": True, "n": 1,
        "would_block": [{"id": "m0", "symbol": "SPY", "structure": "CCS"}]}}])[0]
    empty = driver.decision_log_rows([{"ts": "t", "shadow_gate": {
        "posture": "neutral", "enabled": False, "n": 0, "would_block": []}}])[0]
    legacy = driver.decision_log_rows([{"ts": "t", "stand_down": True}])[0]
    assert driver.shadow_gate_line(live) == ""
    assert driver.shadow_gate_line(empty) == ""
    assert legacy["shadow_gate"] is None and driver.shadow_gate_line(legacy) == ""


def test_equity_curve_figure_maps_series():
    curve = [{"date": "2026-07-08", "equity": 25060.0, "realized": 60.0},
             {"date": "2026-07-09", "equity": 25110.0, "realized": 50.0}]
    fig = driver.equity_curve_figure(curve)
    assert fig["xAxis"]["categories"] == ["2026-07-08", "2026-07-09"]
    series = {s["name"]: s for s in fig["series"]}
    assert series["Equity"]["data"] == [25060.0, 25110.0]
    assert series["Daily P&L"]["data"] == [60.0, 50.0]


def test_equity_curve_figure_empty_is_valid():
    fig = driver.equity_curve_figure([])
    assert fig["xAxis"]["categories"] == []
    assert all(s["data"] == [] for s in fig["series"])


def test_postmortem_rows_and_headline():
    pm = {"by_stance": {
        "with": {"trades": 2, "wins": 2, "win_rate": 1.0, "realized": 160.0, "avg": 80.0},
        "against": {"trades": 3, "wins": 0, "win_rate": 0.0, "realized": -120.0, "avg": -40.0},
        "neutral": {"trades": 0, "wins": 0, "win_rate": 0.0, "realized": 0.0, "avg": 0.0}},
        "edge": {"with_avg": 80.0, "against_avg": -40.0, "avg_delta": 120.0,
                 "n_with": 2, "n_against": 3}}
    rows = driver.postmortem_rows(pm)
    # neutral has 0 trades → omitted; with + against present
    labels = [r["stance"] for r in rows]
    assert labels == ["With tape", "Against tape"]
    assert rows[0]["pnl"] == "+$160" and rows[1]["pnl"] == "-$120"
    assert rows[0]["_pnl_class"] and rows[1]["_pnl_class"]
    head = driver.postmortem_headline(pm)
    assert "With the tape" in head and "+$80" in head and "-$40" in head and "edge +$120" in head


def test_postmortem_headline_empty_when_no_data():
    assert driver.postmortem_headline({"edge": {"n_with": 0, "n_against": 0}}) == ""
    assert driver.postmortem_headline({}) == ""


def test_excursion_text():
    ex = {"n": 5, "avg_mae": -30.0, "avg_mfe": 90.0, "mfe_capture": 0.75}
    txt = driver.excursion_text(ex)
    assert "peak +$90" in txt and "drawdown -$30" in txt and "0.75×" in txt and "5 closed" in txt
    assert driver.excursion_text({"n": 0}) == ""


def test_render_is_callable():
    assert callable(driver.render)


# ── the summary is a LIFETIME figure, the rows are capped (2026-08-20) ───────

def test_closed_summary_text_prefers_the_services_exact_totals():
    """options_svc now publishes only the newest DRIVER_CLOSED_LIMIT rows but
    computes `closed_totals` over EVERY closed trade. The summary line is a
    lifetime count / win-rate / realized total, so it must read the aggregate --
    counting the truncated rows would understate the driver's whole track record."""
    rows = [{"realized_pnl": 10.0}, {"realized_pnl": -4.0}]      # only 2 kept
    totals = {"count": 500, "wins": 300, "losses": 200,
              "realized": 1234.5, "truncated": True}
    txt = driver.closed_summary_text(rows, totals)
    assert "Closed: 500" in txt and "300W" in txt and "200L" in txt
    assert "60% win" in txt
    assert "1,234.50" in txt or "1234.50" in txt


def test_closed_summary_text_falls_back_to_the_rows_without_totals():
    """A snapshot published before the aggregate existed must still render."""
    txt = driver.closed_summary_text([{"realized_pnl": 10.0}, {"realized_pnl": -4.0}])
    assert "Closed: 2" in txt and "1W" in txt and "1L" in txt


def test_closed_summary_text_says_so_when_the_table_is_truncated():
    """No silent caps: if the table cannot show every trade the summary says how
    many it IS showing, rather than letting the reader assume the table is whole."""
    rows = [{"realized_pnl": 10.0}] * 400
    totals = {"count": 460, "wins": 460, "losses": 0,
              "realized": 4600.0, "truncated": True}
    txt = driver.closed_summary_text(rows, totals)
    assert "400" in txt and "showing" in txt.lower()


def test_closed_summary_text_is_silent_about_truncation_when_there_is_none():
    rows = [{"realized_pnl": 10.0}, {"realized_pnl": -4.0}]
    totals = {"count": 2, "wins": 1, "losses": 1, "realized": 6.0, "truncated": False}
    assert "showing" not in driver.closed_summary_text(rows, totals).lower()
