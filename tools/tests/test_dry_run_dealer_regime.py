"""The dealer-regime dry run reads observations, not carried rows (audit AC-121)."""
import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "options-scanner"))

import gex_history_db as db  # noqa: E402

from tools import dry_run_dealer_regime as tool  # noqa: E402


def _db():
    conn = sqlite3.connect(":memory:")
    db.init_schema(conn)
    return conn


def test_a_carried_row_is_left_out_of_the_study():
    conn = _db()
    db.insert_snapshot(conn, "AAPL", "gex", {"ts": 60, "spot": 100.0}, {}, 0)
    db.insert_snapshot(conn, "AAPL", "gex", {"ts": 120, "spot": 101.0,
                                             "carried_age_sec": 95.0}, {}, 0)
    db.insert_snapshot(conn, "AAPL", "gex", {"ts": 180, "spot": 102.0}, {}, 0)
    assert tool._select(conn, "ts, spot", "AAPL", 0, 1000) == \
        [(60, 100.0), (180, 102.0)]


def test_a_database_from_before_the_column_is_read_whole():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (symbol TEXT, view TEXT, ts INTEGER, spot REAL)")
    conn.execute("INSERT INTO snapshots VALUES ('AAPL', 'gex', 60, 100.0)")
    assert tool._select(conn, "ts, spot", "AAPL", 0, 1000) == [(60, 100.0)]
