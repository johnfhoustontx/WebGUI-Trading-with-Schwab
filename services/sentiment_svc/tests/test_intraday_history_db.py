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


def test_prune_deletes_older_than_n_dates():
    c = _conn()
    base = dt.date(2026, 6, 16)
    for i in range(8):
        db.insert_point(c, _ts(base + dt.timedelta(days=i)), 5.0, 50.0)
    db.prune(c, n_days=5)
    remaining = c.execute("SELECT COUNT(*) FROM sentiment_intraday").fetchone()[0]
    assert remaining == 5
