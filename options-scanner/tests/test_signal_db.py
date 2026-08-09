import sqlite3
from pathlib import Path
import signal_db


def test_init_creates_three_tables(tmp_path):
    db_path = tmp_path / "signals.db"
    signal_db.init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    names = [r[0] for r in cur.fetchall()]
    conn.close()

    assert "signals" in names
    assert "signal_marks" in names
    assert "signal_outcomes" in names


def test_init_is_idempotent(tmp_path):
    db_path = tmp_path / "signals.db"
    signal_db.init_db(db_path)
    signal_db.init_db(db_path)  # should not raise


def test_signals_dedup_key_unique(tmp_path):
    db_path = tmp_path / "signals.db"
    signal_db.init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO signals(signal_id, scanner_type, symbol, strategy, short_strike, long_strike, width, expiration, dte_at_entry, entry_credit, entry_max_loss, entry_score, entry_grade, entry_short_delta, entry_net_theta, entry_iv_rank, entry_underlying, first_seen_ts, first_seen_date, dedup_key, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("a1","0DTE","SPY","PCS",690,688,2,"2026-04-15",0,0.6,1.4,54,"Marginal",-0.15,5.0,30.0,700.0,"2026-04-15T09:00:00-05:00","2026-04-15","SPY|PCS|690|688|2026-04-15|0DTE","OPEN"))
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO signals(signal_id, scanner_type, symbol, strategy, short_strike, long_strike, width, expiration, dte_at_entry, entry_credit, entry_max_loss, entry_score, entry_grade, entry_short_delta, entry_net_theta, entry_iv_rank, entry_underlying, first_seen_ts, first_seen_date, dedup_key, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("a2","0DTE","SPY","PCS",690,688,2,"2026-04-15",0,0.6,1.4,54,"Marginal",-0.15,5.0,30.0,700.0,"2026-04-15T09:00:00-05:00","2026-04-15","SPY|PCS|690|688|2026-04-15|0DTE","OPEN"))
        assert False, "expected IntegrityError on duplicate dedup_key"
    except sqlite3.IntegrityError:
        pass
    conn.close()


def test_foreign_key_enforcement(tmp_path):
    db_path = tmp_path / "signals.db"
    conn = signal_db.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO signal_marks(signal_id, mark_ts, mark_date) VALUES (?, ?, ?)",
            ("nonexistent", "2026-04-15T10:00:00-05:00", "2026-04-15"),
        )
        conn.commit()
        assert False, "expected IntegrityError for orphan signal_id"
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()


def test_insert_and_fetch_signal(tmp_path):
    db = tmp_path / "signals.db"
    row = _sample_signal_row(dedup_key="SPY|PCS|690|688|2026-04-15|0DTE")
    signal_db.insert_signal(row, db_path=db)

    got = signal_db.get_signal("a1", db_path=db)
    assert got["symbol"] == "SPY"
    assert got["status"] == "OPEN"


def test_insert_signal_dedup_is_silent(tmp_path):
    db = tmp_path / "signals.db"
    row = _sample_signal_row(dedup_key="SPY|PCS|690|688|2026-04-15|0DTE")
    assert signal_db.insert_signal(row, db_path=db) is True
    row2 = _sample_signal_row(signal_id="a2", dedup_key="SPY|PCS|690|688|2026-04-15|0DTE")
    assert signal_db.insert_signal(row2, db_path=db) is False


def test_get_open_signals_filters_by_scanner_type(tmp_path):
    db = tmp_path / "signals.db"
    signal_db.insert_signal(_sample_signal_row(signal_id="a1", dedup_key="k1", scanner_type="0DTE"), db_path=db)
    signal_db.insert_signal(_sample_signal_row(signal_id="a2", dedup_key="k2", scanner_type="SWING"), db_path=db)
    opens = signal_db.get_open_signals(scanner_type="SWING", db_path=db)
    assert len(opens) == 1
    assert opens[0]["signal_id"] == "a2"


