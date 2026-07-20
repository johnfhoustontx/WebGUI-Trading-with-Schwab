import json
import sqlite3
import time
import pytest
from pathlib import Path
import gex_history_db as db


def test_init_schema_creates_tables():
    conn = sqlite3.connect(":memory:")
    db.init_schema(conn)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='snapshots'"
    )
    assert cur.fetchone() is not None


def test_init_schema_drops_redundant_idx_snap_today():
    """idx_snap_today (symbol, view, ts) exactly duplicates the PRIMARY KEY
    autoindex, so every insert maintained two identical b-trees. init_schema
    must NOT create it and must drop it from a pre-existing DB."""
    conn = sqlite3.connect(":memory:")
    # Simulate an old DB that already carries the redundant index.
    conn.execute("CREATE TABLE snapshots (symbol TEXT, view TEXT, ts INTEGER, "
                 "PRIMARY KEY (symbol, view, ts))")
    conn.execute("CREATE INDEX idx_snap_today ON snapshots(symbol, view, ts)")
    db.init_schema(conn)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_snap_today'")
    assert cur.fetchone() is None


def test_gex_grid_stored_compressed_and_round_trips(tmp_path, monkeypatch):
    """gex_json is stored zlib-compressed (BLOB) at insert — ~5x smaller on disk —
    and reads back identically through load_date_with_grid."""
    import zlib
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    grid = {str(5400.0 + i): {"net": float(i)} for i in range(200)}
    now = int(time.time())
    db.insert_snapshot(conn, "SPY", "gex", {"ts": now, "spot": 5400.0},
                       grid, 1)
    stored = conn.execute("SELECT gex_json FROM snapshots").fetchone()[0]
    assert isinstance(stored, (bytes, bytearray))   # compressed, not raw JSON text
    assert len(stored) < len(json.dumps(grid))       # actually smaller
    assert json.loads(zlib.decompress(stored)) == {str(k): v for k, v in grid.items()}
    rows = db.load_date_with_grid(conn, "SPY", "gex")
    assert rows[0][6] == {5400.0 + i: {"net": float(i)} for i in range(200)}


def test_load_decodes_legacy_uncompressed_rows(tmp_path, monkeypatch):
    """A row written before compression (plain JSON TEXT) still decodes — the
    reader is format-agnostic (bytes -> zlib, str -> json)."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    now = int(time.time())
    conn.execute(
        "INSERT INTO snapshots (symbol, view, ts, spot, gex_json) VALUES (?,?,?,?,?)",
        ("SPY", "gex", now, 5400.0, json.dumps({"5400.0": {"net": 9}})))
    conn.commit()
    rows = db.load_date_with_grid(conn, "SPY", "gex")
    assert rows[0][6] == {5400.0: {"net": 9}}


def test_load_flow_tail_returns_last_n_chronological(tmp_path, monkeypatch):
    """Flow-alert detectors need only the last ~22 rows, not the whole day.
    load_flow_tail returns the last `limit` gex-view rows in ASC order."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    base = int(time.time()) - 300
    for i in range(10):
        db.insert_snapshot(
            conn, "SPY", "gex",
            {"ts": base + i * 60, "spot": float(i), "call_vol": i, "put_vol": 0},
            {"1.0": {"net": 1}}, 1)
    tail = db.load_flow_tail(conn, "SPY", limit=3)
    assert [r[1] for r in tail] == [7.0, 8.0, 9.0]     # last 3, chronological
    assert len(db.load_flow_tail(conn, "SPY", limit=100)) == 10  # fewer than limit


