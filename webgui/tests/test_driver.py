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


def test_render_is_callable():
    assert callable(driver.render)
