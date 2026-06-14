from datetime import datetime
from zoneinfo import ZoneInfo
import signal_db
import signal_recorder
import signal_repricer
import eod_report

TZ = ZoneInfo("America/Chicago")


def _seed(db, score=60):
    sig = {
        "symbol": "SPY", "type": "PCS", "short_strike": 690, "long_strike": 688,
        "width": 2, "expiration": "2026-04-15", "dte": 0, "credit": 0.60,
        "max_loss": 1.40, "composite_score": score, "grade": "Marginal",
        "short_delta": -0.15, "net_theta": 5.0, "iv_rank": 30.0, "underlying_price": 700.0,
    }
    signal_recorder.record_signals([sig], "0DTE", db_path=db)


def test_report_file_written(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    _seed(db)
    out = tmp_path / "reports"
    monkeypatch.setattr(eod_report, "_get_underlying_close", lambda sym, client: 700.0)
    path = eod_report.generate(date_str="2026-04-15", db_path=db, reports_dir=out, client=None)
    assert path.exists()
    md = path.read_text()
    assert "# EOD Signal Report" in md
    assert "2026-04-15" in md
    assert "Hypothetical PnL" in md
    assert "Statistical Performance" in md


def test_zero_dte_auto_expires_on_report(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    _seed(db)
    monkeypatch.setattr(eod_report, "_get_underlying_close", lambda sym, client: 700.0)
    eod_report.generate(date_str="2026-04-15", db_path=db, reports_dir=tmp_path / "r", client=None)
    remaining_open = signal_db.get_open_signals(db_path=db)
    assert remaining_open == []


def test_rolling_7day_stats_included(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    import signal_db
    signal_db.init_db(db)
    import sqlite3
    c = sqlite3.connect(db)
    c.executescript("""
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('x1','0DTE','SPY','PCS','2026-04-14',0.5,60,'Marginal','k1','2026-04-14','EXPIRED');
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('x2','0DTE','QQQ','CCS','2026-04-15',0.4,55,'Marginal','k2','2026-04-15','EXPIRED');
      INSERT INTO signal_outcomes(signal_id, close_date, realized_pnl, exit_reason) VALUES('x1','2026-04-14',50,'EXPIRED');
      INSERT INTO signal_outcomes(signal_id, close_date, realized_pnl, exit_reason) VALUES('x2','2026-04-15',-30,'EXPIRED');
    """)
    c.commit(); c.close()

    monkeypatch.setattr(eod_report, "_get_underlying_close", lambda s, c: 700.0)
    path = eod_report.generate(date_str="2026-04-15", db_path=db, reports_dir=tmp_path / "r", client=None)
    md = path.read_text()
    assert "Rolling 7-Day" in md
    assert "$+20" in md or "$20" in md


def test_by_strategy_and_by_grade_rollups(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    import signal_db
    signal_db.init_db(db)
    import sqlite3
    c = sqlite3.connect(db)
    c.executescript("""
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('a1','0DTE','SPY','PCS','2026-04-15',0.5,70,'Strong','ka1','2026-04-15','EXPIRED');
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('a2','0DTE','SPY','PCS','2026-04-15',0.5,55,'Marginal','ka2','2026-04-15','EXPIRED');
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('a3','0DTE','QQQ','CCS','2026-04-15',0.4,65,'Strong','ka3','2026-04-15','EXPIRED');
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('a4','0DTE','IWM','IC','2026-04-15',0.6,50,'Marginal','ka4','2026-04-15','EXPIRED');
      INSERT INTO signal_outcomes(signal_id, close_date, realized_pnl, exit_reason) VALUES('a1','2026-04-15',40,'EXPIRED');
      INSERT INTO signal_outcomes(signal_id, close_date, realized_pnl, exit_reason) VALUES('a2','2026-04-15',-20,'EXPIRED');
      INSERT INTO signal_outcomes(signal_id, close_date, realized_pnl, exit_reason) VALUES('a3','2026-04-15',30,'EXPIRED');
      INSERT INTO signal_outcomes(signal_id, close_date, realized_pnl, exit_reason) VALUES('a4','2026-04-15',-10,'EXPIRED');
    """)
    c.commit(); c.close()

    monkeypatch.setattr(eod_report, "_get_underlying_close", lambda s, c: 700.0)
    path = eod_report.generate(date_str="2026-04-15", db_path=db, reports_dir=tmp_path / "r", client=None)
    md = path.read_text()
    assert "By strategy:" in md
    assert "By grade:" in md
    assert "PCS:" in md
    assert "CCS:" in md
    assert "IC:" in md
    assert "Strong:" in md
    assert "Marginal:" in md


def test_by_capture_date_cohort_rollup(tmp_path, monkeypatch):
    """Closed trades are broken down by capture date (first_seen_date) so a
    post-criteria-change cohort can be tracked apart from legacy carryover."""
    db = tmp_path / "s.db"
    signal_db.init_db(db)
    import sqlite3
    c = sqlite3.connect(db)
    c.executescript("""
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('old1','SWING','SPY','CCS','2026-06-15',0.5,55,'Marginal','kc1','2026-06-10','CLOSED');
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('new1','0DTE','QQQ','PCS','2026-06-12',0.4,60,'Marginal','kc2','2026-06-12','CLOSED');
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('new2','0DTE','QQQ','CCS','2026-06-12',0.4,60,'Marginal','kc3','2026-06-12','CLOSED');
      INSERT INTO signal_outcomes(signal_id, close_date, realized_pnl, exit_reason) VALUES('old1','2026-06-12',-200,'DELTA_STOP');
      INSERT INTO signal_outcomes(signal_id, close_date, realized_pnl, exit_reason) VALUES('new1','2026-06-12',50,'TARGET_HIT');
      INSERT INTO signal_outcomes(signal_id, close_date, realized_pnl, exit_reason) VALUES('new2','2026-06-12',30,'TARGET_HIT');
    """)
    c.commit(); c.close()

    monkeypatch.setattr(eod_report, "_get_underlying_close", lambda s, c: 700.0)
    path = eod_report.generate(date_str="2026-06-12", db_path=db, reports_dir=tmp_path / "r", client=None)
    md = path.read_text()
    assert "By capture date (cohort):" in md
    assert "2026-06-10" in md  # legacy cohort row
    assert "2026-06-12" in md  # new cohort row
    # 06-12 cohort: 2 trades, 2 wins, +$80
    assert "2 trades, 2 wins" in md


def test_closed_today_lists_intraday_and_settlement(tmp_path, monkeypatch):
    """Section 2 must show ALL signals closed today (intraday TP/CUT auto-closes
    AND settlement closes), not only the ones the report run closed itself."""
    db = tmp_path / "s.db"
    signal_db.init_db(db)
    import sqlite3
    c = sqlite3.connect(db)
    c.executescript("""
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, short_strike, long_strike, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('intr1','0DTE','QQQ','PCS',590,585,'2026-06-03',0.45,62,'Marginal','ki1','2026-06-01','CLOSED');
      INSERT INTO signals(signal_id, scanner_type, symbol, strategy, short_strike, long_strike, expiration, entry_credit, entry_score, entry_grade, dedup_key, first_seen_date, status)
        VALUES('intr2','SWING','SPY','CCS',770,772,'2026-06-12',0.30,55,'Marginal','ki2','2026-06-01','CLOSED');
      INSERT INTO signal_outcomes(signal_id, close_ts, close_date, exit_value, realized_pnl, exit_reason)
        VALUES('intr1','2026-06-01T12:12:05-05:00','2026-06-01',0.05,40.0,'TARGET_HIT');
      INSERT INTO signal_outcomes(signal_id, close_ts, close_date, exit_value, realized_pnl, exit_reason)
        VALUES('intr2','2026-06-01T12:41:03-05:00','2026-06-01',0.45,-15.0,'STOPPED');
    """)
    c.commit(); c.close()

    monkeypatch.setattr(eod_report, "_get_underlying_close", lambda s, c: 700.0)
    path = eod_report.generate(date_str="2026-06-01", db_path=db, reports_dir=tmp_path / "r", client=None)
    md = path.read_text()
    assert "Signals Closed Today" in md
    # Both intraday closes appear, with their exit reasons
    assert "intr1" in md and "TARGET_HIT" in md
    assert "intr2" in md and "STOPPED" in md
    # Subtotal matches the "Closed today" stat (2 trades, 1 win)
    assert "2 trades" in md
    # The misleading 0-DTE-only "no signals closed" line is gone
    assert "No 0-DTE signals closed today" not in md


def test_eod_auto_close_writes_granular_exit_reason(tmp_path, monkeypatch):
    """When EOD auto-closes a swing on a delta breach, signal_outcomes.exit_reason
    must be the granular code 'DELTA_STOP', NOT the literal action 'CUT'."""
    db = tmp_path / "s.db"
    signal_db.init_db(db)
    import sqlite3
    c = sqlite3.connect(db)
    # Open SWING signal. entry_credit=0.30 => credit_total = 30.
    c.execute(
        """INSERT INTO signals(signal_id, scanner_type, symbol, strategy,
              short_strike, long_strike, expiration, entry_credit, entry_score,
              entry_grade, dedup_key, first_seen_date, status)
           VALUES('sw1','SWING','SPY','PCS',690,688,'2026-06-30',0.30,60,
                  'Marginal','ksw1','2026-06-01','OPEN')""")
    c.commit(); c.close()

    # Fake the repricer so recommend() lands on DELTA_STOP:
    #   pnl=-5 -> not TARGET_HIT (>=15) and not MONEY_STOP (<=-60)
    #   abs(short_delta)=0.40 >= 0.35 -> DELTA_STOP
    def fake_reprice(trade, client):
        return {
            "error": None,
            "current_value": 0.55,
            "unrealized_pnl": -5.0,
            "pnl_pct_of_credit": -16.7,
            "current_underlying": 695.0,
            "current_short_delta": -0.40,
        }
    monkeypatch.setattr(signal_repricer, "reprice_swing", fake_reprice)
    monkeypatch.setattr(eod_report, "_get_underlying_close", lambda s, c: 695.0)

    eod_report.generate(date_str="2026-06-01", db_path=db,
                        reports_dir=tmp_path / "r", client=None)

    conn = signal_db.connect(db)
    try:
        row = conn.execute(
            "SELECT exit_reason FROM signal_outcomes WHERE signal_id='sw1'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "EOD should have auto-closed the delta-breach swing"
    assert row["exit_reason"] == "DELTA_STOP"


def test_streaming_section_counts_and_timing(tmp_path):
    import trade_performance_db as tpdb
    import eod_report
    p = tmp_path / "trade_performance.db"
    tpdb.init_schema(p)
    tpdb.record_iv_snapshot(p, {
        "trade_id": "t1", "moment": "entry", "ts": "2026-05-30T10:00:00-05:00",
        "put_short_iv": 0.18, "put_long_iv": 0.19,
        "call_short_iv": None, "call_long_iv": None, "underlying": 5200})
    tpdb.record_event(p, {
        "trade_id": "t1", "event_type": "target_hit", "ts": "2026-05-30T10:14:00-05:00",
        "underlying": 5210, "mid": 0.74, "unrealized_pnl": 76, "pnl_pct": 0.5, "note": ""})
    text = "\n".join(eod_report._streaming_section("2026-05-30", p))
    assert "Target hit: 1" in text
    assert "Avg time-to-target: 14.0 min" in text


def test_streaming_section_missing_db_is_graceful(tmp_path):
    import eod_report
    lines = eod_report._streaming_section("2026-05-30", tmp_path / "nope.db")
    assert any("No streaming" in ln for ln in lines)
