import json, sqlite3
from perf_report import build_report


def test_build_report_joins_and_summarizes(tmp_path):
    log = tmp_path / "trade_log.json"
    json.dump([
        {"trade_id": "d-A-1", "date": "2026-05-30", "bucket": "A",
         "instrument": "SPX", "side": "SELL_TO_OPEN", "success": True,
         "order_id": "1", "pnl": 75.0},
        {"trade_id": "d-B-1", "date": "2026-05-30", "bucket": "B",
         "instrument": "QQQ", "side": "BUY", "success": True,
         "order_id": "2", "pnl": -40.0},
    ], log.open("w"))

    db = tmp_path / "perf.db"
    con = sqlite3.connect(db)
    # Mirror the proxy's real perf_events schema (perf_writer.py), incl. event_id PK.
    con.execute("CREATE TABLE perf_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "trade_id TEXT, event_type TEXT, ts TEXT, underlying REAL, mid REAL, "
                "unrealized_pnl REAL, pnl_pct REAL, note TEXT)")
    # Two events for the same trade; the later (target_hit) is the terminal one.
    con.execute("INSERT INTO perf_events (trade_id, event_type, ts, underlying, mid, "
                "unrealized_pnl, pnl_pct, note) VALUES "
                "('d-A-1','strike_test','2026-05-30T12:00Z',5000,0.8,10.0,5.0,'')")
    con.execute("INSERT INTO perf_events (trade_id, event_type, ts, underlying, mid, "
                "unrealized_pnl, pnl_pct, note) VALUES "
                "('d-A-1','target_hit','2026-05-30T15:00Z',5000,1.2,75.0,50.0,'')")
    con.commit(); con.close()

    rep = build_report(log_path=str(log), perf_db=str(db))
    assert rep["summary"]["total_trades"] == 2
    assert rep["summary"]["wins"] == 1
    assert rep["summary"]["losses"] == 1
    assert rep["summary"]["win_rate"] == 50.0
    assert rep["summary"]["realized_pnl"] == 35.0
    by_a = next(t for t in rep["trades"] if t["trade_id"] == "d-A-1")
    assert by_a["source"] == "streamed"
    assert by_a["exit_reason"] == "target_hit"
    by_b = next(t for t in rep["trades"] if t["trade_id"] == "d-B-1")
    assert by_b["source"] == "polled"


def test_missing_files_return_empty_report(tmp_path):
    rep = build_report(log_path=str(tmp_path / "nope.json"),
                       perf_db=str(tmp_path / "nope.db"))
    assert rep["summary"]["total_trades"] == 0
    assert rep["summary"]["win_rate"] == 0.0
    assert rep["trades"] == []


def test_log_present_but_db_missing_uses_polled(tmp_path):
    log = tmp_path / "trade_log.json"
    json.dump([{"trade_id": "x", "date": "2026-05-30", "bucket": "B",
                "instrument": "QQQ", "side": "BUY", "order_id": "1", "pnl": 20.0}],
              log.open("w"))
    rep = build_report(log_path=str(log), perf_db=str(tmp_path / "nope.db"))
    assert rep["summary"]["total_trades"] == 1
    assert rep["summary"]["wins"] == 1
    assert rep["trades"][0]["source"] == "polled"
