import sqlite3, tempfile
from pathlib import Path
import gex_history_db as db

def test_term_schema_created():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        conn = sqlite3.connect(path)
        try:
            db.init_term_schema(conn)
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gex_term_snapshots'")
            assert cur.fetchone() is not None
        finally:
            conn.close()

def test_term_insert_and_query_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        conn = sqlite3.connect(path)
        try:
            db.init_term_schema(conn)
            rows = [
                ("2026-04-29T09:30:00-05:00", "SPX", "2026-04-29", 7100.0, 100e6, 50e6, 50e6, 7135.9),
                ("2026-04-29T09:30:00-05:00", "SPX", "2026-04-29", 7150.0, 200e6, 30e6, 170e6, 7135.9),
            ]
            db.insert_term_snapshot_rows(conn, rows)
            got = db.load_term_snapshot(conn, "2026-04-29T09:30:00-05:00", "SPX")
            assert len(got) == 2
            assert got[0]["strike"] == 7100.0
            assert got[1]["net_gex_usd"] == 170e6
            assert got[0]["underlying_price"] == 7135.9
        finally:
            conn.close()

def test_term_load_missing_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        conn = sqlite3.connect(path)
        try:
            db.init_term_schema(conn)
            assert db.load_term_snapshot(conn, "2026-04-29T09:30:00-05:00", "SPX") == []
        finally:
            conn.close()

def test_term_list_timestamps_today():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        conn = sqlite3.connect(path)
        try:
            db.init_term_schema(conn)
            for ts in ("2026-04-29T09:30:00-05:00", "2026-04-29T09:35:00-05:00"):
                db.insert_term_snapshot_rows(conn, [
                    (ts, "SPX", "2026-04-29", 7100.0, 0.0, 0.0, 0.0, 7135.9)
                ])
            db.insert_term_snapshot_rows(conn, [
                ("2026-04-28T09:30:00-05:00", "SPX", "2026-04-28", 7100.0, 0.0, 0.0, 0.0, 7100.0)
            ])
            got = db.list_term_timestamps_for_date(conn, "2026-04-29", "SPX")
            assert got == ["2026-04-29T09:30:00-05:00", "2026-04-29T09:35:00-05:00"]
        finally:
            conn.close()
