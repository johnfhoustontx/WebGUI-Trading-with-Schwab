"""
SchwabProxy - Tests for perf_writer
Version: 1.0.0
Last Updated: 2026-05-30

Version 1.0.0 Changes:
- Initial implementation
"""
import sqlite3

import perf_writer as pw


#############################################
# SCHEMA
#############################################

def test_init_schema_idempotent(tmp_path):
    p = tmp_path / "perf.db"
    pw.init_schema(p)
    pw.init_schema(p)  # second call must not raise
    assert p.exists()


#############################################
# EVENT WRITES + LOAD_FIRED
#############################################

def test_record_event_and_load_fired(tmp_path):
    p = tmp_path / "perf.db"
    pw.init_schema(p)
    pw.record_event({
        "trade_id": "t1", "event_type": "target_hit",
        "ts": "2026-05-30T10:47:23-05:00", "underlying": 5210.0,
        "mid": 0.74, "unrealized_pnl": 76.0, "pnl_pct": 0.506, "note": "x",
    }, path=p)
    assert pw.load_fired("t1", path=p) == {"target_hit"}
    assert pw.load_fired("unknown", path=p) == set()


#############################################
# IV SNAPSHOT WRITES
#############################################

def test_record_iv_snapshot(tmp_path):
    p = tmp_path / "perf.db"
    pw.init_schema(p)
    pw.record_iv_snapshot({
        "trade_id": "t1", "moment": "entry", "ts": "2026-05-30T10:00:00-05:00",
        "put_short_iv": 0.18, "put_long_iv": 0.19,
        "call_short_iv": None, "call_long_iv": None, "underlying": 5200.0,
    }, path=p)
    # round-trip via a direct query
    conn = sqlite3.connect(str(p))
    n = conn.execute(
        "SELECT COUNT(*) FROM perf_iv_snapshots WHERE trade_id='t1'"
    ).fetchone()[0]
    conn.close()
    assert n == 1


#############################################
# RESILIENCE — WRITES NEVER RAISE
#############################################

def test_record_event_bad_path_does_not_raise(tmp_path):
    # Opening a directory as a sqlite file raises OperationalError inside
    # record_event; the function must catch and not propagate.
    d = tmp_path / "adir"
    d.mkdir()
    pw.record_event({"trade_id": "t", "event_type": "x", "ts": "t"}, path=d)
    # no assertion needed beyond "did not raise"


def test_record_iv_snapshot_bad_path_does_not_raise(tmp_path):
    d = tmp_path / "adir2"
    d.mkdir()
    pw.record_iv_snapshot({"trade_id": "t", "moment": "entry", "ts": "t"}, path=d)
    # no assertion needed beyond "did not raise"