def test_insert_mark_and_outcome_lifecycle(tmp_path):
    db = tmp_path / "signals.db"
    signal_db.insert_signal(_sample_signal_row(dedup_key="k1"), db_path=db)
    signal_db.insert_mark({
        "signal_id": "a1",
        "mark_ts": "2026-04-15T10:00:00-05:00",
        "mark_date": "2026-04-15",
        "current_value": 0.30,
        "unrealized_pnl": 30.0,
        "pnl_pct_of_credit": 50.0,
        "current_underlying": 700.0,
        "current_short_delta": -0.12,
        "current_score": None,
        "score_drift": None,
        "recommendation": "HOLD",
        "recommendation_reason": "score steady",
    }, db_path=db)
    signal_db.insert_outcome({
        "signal_id": "a1",
        "close_ts": "2026-04-15T15:00:00-05:00",
        "close_date": "2026-04-15",
        "exit_value": 0.0,
        "realized_pnl": 60.0,
        "exit_reason": "EXPIRED",
        "settlement_underlying": 700.0,
    }, new_status="EXPIRED", db_path=db)
    # status should now be EXPIRED
    got = signal_db.get_signal("a1", db_path=db)
    assert got["status"] == "EXPIRED"
    # and get_open_signals should exclude it
    assert signal_db.get_open_signals(db_path=db) == []


def test_get_open_signals_no_filter_returns_all(tmp_path):
    db = tmp_path / "signals.db"
    signal_db.insert_signal(_sample_signal_row(signal_id="a1", dedup_key="k1", scanner_type="0DTE"), db_path=db)
    signal_db.insert_signal(_sample_signal_row(signal_id="a2", dedup_key="k2", scanner_type="SWING"), db_path=db)
    opens = signal_db.get_open_signals(db_path=db)
    assert len(opens) == 2


def _sample_signal_row(signal_id="a1", dedup_key="k", scanner_type="0DTE"):
    return {
        "signal_id": signal_id, "scanner_type": scanner_type, "symbol": "SPY",
        "strategy": "PCS", "short_strike": 690, "long_strike": 688, "call_short": None,
        "call_long": None, "width": 2, "expiration": "2026-04-15", "dte_at_entry": 0,
        "entry_credit": 0.6, "entry_max_loss": 1.4, "entry_score": 54, "entry_grade": "Marginal",
        "entry_short_delta": -0.15, "entry_net_theta": 5.0, "entry_iv_rank": 30.0,
        "entry_underlying": 700.0, "first_seen_ts": "2026-04-15T09:00:00-05:00",
        "first_seen_date": "2026-04-15", "dedup_key": dedup_key, "status": "OPEN",
    }


def test_get_open_signals_with_latest_mark_no_marks(tmp_path):
    db = tmp_path / "s.db"
    signal_db.insert_signal({
        "signal_id": "sig1", "scanner_type": "0DTE", "symbol": "SPX",
        "strategy": "PCS", "short_strike": 5000, "long_strike": 4995,
        "call_short": None, "call_long": None, "width": 5,
        "expiration": "2026-05-26", "dte_at_entry": 0,
        "entry_credit": 1.2, "entry_max_loss": 380, "entry_score": 70,
        "entry_grade": "B", "entry_short_delta": -0.15, "entry_net_theta": 0.5,
        "entry_iv_rank": 40, "entry_underlying": 5050,
        "first_seen_ts": "2026-05-26T09:00:00", "first_seen_date": "2026-05-26",
        "dedup_key": "k1", "status": "OPEN",
    }, db_path=db)
    rows = signal_db.get_open_signals_with_latest_mark(db_path=db)
    assert len(rows) == 1
    r = rows[0]
    assert r["signal_id"] == "sig1"
    assert r["unrealized_pnl"] is None
    assert r["current_score"] is None
    assert r["last_mark_ts"] is None

