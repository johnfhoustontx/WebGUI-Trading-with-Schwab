import datetime as dt

from services.sentiment_svc import market_state_history_db as db


def _conn():
    return db.connect(":memory:")


def _iso(d):
    return d.isoformat()


def test_record_and_load_recent_date_asc_with_decoded_components():
    c = _conn()
    base = dt.date(2026, 6, 16)
    for i in range(4):
        db.record(c, _iso(base + dt.timedelta(days=i)),
                  "bullish", 60.0 + i, 0.5 + i * 0.1,
                  {"evidence": [f"e{i}"], "n": i})
    rows = db.load_recent(c, n_days=60)
    assert [r["date"] for r in rows] == [
        _iso(base + dt.timedelta(days=i)) for i in range(4)]  # asc
    # decoded components round-trip
    assert rows[0]["components"] == {"evidence": ["e0"], "n": 0}
    assert rows[2]["committed_state"] == "bullish"
    assert rows[2]["direction_score"] == 62.0
    assert rows[2]["aggression"] == 0.7


def test_load_recent_windows_last_n_days():
    c = _conn()
    base = dt.date(2026, 6, 1)
    for i in range(10):
        db.record(c, _iso(base + dt.timedelta(days=i)), "neutral", 50.0, 0.0, {})
    rows = db.load_recent(c, n_days=3)
    assert [r["date"] for r in rows] == [
        _iso(base + dt.timedelta(days=i)) for i in (7, 8, 9)]


def test_todays_row_replace_update_wins():
    c = _conn()
    base = dt.date(2026, 6, 16)
    for i in range(3):
        db.record(c, _iso(base + dt.timedelta(days=i)), "neutral", 50.0, 0.0, {})
    latest = _iso(base + dt.timedelta(days=2))
    db.record(c, latest, "bearish", 20.0, -0.8, {"evidence": ["flip"]})
    assert c.execute("SELECT COUNT(*) FROM market_state").fetchone()[0] == 3
    rows = db.load_recent(c, n_days=60)
    assert rows[-1]["committed_state"] == "bearish"
    assert rows[-1]["direction_score"] == 20.0
    assert rows[-1]["aggression"] == -0.8
    assert rows[-1]["components"] == {"evidence": ["flip"]}


def test_components_round_trip():
    c = _conn()
    comp = {"evidence": ["a", "b"], "sub_scores": {"price": 1.0, "vix": 2.0},
            "aggression_confidence": 0.42}
    db.record(c, "2026-06-16", "bullish", 70.0, 0.6, comp)
    assert db.load_recent(c, n_days=60)[0]["components"] == comp


def test_malformed_components_does_not_crash_record():
    c = _conn()
    # A non-JSON-serializable components value -> record stores {} (no raise).
    db.record(c, "2026-06-16", "neutral", 50.0, 0.0, {"bad": {1, 2, 3}})
    row = db.load_recent(c, n_days=60)[0]
    assert row["components"] == {}


def test_prune_keeps_window():
    c = _conn()
    base = dt.date(2026, 6, 1)
    for i in range(20):
        db.record(c, _iso(base + dt.timedelta(days=i)), "neutral", 50.0, 0.0, {})
    db.prune(c, keep=12)
    assert c.execute("SELECT COUNT(*) FROM market_state").fetchone()[0] == 12
    min_date = c.execute("SELECT MIN(date) FROM market_state").fetchone()[0]
    assert min_date == _iso(base + dt.timedelta(days=8))  # last 12 of 20


def test_prune_noop_when_within_window():
    c = _conn()
    base = dt.date(2026, 6, 1)
    for i in range(5):
        db.record(c, _iso(base + dt.timedelta(days=i)), "neutral", 50.0, 0.0, {})
    db.prune(c, keep=90)
    assert c.execute("SELECT COUNT(*) FROM market_state").fetchone()[0] == 5


def test_load_recent_empty_db_is_empty():
    assert db.load_recent(_conn(), n_days=60) == []
