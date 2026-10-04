import datetime as dt

from services.sentiment_svc import intraday_history_db as db


def _conn():
    c = db.connect(":memory:")
    return c


def _ts(date, hour=10, minute=0):
    return int(dt.datetime(date.year, date.month, date.day, hour, minute)
               .astimezone().timestamp())


def test_insert_and_load_roundtrip():
    c = _conn()
    today = dt.date(2026, 6, 30)
    db.insert_point(c, _ts(today, 10, 0), 6.2, 72.0)
    db.insert_point(c, _ts(today, 10, 2), 6.4, 70.0)
    rows = db.load_recent(c, n_days=5)
    assert [(r[1], r[2]) for r in rows] == [(6.2, 72.0), (6.4, 70.0)]
    # ascending by ts
    assert rows[0][0] < rows[1][0]


def test_load_recent_keeps_only_last_n_trading_dates():
    c = _conn()
    base = dt.date(2026, 6, 16)
    for i in range(8):                       # 8 distinct dates
        d = base + dt.timedelta(days=i)
        db.insert_point(c, _ts(d), 5.0 + i * 0.1, 50.0 + i)
    rows = db.load_recent(c, n_days=5)
    dates = sorted({dt.datetime.fromtimestamp(r[0]).astimezone().date() for r in rows})
    assert len(dates) == 5
    assert dates[0] == base + dt.timedelta(days=3)   # last 5 of 8


def test_insert_point_is_idempotent_upsert():
    c = _conn()
    ts = _ts(dt.date(2026, 6, 30), 10, 0)
    db.insert_point(c, ts, 6.2, 72.0)
    db.insert_point(c, ts, 4.1, 33.0)   # same ts, different values
    rows = db.load_recent(c, n_days=5)
    assert len(rows) == 1
    assert (rows[0][1], rows[0][2]) == (4.1, 33.0)   # the replacing value wins


def test_load_recent_empty_db_returns_empty_list():
    assert db.load_recent(db.connect(":memory:")) == []


def test_prune_deletes_older_than_n_dates():
    c = _conn()
    base = dt.date(2026, 6, 16)
    for i in range(8):
        db.insert_point(c, _ts(base + dt.timedelta(days=i)), 5.0, 50.0)
    db.prune(c, n_days=5)
    remaining = c.execute("SELECT COUNT(*) FROM sentiment_intraday").fetchone()[0]
    assert remaining == 5
    min_date = min(dt.datetime.fromtimestamp(r[0]).astimezone().date()
                   for r in c.execute("SELECT ts FROM sentiment_intraday"))
    assert min_date == base + dt.timedelta(days=3)   # start of the kept 5-of-8 window


def test_connect_default_path_is_memory_under_pytest():
    """Regression (2026-07-07): tests calling handlers.refresh() leaked fixture
    rows into the REAL sentiment_intraday.db (the live service then republished
    them — the 'volatile spikes' on the /sentiment intraday graphs). Under
    pytest, connect() with no explicit path must NEVER open the real file —
    mirror of the Bus fakeredis-under-pytest convention."""
    conn = db.connect()          # no path → would be repo_paths.SENTIMENT_INTRADAY_DB
    rows = conn.execute("PRAGMA database_list").fetchall()
    # (seq, name, file) — an in-memory DB has an empty file column.
    assert rows[0][2] in ("", None)


_VEC = {"mean_reversion": 0.5, "trending": 0.3, "breakout": 0.05,
        "choppy": 0.1, "crisis": 0.05}


def test_insert_and_load_regime_roundtrip():
    c = db.connect(":memory:")
    today = dt.date.today()
    db.insert_regime_point(c, _ts(today, 10, 0), _VEC, 0.6, "Balanced")
    rows = db.load_regime_recent(c, n_days=1)
    assert len(rows) == 1
    ts, mem, conf, label = rows[0]
    assert mem["mean_reversion"] == 0.5 and mem["trending"] == 0.3
    assert conf == 0.6 and label == "Balanced"