def test_get_open_signals_with_latest_mark_picks_newest(tmp_path):
    db = tmp_path / "s.db"
    signal_db.insert_signal({
        "signal_id": "sig1", "scanner_type": "0DTE", "symbol": "SPY",
        "strategy": "PCS", "short_strike": 500, "long_strike": 495,
        "call_short": None, "call_long": None, "width": 5,
        "expiration": "2026-05-26", "dte_at_entry": 0,
        "entry_credit": 1.0, "entry_max_loss": 400, "entry_score": 65,
        "entry_grade": "B", "entry_short_delta": -0.2, "entry_net_theta": 0.4,
        "entry_iv_rank": 30, "entry_underlying": 505,
        "first_seen_ts": "2026-05-26T09:00:00", "first_seen_date": "2026-05-26",
        "dedup_key": "k1", "status": "OPEN",
    }, db_path=db)
    signal_db.insert_mark({
        "signal_id": "sig1", "mark_ts": "2026-05-26T10:00:00",
        "mark_date": "2026-05-26", "current_value": 0.8,
        "unrealized_pnl": 20, "pnl_pct_of_credit": 20,
        "current_underlying": 506, "current_short_delta": -0.18,
        "current_score": 68, "score_drift": 3,
        "recommendation": "HOLD", "recommendation_reason": "ok",
    }, db_path=db)
    signal_db.insert_mark({
        "signal_id": "sig1", "mark_ts": "2026-05-26T11:00:00",
        "mark_date": "2026-05-26", "current_value": 0.5,
        "unrealized_pnl": 50, "pnl_pct_of_credit": 50,
        "current_underlying": 507, "current_short_delta": -0.10,
        "current_score": 72, "score_drift": 7,
        "recommendation": "TAKE", "recommendation_reason": "profit target",
    }, db_path=db)
    rows = signal_db.get_open_signals_with_latest_mark(db_path=db)
    assert len(rows) == 1
    assert rows[0]["unrealized_pnl"] == 50
    assert rows[0]["current_score"] == 72
    assert rows[0]["current_value"] == 0.5      # latest mark's spread value (cur option price)
    assert rows[0]["recommendation"] == "TAKE"
    assert rows[0]["last_mark_ts"] == "2026-05-26T11:00:00"

def test_get_open_signals_with_latest_mark_excludes_closed(tmp_path):
    db = tmp_path / "s.db"
    for sid, status in [("a", "OPEN"), ("b", "CLOSED")]:
        signal_db.insert_signal({
            "signal_id": sid, "scanner_type": "0DTE", "symbol": "SPY",
            "strategy": "PCS", "short_strike": 500, "long_strike": 495,
            "call_short": None, "call_long": None, "width": 5,
            "expiration": "2026-05-26", "dte_at_entry": 0,
            "entry_credit": 1.0, "entry_max_loss": 400, "entry_score": 65,
            "entry_grade": "B", "entry_short_delta": -0.2, "entry_net_theta": 0.4,
            "entry_iv_rank": 30, "entry_underlying": 505,
            "first_seen_ts": "2026-05-26T09:00:00", "first_seen_date": "2026-05-26",
            "dedup_key": f"k{sid}", "status": status,
        }, db_path=db)
    rows = signal_db.get_open_signals_with_latest_mark(db_path=db)
    assert [r["signal_id"] for r in rows] == ["a"]


