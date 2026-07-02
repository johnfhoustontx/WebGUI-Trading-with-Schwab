"""Tests for the Driver page pure display builders + render API.

The orchestration (morning pipeline, order execution, perf aggregation) lives in
``services/driver_svc``; this page is a Tier-3 reader, so only its pure
transforms (grade coloring, status text, condition/trade/perf rows) and the
``render`` callable are exercised here.
"""
from pages import driver


def test_grade_color():
    assert driver.grade_color("A") == driver.GRADE_COLORS["A"]
    assert driver.grade_color("b") == driver.GRADE_COLORS["B"]
    assert driver.grade_color("X") == driver.GRADE_COLORS["X"]
    assert driver.grade_color("?") == driver.GRADE_NEUTRAL  # unknown -> grey


def test_is_pending():
    assert driver.is_pending({"status": "pending"}) is True
    assert driver.is_pending({"status": "approved"}) is False
    assert driver.is_pending({"status": "no_trade"}) is False
    assert driver.is_pending(None) is False
    assert driver.is_pending({}) is False


def test_status_text_variants():
    assert "Run the morning agent" in driver.status_text(None)
    assert driver.status_text({"status": "pending", "grade": "B",
                               "proposed_trades": [1, 2]}).startswith("Grade B")
    assert "2 proposed" in driver.status_text({"status": "pending", "grade": "B",
                                               "proposed_trades": [1, 2]})
    assert "Approved" in driver.status_text({"status": "approved",
                                             "proposed_trades": [1]})
    assert "Skipped" in driver.status_text({"status": "skipped"})
    assert "No trade" in driver.status_text({"status": "no_trade",
                                             "reasons": ["VIX too high"]})
    assert "error" in driver.status_text({"status": "error",
                                          "error": "proxy down"}).lower()


def test_condition_rows_formats_and_handles_missing():
    rows = dict(driver.condition_rows(
        {"vix": 16.2, "spx_spot": 5400.5, "vix1d": 14.1}, 25.0, -50.0))
    assert rows["VIX"] == "16.2"
    assert rows["SPX"] == "5,400.50"
    assert rows["VIX1D"] == "14.1"
    assert rows["P&L today"] == "+$25.00"
    assert rows["P&L week"] == "-$50.00"


def test_condition_rows_missing_is_dash():
    rows = dict(driver.condition_rows({}, None, None))
    assert rows["VIX"] == "—"
    assert rows["SPX"] == "—"
    assert rows["P&L today"] == "—"


def test_proposed_trade_lines_bucket_a():
    lines = driver.proposed_trade_lines({
        "bucket": "A", "instrument": "SPX", "structure": "put_credit_spread",
        "strikes": {"short": 5350, "long": 5340}, "contracts": 1,
        "max_risk": 300.0, "notes": "SPX 0-DTE pcs",
        "ml_signal": "Bullish", "ml_confidence": 72.0,
    })
    blob = " | ".join(lines)
    assert "Bucket A" in blob
    assert "Put Credit Spread" in blob  # structure title-cased, underscores spaced
    assert "short" in blob and "5350" in blob  # strike detail
    assert "300" in blob  # max risk
    assert "SPX 0-DTE pcs" in blob  # notes
    assert "Bullish" in blob  # ml signal


def test_proposed_trade_lines_bucket_b_equity():
    lines = driver.proposed_trade_lines({
        "bucket": "B", "instrument": "QQQ", "side": "BUY", "max_risk": 150.0,
        "notes": "momentum long",
    })
    blob = " | ".join(lines)
    assert "Bucket B" in blob
    assert "QQQ" in blob and "BUY" in blob
    assert "150" in blob


def test_perf_summary_text():
    txt = driver.perf_summary_text({"total_trades": 5, "wins": 3, "losses": 2,
                                    "win_rate": 60.0, "realized_pnl": 125.5})
    assert "5" in txt and "60.0%" in txt and "3-2" in txt and "125.5" in txt


def test_perf_summary_text_empty():
    assert "No trades" in driver.perf_summary_text({})
    assert "No trades" in driver.perf_summary_text(None)


def test_perf_rows():
    rows = driver.perf_rows([
        {"trade_id": "2026-06-16-A-1", "date": "2026-06-16", "bucket": "A",
         "instrument": "SPX", "side": "SELL_TO_OPEN", "status": "closed",
         "source": "streamed", "pnl": 100.0},
    ])
    assert rows[0]["bucket"] == "A"
    assert rows[0]["pnl"] == "+$100.00"
    assert rows[0]["trade_id"] == "2026-06-16-A-1"


def test_perf_rows_carry_pnl_color():
    rows = driver.perf_rows([
        {"trade_id": "w", "pnl": 100.0},
        {"trade_id": "l", "pnl": -50.0},
        {"trade_id": "z", "pnl": 0.0},
    ])
    by_id = {r["trade_id"]: r for r in rows}
    assert by_id["w"]["_pnl_color"] == "#66bb6a"
    assert by_id["l"]["_pnl_color"] == "#ef5350"
    assert by_id["z"]["_pnl_color"] == "#bdbdbd"


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


def test_grade_bg_class():
    assert driver.grade_bg_class("A") == "bg-[#1D9E75]"
    assert driver.grade_bg_class("X") == "bg-[#E24B4A]"
    assert driver.grade_bg_class("?") == "bg-[#888888]"   # fallback -> neutral


def test_perf_rows_carry_pnl_class():
    rows = driver.perf_rows([
        {"trade_id": "w", "pnl": 100.0},
        {"trade_id": "z", "pnl": 0.0},
    ])
    by_id = {r["trade_id"]: r for r in rows}
    assert by_id["w"]["_pnl_class"] == "text-[#66bb6a]"
    assert by_id["z"]["_pnl_class"] == "text-[#bdbdbd]"


def test_position_rows_carry_pnl_class():
    rows = driver.position_rows([{"position_id": "p1", "unrealized_pnl": -12.0}])
    assert rows[0]["_pnl_class"] == "text-[#ef5350]"


def test_perf_cols_use_full_words():
    labels = {c["field"]: c["label"] for c in driver._PERF_COLS}
    assert labels["bucket"] == "Bucket"
    assert labels["instrument"] == "Instrument"


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