def test_init_schema_is_idempotent():
    conn = sqlite3.connect(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # must not raise


def test_connect_creates_file(tmp_path, monkeypatch):
    dbpath = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    assert dbpath.exists()
    conn.close()


def test_connect_read_only_rejects_write(tmp_path, monkeypatch):
    dbpath = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    rw = db.connect()
    db.init_schema(rw)
    rw.close()
    ro = db.connect(read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute(
            "INSERT INTO snapshots(symbol,view,ts) VALUES ('X','gex',1)"
        )
        ro.commit()
    ro.close()


def test_connect_read_only_missing_file_raises(tmp_path, monkeypatch):
    dbpath = tmp_path / "missing.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    with pytest.raises(sqlite3.OperationalError):
        db.connect(read_only=True)


def test_insert_snapshot_roundtrip(tmp_path, monkeypatch):
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    ts = int(time.time())
    summary = {
        "ts": ts, "spot": 5000.0, "flip": 4995.0,
        "top_pos_strike": 5010.0, "top_neg_strike": 4980.0,
        "net_total": 1.23e9,
    }
    grid = {"4990": {"call": 1.0, "put": -0.5, "net": 0.5}}
    db.insert_snapshot(conn, "SPX", "gex", summary, grid, dte=2)
    rows = db.load_today(conn, "SPX", "gex")
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == ts
    assert row[1] == 5000.0
    assert row[2] == 4995.0


def test_latest_flip_returns_most_recent_and_none_on_empty(tmp_path, monkeypatch):
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    now = int(time.time())

    def summary(ts, flip):
        return {"ts": ts, "spot": 100.0, "flip": flip,
                "top_pos_strike": None, "top_neg_strike": None, "net_total": None}

    # empty -> None
    assert db.latest_flip(conn, "SPY", "gex") is None
    db.insert_snapshot(conn, "SPY", "gex", summary(now - 120, 4990.0), {}, dte=1)
    db.insert_snapshot(conn, "SPY", "gex", summary(now, 5001.0), {}, dte=1)
    # most-recent row's flip wins
    assert db.latest_flip(conn, "SPY", "gex") == 5001.0
    # other symbol still empty
    assert db.latest_flip(conn, "QQQ", "gex") is None


def test_insert_replace_on_duplicate_ts(tmp_path, monkeypatch):
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    ts = int(time.time())
    s1 = {"ts": ts, "spot": 100.0, "flip": None,
          "top_pos_strike": None, "top_neg_strike": None, "net_total": None}
    s2 = dict(s1, spot=200.0)
    db.insert_snapshot(conn, "SPY", "gex", s1, {}, dte=1)
    db.insert_snapshot(conn, "SPY", "gex", s2, {}, dte=1)
    rows = db.load_today(conn, "SPY", "gex")
    assert len(rows) == 1
    assert rows[0][1] == 200.0


def test_load_today_filters_by_symbol_and_view(tmp_path, monkeypatch):
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    now = int(time.time())
    summary = lambda ts, spot=100.0: {
        "ts": ts, "spot": spot, "flip": None,
        "top_pos_strike": None, "top_neg_strike": None, "net_total": None,
    }
    db.insert_snapshot(conn, "SPY", "gex",   summary(now),     {}, 1)
    db.insert_snapshot(conn, "SPY", "charm", summary(now),     {}, 1)
    db.insert_snapshot(conn, "QQQ", "gex",   summary(now),     {}, 1)
    assert len(db.load_today(conn, "SPY", "gex")) == 1
    assert len(db.load_today(conn, "SPY", "charm")) == 1
    assert len(db.load_today(conn, "QQQ", "gex")) == 1
    assert len(db.load_today(conn, "QQQ", "charm")) == 0


def test_load_today_excludes_other_days(tmp_path, monkeypatch):
    """Only today's (local-date) snapshots come back — the sargable ts-range
    filter must match the old DATE(ts,'unixepoch','localtime') behavior."""
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    now = int(time.time())
    summary = lambda ts, spot: {
        "ts": ts, "spot": spot, "flip": None,
        "top_pos_strike": None, "top_neg_strike": None, "net_total": None,
    }
    db.insert_snapshot(conn, "SPY", "gex", summary(now, 1.0), {}, 1)
    db.insert_snapshot(conn, "SPY", "gex", summary(now - 2 * 86400, 2.0), {}, 1)  # 2 days ago
    db.insert_snapshot(conn, "SPY", "gex", summary(now - 5 * 86400, 3.0), {}, 1)  # 5 days ago
    rows = db.load_today(conn, "SPY", "gex")
    assert len(rows) == 1 and rows[0][1] == 1.0
    grid_rows = db.load_today_with_grid(conn, "SPY", "gex")
    assert len(grid_rows) == 1 and grid_rows[0][1] == 1.0


def test_load_date_with_grid_since_returns_only_newer_rows(tmp_path, monkeypatch):
    """since_ts makes the session reload incremental: only rows with ts >
    since_ts come back (the gamma snapshot memo appends them instead of
    re-decoding the whole day every minute)."""
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    now = int(time.time())
    summary = lambda ts, spot: {
        "ts": ts, "spot": spot, "flip": None,
        "top_pos_strike": None, "top_neg_strike": None, "net_total": None,
    }
    db.insert_snapshot(conn, "SPY", "gex", summary(now - 120, 1.0), {"100.0": 5}, 1)
    db.insert_snapshot(conn, "SPY", "gex", summary(now - 60, 2.0), {"100.0": 6}, 1)
    db.insert_snapshot(conn, "SPY", "gex", summary(now, 3.0), {"100.0": 7}, 1)
    full = db.load_date_with_grid(conn, "SPY", "gex")
    assert [r[1] for r in full] == [1.0, 2.0, 3.0]
    newer = db.load_date_with_grid(conn, "SPY", "gex", since_ts=now - 120)
    assert [r[1] for r in newer] == [2.0, 3.0]     # strictly ts > since_ts
    assert newer[0][6] == {100.0: 6}               # grid decoded, float keys


def test_load_today_orders_by_ts(tmp_path, monkeypatch):
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    now = int(time.time())
    make = lambda ts: {"ts": ts, "spot": float(ts), "flip": None,
                       "top_pos_strike": None, "top_neg_strike": None,
                       "net_total": None}
    db.insert_snapshot(conn, "SPY", "gex", make(now + 10), {}, 1)
    db.insert_snapshot(conn, "SPY", "gex", make(now),      {}, 1)
    db.insert_snapshot(conn, "SPY", "gex", make(now + 5),  {}, 1)
    rows = db.load_today(conn, "SPY", "gex")
    assert [r[0] for r in rows] == [now, now + 5, now + 10]


def test_purge_old_deletes_prior_days(tmp_path, monkeypatch):
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    now = int(time.time())
    yesterday = now - 86400 * 2
    make = lambda ts: {"ts": ts, "spot": 1.0, "flip": None,
                       "top_pos_strike": None, "top_neg_strike": None,
                       "net_total": None}
    db.insert_snapshot(conn, "SPY", "gex", make(yesterday), {}, 1)
    db.insert_snapshot(conn, "SPY", "gex", make(now),       {}, 1)
    db.purge_old(conn)
    rows = conn.execute("SELECT ts FROM snapshots").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == now


def _day_ts(days_ago, hour=12):
    """Unix ts at local `hour` on the day `days_ago` days before today."""
    import datetime as _dt
    d = _dt.date.today() - _dt.timedelta(days=days_ago)
    return int(_dt.datetime(d.year, d.month, d.day, hour).astimezone().timestamp())


def _make_summary(ts, spot=1.0):
    return {"ts": ts, "spot": spot, "flip": None,
            "top_pos_strike": None, "top_neg_strike": None, "net_total": None}


def test_purge_keep_sessions_keeps_last_n_and_last_session(tmp_path, monkeypatch):
    """Retention keeps exactly the last N distinct session-dates AND never drops
    the most recent session (off-hours persistence)."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    # 8 distinct session-dates, two rows each (two views).
    for days in range(8):
        ts = _day_ts(days)
        db.insert_snapshot(conn, "SPY", "gex", _make_summary(ts, float(days)), {}, 1)
        db.insert_snapshot(conn, "SPY", "charm", _make_summary(ts, float(days)), {}, 1)
    deleted = db.purge_keep_sessions(conn, keep_sessions=5)
    assert deleted > 0
    kept_dates = db._distinct_session_dates(conn)
    assert len(kept_dates) == 5
    # The most-recent (today) session is preserved.
    import datetime as _dt
    assert _dt.date.today().isoformat() in kept_dates


def test_purge_keep_sessions_noop_when_within_window(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    for days in range(3):  # only 3 sessions, keep 5 → nothing to delete
        db.insert_snapshot(conn, "SPY", "gex", _make_summary(_day_ts(days)), {}, 1)
    assert db.purge_keep_sessions(conn, keep_sessions=5) == 0
    assert len(db._distinct_session_dates(conn)) == 3


def test_purge_keep_sessions_covers_term_table(tmp_path, monkeypatch):
    """Retention purges BOTH snapshots AND gex_term_snapshots, keeping the same
    N session-dates in each."""
    import datetime as _dt
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    for days in range(6):  # 6 sessions in both tables; keep 5 → oldest dropped
        ts = _day_ts(days)
        db.insert_snapshot(conn, "$SPX", "gex", _make_summary(ts), {}, 1)
        date_str = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
        db.insert_term_snapshot_rows(
            conn,
            [(f"{date_str} 12:00:00", "$SPX", "2026-06-18", 5000.0,
              1.0, -0.5, 0.5, 5000.0)],
        )
    db.purge_keep_sessions(conn, keep_sessions=5)
    # snapshots: 5 sessions kept.
    assert len(db._distinct_session_dates(conn)) == 5
    # term table: 5 distinct date-prefixes kept (oldest dropped).
    term_dates = {r[0] for r in conn.execute(
        "SELECT DISTINCT substr(timestamp_ct,1,10) FROM gex_term_snapshots")}
    assert len(term_dates) == 5
    oldest = (_dt.date.today() - _dt.timedelta(days=5)).isoformat()
    assert oldest not in term_dates


def test_last_snapshot_age_matches_date_filter(tmp_path, monkeypatch):
    """The sargable ts-range in last_snapshot_age returns the same result as the
    old DATE(ts,'unixepoch','localtime')=DATE('now') filter."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    today_ts = _day_ts(0)
    db.insert_snapshot(conn, "SPY", "gex", _make_summary(today_ts, 1.0), {}, 1)
    db.insert_snapshot(conn, "SPY", "gex", _make_summary(_day_ts(2), 2.0), {}, 1)
    age, last_ts = db.last_snapshot_age(conn, "SPY", "gex")
    assert last_ts == today_ts  # yesterday/older excluded


def test_first_snapshot_today_matches_date_filter(tmp_path, monkeypatch):
    """The sargable ts-range in first_snapshot_today returns today's earliest,
    excluding prior days (same as the old DATE() filter)."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    grid_today = {5000.0: {"call": 1.0, "put": -0.5, "net": 0.5}}
    db.insert_snapshot(conn, "$SPX", "dex",
                       _make_summary(_day_ts(0, hour=9)), grid_today, 0)
    # A prior-day row that is EARLIER in clock-time must NOT be picked.
    db.insert_snapshot(conn, "$SPX", "dex",
                       _make_summary(_day_ts(3, hour=8)),
                       {9999.0: {"net": 1.0}}, 0)
    assert db.first_snapshot_today(conn, "$SPX", "dex") == grid_today


def test_last_snapshot_age_fresh(tmp_path, monkeypatch):
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    ts = int(time.time()) - 120
    db.insert_snapshot(
        conn, "SPY", "gex",
        {"ts": ts, "spot": 1.0, "flip": None,
         "top_pos_strike": None, "top_neg_strike": None, "net_total": None},
        {}, 1,
    )
    age, last_ts = db.last_snapshot_age(conn, "SPY", "gex")
    assert last_ts == ts
    assert 115 <= age <= 130


def test_load_today_with_grid_decodes_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    ts = int(time.time())
    summary = {"ts": ts, "spot": 100.0, "flip": 99.0,
               "top_pos_strike": 110.0, "top_neg_strike": 95.0,
               "net_total": 1.0}
    # JSON stringifies dict keys. Loader must return float-keyed dicts so
    # downstream numeric logic (GammaEngine.group_gex) works.
    grid = {100.0: {"call": 0.5, "put": -0.25, "net": 0.25}}
    db.insert_snapshot(conn, "SPY", "gex", summary, grid, dte=1)
    rows = db.load_today_with_grid(conn, "SPY", "gex")
    assert len(rows) == 1
    assert rows[0][6] == grid
    assert all(isinstance(k, float) for k in rows[0][6].keys())


def test_load_date_with_grid_loads_a_prior_session(tmp_path, monkeypatch):
    # Gamma persistence: a prior session's rows (e.g. Friday's) must be loadable by
    # explicit date so the heatmap stays populated over the weekend.
    import datetime as _dt
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    yesterday = _dt.date.today() - _dt.timedelta(days=1)
    y_noon = _dt.datetime(yesterday.year, yesterday.month, yesterday.day, 12, 0).astimezone()
    summary = {"ts": int(y_noon.timestamp()), "spot": 100.0, "flip": 99.0,
               "top_pos_strike": 110.0, "top_neg_strike": 95.0, "net_total": 1.0}
    grid = {100.0: {"call": 0.5, "put": -0.25, "net": 0.25}}
    db.insert_snapshot(conn, "SPY", "gex", summary, grid, dte=1)
    # Today's query finds nothing; the prior-date query finds the row.
    assert db.load_date_with_grid(conn, "SPY", "gex") == []
    rows = db.load_date_with_grid(conn, "SPY", "gex", yesterday)
    assert len(rows) == 1 and rows[0][6] == grid


def test_load_today_with_grid_empty_when_no_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    summary = {"ts": int(time.time()), "spot": None, "flip": None,
               "top_pos_strike": None, "top_neg_strike": None,
               "net_total": None}
    db.insert_snapshot(conn, "SPY", "gex", summary, {}, dte=1)
    rows = db.load_today_with_grid(conn, "SPY", "gex")
    assert rows[0][6] == {}


def test_last_snapshot_age_no_data(tmp_path, monkeypatch):
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    age, last_ts = db.last_snapshot_age(conn, "SPY", "gex")
    assert age is None
    assert last_ts is None


def test_insert_snapshot_stores_0dte_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    db.insert_snapshot(
        conn, "$SPX", "dex",
        {
            "ts": 1_700_000_000, "spot": 5000.0, "flip": 4995.0,
            "top_pos_strike": 5010.0, "top_neg_strike": 4980.0,
            "net_total": 1.0e9,
            "net_delta_0dte": -1.2e9,
            "projected_net_delta_close": -0.87e9,
            "hedge_pressure": 3.3e8,
        },
        {5000.0: {"call": 1.0, "put": -0.5, "net": 0.5}},
        0,
    )
    row = conn.execute(
        "SELECT net_delta_0dte, projected_net_delta_close, hedge_pressure "
        "FROM snapshots WHERE view = 'dex'"
    ).fetchone()
    assert row == (-1.2e9, -0.87e9, 3.3e8)


def test_insert_snapshot_null_0dte_fields(tmp_path, monkeypatch):
    """When chain has no 0-DTE contracts, the three fields must be NULL, not 0."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    db.insert_snapshot(
        conn, "VIX", "dex",
        {"ts": 1_700_000_000, "spot": 18.0, "flip": None,
         "top_pos_strike": None, "top_neg_strike": None, "net_total": 0.0,
         "net_delta_0dte": None,
         "projected_net_delta_close": None,
         "hedge_pressure": None},
        {}, 7,
    )
    row = conn.execute(
        "SELECT net_delta_0dte, projected_net_delta_close, hedge_pressure "
        "FROM snapshots WHERE symbol = 'VIX'"
    ).fetchone()
    assert row == (None, None, None)


def test_init_schema_backfills_legacy_db(tmp_path, monkeypatch):
    """A DB created before the 0-DTE columns existed must get them via ALTER TABLE.

    This exercises the PRAGMA table_info + ADD COLUMN branch of init_schema,
    which is the path production DBs will hit on first run after the upgrade.
    A second init_schema call must be a no-op.
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    conn = db.connect()
    # Simulate the pre-migration schema (no 0-DTE columns).
    conn.executescript(
        """
        CREATE TABLE snapshots (
            symbol          TEXT    NOT NULL,
            view            TEXT    NOT NULL,
            ts              INTEGER NOT NULL,
            spot            REAL,
            flip            REAL,
            top_pos_strike  REAL,
            top_neg_strike  REAL,
            net_total       REAL,
            dte             INTEGER,
            gex_json        TEXT,
            PRIMARY KEY (symbol, view, ts)
        );
        """
    )
    db.init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
    assert {"net_delta_0dte", "projected_net_delta_close", "hedge_pressure"} <= cols

    # Second call must be idempotent (no duplicate ALTER, no exception).
    db.init_schema(conn)
    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
    assert cols_after == cols


def test_first_snapshot_today_returns_earliest(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)

    import time as _time
    # Three snapshots today at different seconds; earliest must win.
    base = int(_time.time()) - 3600
    for offset, spot in [(600, 5000.0), (0, 4998.0), (1200, 5002.0)]:
        db.insert_snapshot(
            conn, "$SPX", "dex",
            {"ts": base + offset, "spot": spot, "flip": None,
             "top_pos_strike": None, "top_neg_strike": None, "net_total": 0.0},
            {5000.0: {"call": 1.0, "put": -0.5, "net": 0.5}},
            0,
        )
    grid = db.first_snapshot_today(conn, "$SPX", "dex")
    # The "earliest" snapshot is offset 0 (spot 4998) — its grid should come back.
    assert grid == {5000.0: {"call": 1.0, "put": -0.5, "net": 0.5}}


def test_first_snapshot_today_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    assert db.first_snapshot_today(conn, "$SPX", "dex") == {}


def test_insert_snapshot_roundtrips_skew_scalars(tmp_path, monkeypatch):
    """The 25-delta skew scalars (rr_25d/call_vol/put_vol) round-trip from the
    summary dict through insert_snapshot to the DB."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    db.insert_snapshot(
        conn, "$SPX", "dex",
        {"ts": 1_700_000_000, "spot": 5000.0, "flip": None,
         "top_pos_strike": None, "top_neg_strike": None, "net_total": 0.0,
         "rr_25d": 4.5, "call_vol": 1200, "put_vol": 1500},
        {}, 0,
    )
    row = conn.execute(
        "SELECT rr_25d, call_vol, put_vol FROM snapshots WHERE view = 'dex'"
    ).fetchone()
    assert row == (4.5, 1200, 1500)