def test_regime_point_is_idempotent_upsert():
    c = db.connect(":memory:")
    ts = _ts(dt.date.today())
    db.insert_regime_point(c, ts, _VEC, 0.6, "Balanced")
    db.insert_regime_point(c, ts, dict(_VEC, trending=0.9), 0.9, "Trending")
    rows = db.load_regime_recent(c, n_days=1)
    assert len(rows) == 1 and rows[0][1]["trending"] == 0.9 and rows[0][3] == "Trending"


def test_load_regime_recent_windows_by_date():
    c = db.connect(":memory:")
    base = dt.date.today() - dt.timedelta(days=40)
    for i in range(40):
        db.insert_regime_point(c, _ts(base + dt.timedelta(days=i)), _VEC, 0.5, "x")
    assert len({db._local_date(r[0]) for r in db.load_regime_recent(c, n_days=3)}) == 3


def test_prune_regime_keeps_30_sessions():
    c = db.connect(":memory:")
    base = dt.date.today() - dt.timedelta(days=45)
    for i in range(45):
        db.insert_regime_point(c, _ts(base + dt.timedelta(days=i)), _VEC, 0.5, "x")
    db.prune_regime(c, n_days=30)
    dates = {db._local_date(r[0]) for r in db.load_regime_recent(c, n_days=100)}
    assert len(dates) == 30


def test_regime_table_shares_pytest_memory_isolation():
    # connect() with no path is :memory: under pytest — the regime table must
    # obey the same guard as sentiment_intraday (defense-in-depth).
    c = db.connect()
    db.insert_regime_point(c, _ts(dt.date.today()), _VEC, 0.5, "x")
    assert db.load_regime_recent(c, n_days=1)   # writes to the in-memory DB, not the file


# --- AC-48: each session's last LIVE composite, kept for like-for-like history --

def test_record_close_keeps_the_last_reading_of_a_session():
    c = _conn()
    db.record_close(c, "2026-09-29", 4.10, 100)
    db.record_close(c, "2026-09-29", 4.93, 200)       # later in the same session
    db.record_close(c, "2026-09-30", 4.06, 300)
    assert db.load_closes(c) == [("2026-09-29", 4.93), ("2026-09-30", 4.06)]


def test_load_closes_stops_before_the_given_date():
    c = _conn()
    for d, t in (("2026-09-28", 4.0), ("2026-09-29", 4.9), ("2026-09-30", 4.1)):
        db.record_close(c, d, t, 1)
    assert db.load_closes(c, before="2026-09-30") == [("2026-09-28", 4.0),
                                                      ("2026-09-29", 4.9)]


def test_seed_closes_takes_the_last_sample_of_each_recorded_day():
    c = _conn()
    d1, d2 = dt.date(2026, 9, 29), dt.date(2026, 9, 30)
    db.insert_point(c, _ts(d1, 9, 0), 4.0, 50.0)
    db.insert_point(c, _ts(d1, 14, 58), 4.93, 50.0)
    db.insert_point(c, _ts(d2, 14, 58), 4.06, 50.0)
    db.seed_closes(c)
    assert db.load_closes(c) == [("2026-09-29", 4.93), ("2026-09-30", 4.06)]


def test_seed_closes_never_overwrites_a_recorded_close():
    c = _conn()
    d1 = dt.date(2026, 9, 29)
    db.record_close(c, "2026-09-29", 5.55, 1)
    db.insert_point(c, _ts(d1, 14, 58), 4.93, 50.0)
    db.seed_closes(c)
    assert db.load_closes(c) == [("2026-09-29", 5.55)]


def test_prune_closes_keeps_the_newest():
    c = _conn()
    for i in range(1, 11):
        db.record_close(c, f"2026-09-{i:02d}", float(i), i)
    db.prune_closes(c, keep=3)
    assert [d for d, _ in db.load_closes(c)] == ["2026-09-08", "2026-09-09",
                                                 "2026-09-10"]
