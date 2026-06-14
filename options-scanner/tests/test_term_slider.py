import sqlite3
import tempfile
from pathlib import Path
import gex_history_db as db
from gamma_tool import term_slider_positions


def test_slider_positions_returns_snapped_timestamps():
    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "t.db")
        try:
            db.init_term_schema(conn)
            for ts in (
                "2026-04-29T09:30:00-05:00",
                "2026-04-29T09:35:00-05:00",
                "2026-04-29T09:40:00-05:00",
            ):
                db.insert_term_snapshot_rows(conn, [
                    (ts, "SPX", "2026-04-29", 7100.0, 0.0, 0.0, 0.0, 7135.0)
                ])
            positions = term_slider_positions(conn, "2026-04-29", "SPX")
            assert positions == [
                "2026-04-29T09:30:00-05:00",
                "2026-04-29T09:35:00-05:00",
                "2026-04-29T09:40:00-05:00",
            ]
        finally:
            conn.close()


def test_slider_positions_empty():
    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "t.db")
        try:
            db.init_term_schema(conn)
            assert term_slider_positions(conn, "2026-04-29", "SPX") == []
        finally:
            conn.close()


def test_slider_positions_filters_other_dates():
    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "t.db")
        try:
            db.init_term_schema(conn)
            db.insert_term_snapshot_rows(conn, [
                ("2026-04-28T15:55:00-05:00", "SPX", "2026-04-28",
                 7100.0, 0.0, 0.0, 0.0, 7100.0)
            ])
            db.insert_term_snapshot_rows(conn, [
                ("2026-04-29T09:30:00-05:00", "SPX", "2026-04-29",
                 7100.0, 0.0, 0.0, 0.0, 7135.0)
            ])
            assert term_slider_positions(conn, "2026-04-29", "SPX") == [
                "2026-04-29T09:30:00-05:00",
            ]
        finally:
            conn.close()