def test_insert_snapshot_missing_skew_is_null(tmp_path, monkeypatch):
    """A summary WITHOUT the skew keys → the columns are NULL, no error."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    db.insert_snapshot(
        conn, "SPY", "gex",
        {"ts": 1_700_000_000, "spot": 100.0, "flip": None,
         "top_pos_strike": None, "top_neg_strike": None, "net_total": None},
        {}, 1,
    )
    row = conn.execute(
        "SELECT rr_25d, call_vol, put_vol FROM snapshots WHERE symbol = 'SPY'"
    ).fetchone()
    assert row == (None, None, None)


def test_latest_skew_by_symbol_two_most_recent(tmp_path, monkeypatch):
    """latest_skew_by_symbol returns the two most-recent (ts, rr_25d, call_vol,
    put_vol) rows for (symbol, view), most-recent first (LIMIT 2)."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    base = int(time.time())
    for offset, rr, cv, pv in [(0, 1.0, 100, 110), (60, 2.0, 200, 210),
                               (120, 3.0, 300, 310)]:
        db.insert_snapshot(
            conn, "$SPX", "gex",
            {"ts": base + offset, "spot": 5000.0, "flip": None,
             "top_pos_strike": None, "top_neg_strike": None, "net_total": 0.0,
             "rr_25d": rr, "call_vol": cv, "put_vol": pv},
            {}, 0,
        )
    rows = db.latest_skew_by_symbol(conn, "$SPX", "gex")
    assert len(rows) == 2
    assert rows[0] == (base + 120, 3.0, 300, 310)   # most recent first
    assert rows[1] == (base + 60, 2.0, 200, 210)


