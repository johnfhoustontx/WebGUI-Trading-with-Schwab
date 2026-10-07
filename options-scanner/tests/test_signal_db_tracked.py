"""Tracked structures in ``signals.db`` - hidden from every existing reader.

The Market Scanner records structures that are not credit spreads (straddles,
butterflies, calendars, backspreads, the Directional tab's single legs) under two
scanner types of their own so their outcomes can be measured. They share the
``signals`` / ``signal_marks`` / ``signal_outcomes`` tables.

What these tests pin is the ISOLATION. More than a dozen readers of this store
were written for a credit spread: the Captured Signals page and its score, the
paper Account's entry feed, the phone push, Rescue, the manage cycle. Rather than
teach each of them the new shape, every accessor EXCLUDES the tracked types
unless the caller asks for them (``tracked=True``), so an existing reader gets
exactly the rows it got before.
"""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import signal_db

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)


def _row(signal_id, scanner_type="SWING", strategy="PCS", symbol="SPY", **over):
    row = {"signal_id": signal_id, "scanner_type": scanner_type, "symbol": symbol,
           "strategy": strategy, "short_strike": 500.0, "long_strike": 495.0,
           "call_short": None, "call_long": None, "width": 5.0,
           "expiration": "2026-10-16", "dte_at_entry": 9, "entry_credit": 0.60,
           "entry_max_loss": 4.40, "entry_score": 66, "entry_grade": "Good",
           "entry_short_delta": -0.2, "entry_net_theta": 0.0, "entry_iv_rank": 40.0,
           "entry_underlying": 510.0, "first_seen_ts": NOW.isoformat(),
           "first_seen_date": NOW.date().isoformat(),
           "dedup_key": f"{signal_id}-key", "status": "OPEN"}
    row.update(over)
    return row


def _tracked(signal_id, scanner_type="SWING_STRUCT", strategy="LONG_STRADDLE", **over):
    base = dict(short_strike=None, long_strike=None, width=None, entry_credit=-5.40,
                entry_max_loss=5.43, entry_short_delta=None,
                legs_json='[{"kind":"call","side":"long","strike":500.0,'
                          '"expiration":"2026-10-16","qty":1}]',
                family="STRADDLE", entry_max_profit=None, entry_capital=5.43,
                unbounded=0, entry_spans_earnings=1)
    base.update(over)
    return _row(signal_id, scanner_type, strategy, **base)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "signals.db"
    for row in (_row("c1", "SWING"), _row("c2", "0DTE", symbol="QQQ"),
                _row("c3", "INCOME", strategy="SHORT_PUT", symbol="SPY"),
                _tracked("t1"), _tracked("t2", "0DTE_STRUCT", "BUTTERFLY_CALL"),
                _tracked("t3", symbol="NVDA")):
        assert signal_db.insert_signal(row, db_path=path)
    return path


def _ids(rows):
    return sorted(r["signal_id"] for r in rows)


# ── the schema ──────────────────────────────────────────────────────────────

NEW_COLUMNS = ("legs_json", "family", "entry_max_profit", "entry_capital",
               "unbounded", "entry_spans_earnings")


def test_a_fresh_store_has_the_new_columns(tmp_path):
    path = tmp_path / "s.db"
    signal_db.init_db(path)
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(signals)")}
    assert set(NEW_COLUMNS) <= cols


def test_an_existing_store_gains_them_and_keeps_its_rows(tmp_path):
    """Prod's store predates the columns. The migration is additive and runs on
    every connect."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE signals (signal_id TEXT PRIMARY KEY,
        scanner_type TEXT NOT NULL, symbol TEXT NOT NULL, strategy TEXT NOT NULL,
        short_strike REAL, long_strike REAL, call_short REAL, call_long REAL,
        width REAL, expiration TEXT, dte_at_entry INTEGER, entry_credit REAL,
        entry_max_loss REAL, entry_score INTEGER, entry_grade TEXT,
        entry_short_delta REAL, entry_net_theta REAL, entry_iv_rank REAL,
        entry_underlying REAL, first_seen_ts TEXT, first_seen_date TEXT,
        dedup_key TEXT UNIQUE, status TEXT DEFAULT 'OPEN')""")
    conn.execute("INSERT INTO signals (signal_id, scanner_type, symbol, strategy, "
                 "status, dedup_key) VALUES ('old1', 'SWING', 'SPY', 'PCS', 'OPEN', 'k')")
    conn.commit()
    conn.close()
    signal_db.init_db(path)
    signal_db.init_db(path)                       # idempotent
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(signals)")}
    assert set(NEW_COLUMNS) <= cols
    old = signal_db.get_signal("old1", db_path=path)
    assert old["strategy"] == "PCS" and old["legs_json"] is None
    assert old["unbounded"] in (0, None)


def test_a_tracked_row_round_trips_its_new_fields(db):
    row = signal_db.get_signal("t1", db_path=db)
    assert row["legs_json"].startswith("[{") and row["family"] == "STRADDLE"
    assert row["entry_max_profit"] is None and row["entry_capital"] == 5.43
    assert row["unbounded"] == 0 and row["entry_spans_earnings"] == 1
    assert row["entry_credit"] == -5.40 and row["short_strike"] is None


