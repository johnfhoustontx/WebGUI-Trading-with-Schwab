import trade_performance_db as tpdb


def _db(tmp_path):
    p = tmp_path / "trade_performance.db"
    tpdb.init_schema(p)
    return p


def test_init_schema_is_idempotent(tmp_path):
    p = _db(tmp_path)
    tpdb.init_schema(p)  # second call must not raise
    assert p.exists()


def test_record_and_get_event(tmp_path):
    p = _db(tmp_path)
    tpdb.record_event(p, {
        "trade_id": "abc123", "event_type": "target_hit",
        "ts": "2026-05-30T10:47:23-05:00", "underlying": 5210.0,
        "mid": 0.74, "unrealized_pnl": 76.0, "pnl_pct": 0.506,
        "note": ">=50% credit captured",
    })
    events = tpdb.get_events(p, "abc123")
    assert len(events) == 1
    assert events[0]["event_type"] == "target_hit"
    assert events[0]["pnl_pct"] == 0.506


def test_record_and_get_iv_snapshot_vertical(tmp_path):
    p = _db(tmp_path)
    tpdb.record_iv_snapshot(p, {
        "trade_id": "abc123", "moment": "entry",
        "ts": "2026-05-30T10:33:00-05:00",
        "put_short_iv": 0.183, "put_long_iv": 0.191,
        "call_short_iv": None, "call_long_iv": None, "underlying": 5205.0,
    })
    snaps = tpdb.get_iv_snapshots(p, "abc123")
    assert len(snaps) == 1
    assert snaps[0]["put_short_iv"] == 0.183
    assert snaps[0]["call_short_iv"] is None


def test_summarize_target(tmp_path):
    p = _db(tmp_path)
    tpdb.record_event(p, {
        "trade_id": "t1", "event_type": "target_hit",
        "ts": "2026-05-30T10:47:23-05:00", "underlying": 5210.0,
        "mid": 0.74, "unrealized_pnl": 76.0, "pnl_pct": 0.506, "note": "",
    })
    s = tpdb.summarize(p, "t1")
    assert s["target_hit_ts"] == "2026-05-30T10:47:23-05:00"
    assert s["stop_hit_ts"] is None
    assert s["strike_test_ts"] is None