def test_latest_skew_by_symbol_filters_symbol_and_view(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    ts = int(time.time())
    base = {"ts": ts, "spot": 1.0, "flip": None, "top_pos_strike": None,
            "top_neg_strike": None, "net_total": 0.0,
            "rr_25d": 5.0, "call_vol": 1, "put_vol": 2}
    db.insert_snapshot(conn, "$SPX", "gex", base, {}, 0)
    db.insert_snapshot(conn, "$SPX", "charm", base, {}, 0)
    db.insert_snapshot(conn, "SPY", "gex", base, {}, 0)
    assert len(db.latest_skew_by_symbol(conn, "$SPX", "gex")) == 1
    assert db.latest_skew_by_symbol(conn, "QQQ", "gex") == []


def test_latest_skew_by_symbol_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    assert db.latest_skew_by_symbol(conn, "$SPX", "gex") == []


def test_skew_columns_backfilled_on_preexisting_db(tmp_path, monkeypatch):
    """A DB created before the skew columns existed must get them via ALTER TABLE
    (rr_25d REAL, call_vol INTEGER, put_vol INTEGER), and a subsequent insert must
    round-trip the scalars."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    conn = db.connect()
    # Pre-migration schema: has the 0-DTE cols but NOT the skew cols.
    conn.executescript(
        """
        CREATE TABLE snapshots (
            symbol          TEXT    NOT NULL,
            view            TEXT    NOT NULL,
            ts              INTEGER NOT NULL,
            spot            REAL,
            flip            REAL,
            top_pos_strike  REAL,
            top_neg_strike  REAL,
            net_total       REAL,
            dte             INTEGER,
            gex_json        TEXT,
            net_delta_0dte            REAL,
            projected_net_delta_close REAL,
            hedge_pressure            REAL,
            PRIMARY KEY (symbol, view, ts)
        );
        """
    )
    db.init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
    assert {"rr_25d", "call_vol", "put_vol"} <= cols

    # And the scalars round-trip through insert after the migration.
    db.insert_snapshot(
        conn, "$SPX", "dex",
        {"ts": 1_700_000_000, "spot": 5000.0, "flip": None,
         "top_pos_strike": None, "top_neg_strike": None, "net_total": 0.0,
         "rr_25d": -2.1, "call_vol": 900, "put_vol": 1100},
        {}, 0,
    )
    row = conn.execute(
        "SELECT rr_25d, call_vol, put_vol FROM snapshots WHERE view = 'dex'"
    ).fetchone()
    assert row == (-2.1, 900, 1100)

    # Idempotent: a second init_schema must not raise or duplicate columns.
    db.init_schema(conn)
    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
    assert cols_after == cols


def test_insert_and_load_flow_series(tmp_path, monkeypatch):
    dbpath = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)
    ts = int(time.time())
    for i, (cv, pv, cp, pp) in enumerate([(100, 80, 2.0e6, 1.5e6),
                                          (140, 130, 3.1e6, 2.9e6)]):
        summary = {"ts": ts + i * 120, "spot": 500.0 + i, "call_vol": cv,
                   "put_vol": pv, "call_prem": cp, "put_prem": pp}
        db.insert_snapshot(conn, "SPY", "gex", summary, {"500": {"net": 1.0}}, dte=1)
    rows = db.load_flow_series(conn, "SPY")
    assert len(rows) == 2
    # (ts, spot, call_vol, put_vol, call_prem, put_prem), chronological
    assert rows[0][1] == 500.0 and rows[0][4] == 2.0e6 and rows[0][5] == 1.5e6
    assert rows[1][2] == 140 and rows[1][4] == 3.1e6   # newest snapshot's call premium


def test_premium_columns_backfilled_on_existing_db(tmp_path, monkeypatch):
    # A pre-existing DB WITHOUT the premium columns (schema as it was through
    # put_vol) gets call_prem/put_prem added by init_schema's ALTER migration.
    dbpath = tmp_path / "old.db"
    raw = sqlite3.connect(dbpath)
    raw.execute(
        "CREATE TABLE snapshots ("
        "symbol TEXT, view TEXT, ts INTEGER, spot REAL, flip REAL, "
        "top_pos_strike REAL, top_neg_strike REAL, net_total REAL, dte INTEGER, "
        "gex_json TEXT, net_delta_0dte REAL, projected_net_delta_close REAL, "
        "hedge_pressure REAL, rr_25d REAL, call_vol INTEGER, put_vol INTEGER, "
        "PRIMARY KEY (symbol, view, ts))")
    raw.commit(); raw.close()
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    conn = db.connect()
    db.init_schema(conn)  # must ALTER-add call_prem/put_prem (+ the older ones)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
    assert "call_prem" in cols and "put_prem" in cols
    # and inserts now round-trip the premium
    db.insert_snapshot(conn, "SPY", "gex",
                       {"ts": int(time.time()), "spot": 1.0,
                        "call_prem": 9.0, "put_prem": 4.0},
                       {"x": {"net": 1}}, dte=1)
    assert db.load_flow_series(conn, "SPY")[0][4] == 9.0