def test_a_caller_that_predates_the_columns_still_inserts(tmp_path):
    """signal_recorder's credit row carries none of the new keys."""
    path = tmp_path / "s.db"
    assert signal_db.insert_signal(_row("c9"), db_path=path)
    row = signal_db.get_signal("c9", db_path=path)
    assert row["legs_json"] is None and row["family"] is None
    assert row["unbounded"] == 0 and row["entry_spans_earnings"] == 0


def test_the_tracked_types_are_named_once():
    assert signal_db.TRACKED_TYPES == ("0DTE_STRUCT", "SWING_STRUCT")
    assert signal_db.is_tracked({"scanner_type": " swing_struct "})
    assert not signal_db.is_tracked({"scanner_type": "SWING"})
    assert not signal_db.is_tracked({"scanner_type": None})
    assert not signal_db.is_tracked({})


# ── every existing reader sees what it saw before ───────────────────────────

def test_open_signals_exclude_tracked_rows_by_default(db):
    assert _ids(signal_db.get_open_signals(db_path=db)) == ["c1", "c2", "c3"]
    assert _ids(signal_db.get_open_signals_with_latest_mark(db_path=db)) == [
        "c1", "c2", "c3"]


def test_the_open_count_per_symbol_excludes_tracked_rows_by_default(db):
    """This is the count the credit capture cap reads. A tracked straddle must
    not take a slot a credit spread - which the Account can trade - would need."""
    assert signal_db.count_open_by_symbol(db_path=db) == {"SPY": 2, "QQQ": 1}


def test_tracked_rows_are_read_only_on_request(db):
    assert _ids(signal_db.get_open_signals(db_path=db, tracked=True)) == [
        "t1", "t2", "t3"]
    assert _ids(signal_db.get_open_signals_with_latest_mark(
        db_path=db, tracked=True)) == ["t1", "t2", "t3"]
    assert signal_db.count_open_by_symbol(db_path=db, tracked=True) == {
        "SPY": 2, "NVDA": 1}


def test_an_explicit_scanner_type_is_honoured_as_before(db):
    assert _ids(signal_db.get_open_signals("SWING", db_path=db)) == ["c1"]
    assert _ids(signal_db.get_open_signals("SWING_STRUCT", db_path=db)) == ["t1", "t3"]