def test_close_signal_manually_writes_outcome_and_flips_status(tmp_path):
    db = tmp_path / "s.db"
    signal_db.insert_signal({
        "signal_id": "sig1", "scanner_type": "0DTE", "symbol": "SPX",
        "strategy": "PCS", "short_strike": 5000, "long_strike": 4995,
        "call_short": None, "call_long": None, "width": 5,
        "expiration": "2026-05-26", "dte_at_entry": 0,
        "entry_credit": 1.2, "entry_max_loss": 380, "entry_score": 70,
        "entry_grade": "B", "entry_short_delta": -0.15, "entry_net_theta": 0.5,
        "entry_iv_rank": 40, "entry_underlying": 5050,
        "first_seen_ts": "2026-05-26T09:00:00", "first_seen_date": "2026-05-26",
        "dedup_key": "k1", "status": "OPEN",
    }, db_path=db)
    signal_db.close_signal_manually(
        "sig1", exit_value=0.4, exit_reason="TARGET_HIT", db_path=db,
    )
    sig = signal_db.get_signal("sig1", db_path=db)
    assert sig["status"] == "CLOSED"
    conn = signal_db.connect(db)
    try:
        row = conn.execute(
            "SELECT * FROM signal_outcomes WHERE signal_id='sig1'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    # realized_pnl = (entry_credit - exit_value) * 100  = (1.2 - 0.4) * 100 = 80
    assert abs(row["realized_pnl"] - 80.0) < 1e-6
    assert row["exit_value"] == 0.4
    assert row["exit_reason"] == "TARGET_HIT"


def test_close_signal_manually_unknown_signal_raises(tmp_path):
    db = tmp_path / "s.db"
    signal_db.init_db(db)
    import pytest
    with pytest.raises(ValueError):
        signal_db.close_signal_manually("nope", 0.0, "OTHER", db_path=db)


def test_close_signal_manually_none_entry_credit_raises(tmp_path):
    db = tmp_path / "s.db"
    signal_db.insert_signal({
        "signal_id": "sigX", "scanner_type": "0DTE", "symbol": "SPY",
        "strategy": "PCS", "short_strike": 500, "long_strike": 495,
        "call_short": None, "call_long": None, "width": 5,
        "expiration": "2026-05-26", "dte_at_entry": 0,
        "entry_credit": None, "entry_max_loss": 400, "entry_score": 65,
        "entry_grade": "B", "entry_short_delta": -0.2, "entry_net_theta": 0.4,
        "entry_iv_rank": 30, "entry_underlying": 505,
        "first_seen_ts": "2026-05-26T09:00:00", "first_seen_date": "2026-05-26",
        "dedup_key": "kX", "status": "OPEN",
    }, db_path=db)
    import pytest
    with pytest.raises(ValueError, match="entry_credit"):
        signal_db.close_signal_manually("sigX", 0.5, "OTHER", db_path=db)


def test_schema_migration_adds_new_columns(tmp_path):
    """init_schema is idempotent and adds the new columns even on pre-existing DBs."""
    import sqlite3
    import signal_db
    db_path = str(tmp_path / "signals.db")
    signal_db.init_schema(db_path=db_path)
    signal_db.init_schema(db_path=db_path)  # second call must not raise

    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
        assert "entry_net_delta_position" in cols
        assert "entry_net_theta_position" in cols
        assert "entry_spread_bid" in cols
        assert "entry_spread_ask" in cols
    finally:
        conn.close()


class TestModeColumn:
    """Mode column for distinguishing PREMIUM vs DIRECTIONAL signals."""

    def test_mode_column_exists_after_init(self, tmp_path):
        """init_db creates the mode column on a fresh DB."""
        db = tmp_path / "test_mode.db"
        signal_db.init_db(db)
        import sqlite3
        conn = sqlite3.connect(db)
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)")}
            assert "mode" in cols
        finally:
            conn.close()

    def test_mode_migration_is_idempotent(self, tmp_path):
        """Calling init_db twice on a DB with existing data does not error."""
        db = tmp_path / "test_mode_idempotent.db"
        signal_db.init_db(db)
        row = _sample_signal_row(signal_id="AAA111", dedup_key="test|key|1")
        signal_db.insert_signal(row, db_path=db)
        signal_db.init_db(db)  # Second migration pass must not raise
        assert signal_db.get_signal("AAA111", db_path=db) is not None

    def test_mode_defaults_to_null_when_not_provided(self, tmp_path):
        """Legacy callers that omit `mode` get NULL in the column."""
        db = tmp_path / "test_mode_null.db"
        signal_db.init_db(db)
        row = _sample_signal_row(signal_id="BBB222", dedup_key="test|key|2")
        signal_db.insert_signal(row, db_path=db)
        got = signal_db.get_signal("BBB222", db_path=db)
        assert got is not None
        assert got.get("mode") is None

    def test_mode_round_trip_directional(self, tmp_path):
        """When `mode='DIRECTIONAL'` is provided, it persists and is read back."""
        db = tmp_path / "test_mode_directional.db"
        signal_db.init_db(db)
        row = _sample_signal_row(signal_id="CCC333", dedup_key="test|key|3")
        row["mode"] = "DIRECTIONAL"
        signal_db.insert_signal(row, db_path=db)
        got = signal_db.get_signal("CCC333", db_path=db)
        assert got["mode"] == "DIRECTIONAL"


def test_schema_migration_idempotent_on_legacy_db(tmp_path):
    """If a DB was created with the OLD schema, init_schema adds the new columns."""
    import sqlite3
    import signal_db
    db_path = str(tmp_path / "signals.db")

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE signals (
            signal_id TEXT PRIMARY KEY,
            symbol TEXT,
            strategy TEXT,
            entry_short_delta REAL,
            entry_net_theta REAL
        )
    """)
    conn.commit()
    conn.close()

    signal_db.init_schema(db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
        assert "entry_net_delta_position" in cols
        assert "entry_net_theta_position" in cols
        assert "entry_spread_bid" in cols
        assert "entry_spread_ask" in cols
    finally:
        conn.close()


# ── be_armed column + closed-by-date query (captured-autoclose) ─────────────
def test_be_armed_defaults_zero_and_survives_round_trip(tmp_path):
    db = tmp_path / "s.db"
    signal_db.insert_signal(_sample_signal_row(dedup_key="ba1"), db_path=db)
    got = signal_db.get_signal("a1", db_path=db)
    assert got["be_armed"] == 0


def test_set_be_armed_flips_the_flag(tmp_path):
    db = tmp_path / "s.db"
    signal_db.insert_signal(_sample_signal_row(dedup_key="ba2"), db_path=db)
    assert signal_db.get_signal("a1", db_path=db)["be_armed"] == 0
    signal_db.set_be_armed("a1", db_path=db)
    assert signal_db.get_signal("a1", db_path=db)["be_armed"] == 1
    # idempotent — a second call keeps it armed
    signal_db.set_be_armed("a1", db_path=db)
    assert signal_db.get_signal("a1", db_path=db)["be_armed"] == 1


def test_open_view_exposes_be_armed(tmp_path):
    db = tmp_path / "s.db"
    signal_db.insert_signal(_sample_signal_row(dedup_key="ba3"), db_path=db)
    signal_db.set_be_armed("a1", db_path=db)
    rows = signal_db.get_open_signals_with_latest_mark(db_path=db)
    assert len(rows) == 1 and rows[0]["be_armed"] == 1


def test_be_armed_migration_on_legacy_db(tmp_path):
    """init_schema adds be_armed to a DB created without it (idempotent)."""
    import sqlite3
    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE signals (signal_id TEXT PRIMARY KEY, symbol TEXT)")
    conn.commit()
    conn.close()
    signal_db.init_schema(db_path=db_path)
    signal_db.init_schema(db_path=db_path)  # second pass must not raise
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)").fetchall()}
        assert "be_armed" in cols
    finally:
        conn.close()


def _seed_closed(db, signal_id, dedup_key, exit_value, exit_reason,
                 close_date, close_ts, entry_credit=1.2):
    signal_db.insert_signal(_sample_signal_row(
        signal_id=signal_id, dedup_key=dedup_key), db_path=db)
    # entry_credit override on the seeded row
    conn = signal_db.connect(db)
    conn.execute("UPDATE signals SET entry_credit=?, strategy='PCS' WHERE signal_id=?",
                 (entry_credit, signal_id))
    conn.commit(); conn.close()
    realized = (entry_credit - exit_value) * 100.0
    signal_db.insert_outcome({
        "signal_id": signal_id, "close_ts": close_ts, "close_date": close_date,
        "exit_value": exit_value, "realized_pnl": realized,
        "exit_reason": exit_reason, "settlement_underlying": None,
    }, new_status="CLOSED", db_path=db)


def test_get_outcomes_for_date_returns_today_only(tmp_path):
    db = tmp_path / "s.db"
    _seed_closed(db, "cA", "kcA", 0.40, "BREAKEVEN_STOP",
                 "2026-08-09", "2026-08-09T14:31:00-05:00")
    _seed_closed(db, "cB", "kcB", 1.50, "MONEY_STOP",
                 "2026-08-09", "2026-08-09T10:05:00-05:00")
    _seed_closed(db, "cOld", "kcOld", 0.10, "EXPIRED",
                 "2026-08-08", "2026-08-08T15:00:00-05:00")

    rows = signal_db.get_outcomes_for_date("2026-08-09", db_path=db)
    ids = [r["signal_id"] for r in rows]
    assert ids == ["cA", "cB"]                 # newest close_ts first, other date excluded
    a = rows[0]
    assert a["symbol"] == "SPY" and a["strategy"] == "PCS"
    assert a["exit_value"] == 0.40 and a["exit_reason"] == "BREAKEVEN_STOP"
    assert abs(a["realized_pnl"] - (1.2 - 0.40) * 100.0) < 1e-6
    assert a["entry_credit"] == 1.2 and a["close_ts"].startswith("2026-08-09")


def test_get_outcomes_for_date_empty_when_none(tmp_path):
    db = tmp_path / "s.db"
    signal_db.init_db(db)
    assert signal_db.get_outcomes_for_date("2026-08-09", db_path=db) == []


def test_insert_mark_ignores_unknown_columns_without_mutating(tmp_path):
    db = str(tmp_path / "signals.db")
    signal_db.insert_signal({
        "signal_id": "sig1", "scanner_type": "SWING", "symbol": "SPX",
        "strategy": "PCS", "short_strike": 5000.0, "long_strike": 4990.0,
        "call_short": None, "call_long": None,
        "width": 10.0, "expiration": "2026-12-31", "dte_at_entry": 30,
        "entry_credit": 1.0, "entry_max_loss": 9.0, "entry_score": 70,
        "entry_grade": "B", "entry_short_delta": 0.2, "entry_net_theta": 0.05,
        "entry_iv_rank": 40.0, "entry_underlying": 5050.0,
        "first_seen_ts": "2026-06-01T10:00:00-05:00",
        "first_seen_date": "2026-06-01", "dedup_key": "sig1",
        "status": "OPEN",
    }, db_path=db)

    row = {
        "signal_id": "sig1",
        "mark_ts": "2026-06-01T11:00:00-05:00",
        "current_value": 0.40,
        "recommendation": "TAKE_PROFIT",
        "recommendation_code": "TARGET_HIT",   # NOT a signal_marks column
    }
    signal_db.insert_mark(row, db_path=db)              # must not raise
    assert row["recommendation_code"] == "TARGET_HIT"  # caller dict untouched

    conn = signal_db.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(signal_marks)")}
        assert "recommendation_code" not in cols
        got = conn.execute(
            "SELECT signal_id, current_value, recommendation FROM signal_marks "
            "WHERE signal_id='sig1'"
        ).fetchone()
    finally:
        conn.close()
    assert got["signal_id"] == "sig1"
    assert got["current_value"] == 0.40
    assert got["recommendation"] == "TAKE_PROFIT"