def test_a_legacy_row_with_no_scanner_type_is_not_hidden(tmp_path):
    """``NULL NOT IN (...)`` is NULL in SQL, which would silently drop every
    row captured before the column existed."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    # A store from before ``scanner_type``: the migration adds it, as NULL.
    conn.execute("""CREATE TABLE signals (signal_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL, strategy TEXT NOT NULL, entry_credit REAL,
        first_seen_ts TEXT, first_seen_date TEXT, dedup_key TEXT UNIQUE,
        status TEXT DEFAULT 'OPEN')""")
    conn.execute("INSERT INTO signals (signal_id, symbol, strategy, entry_credit, "
                 "first_seen_ts, first_seen_date, dedup_key, status) VALUES "
                 "('n1', 'SPY', 'PCS', 0.6, ?, ?, 'k', 'OPEN')",
                 (NOW.isoformat(), NOW.date().isoformat()))
    conn.commit()
    conn.close()
    assert signal_db.get_signal("n1", db_path=path)["scanner_type"] is None   # vacuity
    assert _ids(signal_db.get_open_signals(db_path=path)) == ["n1"]
    assert _ids(signal_db.get_open_signals_with_latest_mark(db_path=path)) == ["n1"]
    assert signal_db.count_open_by_symbol(db_path=path) == {"SPY": 1}
    assert signal_db.count_opened_on(NOW.date().isoformat(), db_path=path) == 1
    # ... and it is not a tracked row either.
    assert signal_db.get_open_signals(db_path=path, tracked=True) == []


def test_captures_counted_on_a_date_exclude_tracked_rows_by_default(db):
    today = NOW.date().isoformat()
    assert signal_db.count_opened_on(today, db_path=db) == 3
    assert signal_db.count_opened_on(today, db_path=db, tracked=True) == 3


def _close(db, signal_id, exit_value, when=NOW):
    signal_db.close_signal_manually(signal_id, exit_value, "TARGET_HIT",
                                    db_path=db, close_ts=when)


def test_outcomes_exclude_tracked_rows_by_default(db):
    _close(db, "c1", 0.30)
    _close(db, "t1", -8.10)
    today = NOW.date().isoformat()
    assert _ids(signal_db.get_outcomes_for_date(today, db_path=db)) == ["c1"]
    assert _ids(signal_db.get_outcomes_in_range(today, today, db_path=db)) == ["c1"]
    assert _ids(signal_db.get_outcomes_for_date(today, db_path=db, tracked=True)) == ["t1"]
    assert _ids(signal_db.get_outcomes_in_range(
        today, today, db_path=db, tracked=True)) == ["t1"]


def test_a_closed_tracked_row_leaves_the_open_readers(db):
    _close(db, "t1", -8.10)
    assert _ids(signal_db.get_open_signals(db_path=db, tracked=True)) == ["t2", "t3"]


# ── the one P&L convention ──────────────────────────────────────────────────

def test_a_debit_closes_on_the_same_formula_as_a_credit(db):
    """``entry_credit`` is SIGNED (a debit is negative) and ``exit_value`` is the
    cost to close in the same convention (what a long position is worth, negated).
    So ``(entry_credit - exit_value) * 100`` is right for both: a straddle bought
    for 5.40 and worth 8.10 made $270."""
    _close(db, "t1", -8.10)
    out = signal_db.get_outcomes_for_date(NOW.date().isoformat(), db_path=db,
                                          tracked=True)[0]
    assert out["realized_pnl"] == pytest.approx(270.0)
    assert out["entry_credit"] == -5.40 and out["exit_value"] == -8.10


def test_marks_and_the_peak_work_for_a_tracked_row(db):
    signal_db.insert_marks([
        {"signal_id": "t1", "mark_ts": "2026-10-07T10:05:00", "mark_date": "2026-10-07",
         "current_value": -6.0, "unrealized_pnl": 60.0, "recommendation": "HOLD"},
        {"signal_id": "t1", "mark_ts": "2026-10-07T10:10:00", "mark_date": "2026-10-07",
         "current_value": -5.0, "unrealized_pnl": -40.0, "recommendation": "HOLD"}],
        db_path=db)
    assert signal_db.peak_unrealized("t1", db_path=db) == 60.0
    row = next(r for r in signal_db.get_open_signals_with_latest_mark(
        db_path=db, tracked=True) if r["signal_id"] == "t1")
    assert row["unrealized_pnl"] == -40.0 and row["current_value"] == -5.0


# ── the tracked rows' own readers ───────────────────────────────────────────

def test_tracked_outcomes_carry_what_a_study_needs_and_only_tracked_rows(db):
    _close(db, "c1", 0.30)
    _close(db, "t1", -8.10)
    rows = signal_db.get_tracked_outcomes(db_path=db)
    assert _ids(rows) == ["t1"]
    row = rows[0]
    assert row["strategy"] == "LONG_STRADDLE" and row["family"] == "STRADDLE"
    assert row["entry_max_loss"] == 5.43 and row["unbounded"] == 0
    assert row["entry_spans_earnings"] == 1
    assert row["realized_pnl"] == pytest.approx(270.0)
    assert row["exit_reason"] == "TARGET_HIT"


def test_an_unmarkable_close_has_no_outcome_and_leaves_the_open_set(db):
    signal_db.close_unmarkable("t3", db_path=db, close_ts=NOW)
    assert "t3" not in _ids(signal_db.get_open_signals(db_path=db, tracked=True))
    row = signal_db.get_tracked_outcomes(db_path=db)[0]
    assert row["exit_reason"] == "UNMARKABLE"
    assert row["realized_pnl"] is None and row["exit_value"] is None
    assert signal_db.get_signal("t3", db_path=db)["status"] == "CLOSED"


def test_an_unmarkable_close_of_an_unknown_id_raises(db):
    with pytest.raises(ValueError):
        signal_db.close_unmarkable("nope", db_path=db, close_ts=NOW)


# ── what the tracked recorder's caps count (2026-10-07) ─────────────────────

def test_count_open_tracked_groups_by_family_structure_and_symbol(tmp_path):
    db = tmp_path / "s.db"
    signal_db.init_db(db)
    conn = signal_db.connect(db)
    rows = [("a", "SWING_STRUCT", "STRADDLE", "LONG_STRADDLE", "SPY", "OPEN"),
            ("b", "0DTE_STRUCT", "STRADDLE", "LONG_STRADDLE", "SPY", "OPEN"),
            ("c", "SWING_STRUCT", "STRADDLE", "SHORT_STRANGLE", "QQQ", "OPEN"),
            ("d", "SWING_STRUCT", None, "LONG_CALL", "SPY", "OPEN"),
            ("e", "SWING_STRUCT", "STRADDLE", "LONG_STRADDLE", "IWM", "CLOSED"),
            ("f", "SWING", None, "PCS", "SPY", "OPEN")]            # a credit spread
    for sid, kind, fam, strat, sym, status in rows:
        conn.execute(
            "INSERT INTO signals (signal_id, scanner_type, family, strategy, symbol, "
            "status, dedup_key, expiration, first_seen_ts, first_seen_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-16', '2026-10-07T10:00:00', "
            "'2026-10-07')", (sid, kind, fam, strat, sym, status, sid))
    conn.commit()
    conn.close()
    assert sorted(signal_db.count_open_tracked(db_path=db)) == [
        ("", "LONG_CALL", "SPY", 1), ("STRADDLE", "LONG_STRADDLE", "SPY", 2),
        ("STRADDLE", "SHORT_STRANGLE", "QQQ", 1)]
