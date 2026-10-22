"""The Opening Scalp's bar and chain store (scalp_store).

Every function resolves its database at call time (``db_path=None``), so a test
points it at ``tmp_path`` and nothing here can open the live store."""
import inspect
import sqlite3

import pytest

from services.options_svc import scalp_store as st


def _db(tmp_path):
    return tmp_path / "scalp.db"


def test_upsert_is_idempotent(tmp_path):
    db = _db(tmp_path)
    row = ("SPY", 300, 1000, 1.0, 2.0, 0.5, 1.5, 100.0)
    assert st.upsert_bars([row], db_path=db) == 1
    assert st.upsert_bars([row], db_path=db) == 1
    second = ("SPY", 300, 1000, 9.0, 9.5, 8.5, 9.25, 7.0)
    st.upsert_bars([second], db_path=db)
    got = st.bars("SPY", 300, 0, 10_000, db_path=db)
    assert got == [st.Bar(1000, 9.0, 9.5, 8.5, 9.25, 7.0)]


def test_bars_range_is_half_open_and_sorted(tmp_path):
    db = _db(tmp_path)
    st.upsert_bars([("SPY", 300, ts, float(ts), float(ts), float(ts), float(ts), 1.0)
                    for ts in (400, 100, 300, 200, 500)], db_path=db)
    got = st.bars("SPY", 300, 200, 500, db_path=db)
    assert [b.ts for b in got] == [200, 300, 400]
    assert st.bars("SPY", 300, 500, 500, db_path=db) == []


def test_bars_are_kept_apart_by_symbol_and_interval(tmp_path):
    db = _db(tmp_path)
    st.upsert_bars([("SPY", 300, 100, 1, 1, 1, 1, 1), ("QQQ", 300, 100, 2, 2, 2, 2, 2),
                    ("SPY", 60, 100, 3, 3, 3, 3, 3)], db_path=db)
    assert st.bars("SPY", 300, 0, 200, db_path=db)[0].close == 1
    assert st.bars("QQQ", 300, 0, 200, db_path=db)[0].close == 2
    assert st.bars("SPY", 60, 0, 200, db_path=db)[0].close == 3
    assert st.bars("IWM", 300, 0, 200, db_path=db) == []


def test_bar_is_a_named_tuple_in_the_documented_order():
    assert st.Bar._fields == ("ts", "open", "high", "low", "close", "volume")


def test_latest_ts(tmp_path):
    db = _db(tmp_path)
    assert st.latest_ts("SPY", 300, db_path=db) is None
    st.upsert_bars([("SPY", 300, 100, 1, 1, 1, 1, 1), ("SPY", 300, 700, 1, 1, 1, 1, 1),
                    ("QQQ", 300, 900, 1, 1, 1, 1, 1), ("SPY", 60, 800, 1, 1, 1, 1, 1)],
                   db_path=db)
    assert st.latest_ts("SPY", 300, db_path=db) == 700


def test_an_empty_upsert_writes_nothing(tmp_path):
    db = _db(tmp_path)
    assert st.upsert_bars([], db_path=db) == 0
    assert st.bars("SPY", 300, 0, 10, db_path=db) == []


def test_chain_slice_round_trips(tmp_path):
    db = _db(tmp_path)
    body = {"calls": [{"strike": 500.0, "bid": 1.2, "ask": 1.3}], "note": "café", "n": None}
    assert st.chain_slice("2026-10-19", "SPY", db_path=db) is None
    st.save_chain_slice("2026-10-19", "SPY", 1234, body, db_path=db)
    assert st.chain_slice("2026-10-19", "SPY", db_path=db) == body
    assert st.chain_slice("2026-10-19", "QQQ", db_path=db) is None
    assert st.chain_slice("2026-10-20", "SPY", db_path=db) is None


def test_a_second_chain_slice_for_the_day_replaces_the_first(tmp_path):
    db = _db(tmp_path)
    st.save_chain_slice("2026-10-19", "SPY", 1, {"v": 1}, db_path=db)
    st.save_chain_slice("2026-10-19", "SPY", 2, {"v": 2}, db_path=db)
    assert st.chain_slice("2026-10-19", "SPY", db_path=db) == {"v": 2}
    conn = st.connect(db)
    try:
        assert conn.execute("select count(*), max(ts) from chain_slices").fetchone() == (1, 2)
    finally:
        conn.close()


def test_a_call_round_trips_and_the_latest_day_is_found(tmp_path):
    db = _db(tmp_path)
    assert st.latest_call_day(db_path=db) is None
    assert st.calls_on("2026-10-19", db_path=db) == {}
    st.save_call("2026-10-16", "SPY", {"direction": 1, "roles": {}}, db_path=db)
    st.save_call("2026-10-19", "SPY", {"direction": -1}, db_path=db)
    st.save_call("2026-10-19", "QQQ", {"direction": None}, db_path=db)
    st.save_call("2026-10-19", "SPY", {"direction": 1}, db_path=db)     # replaces
    assert st.latest_call_day(db_path=db) == "2026-10-19"
    assert st.calls_on("2026-10-19", db_path=db) == {"SPY": {"direction": 1},
                                                     "QQQ": {"direction": None}}
    assert st.calls_on("2026-10-16", db_path=db) == {"SPY": {"direction": 1, "roles": {}}}


def test_a_call_with_a_nan_is_refused_not_stored(tmp_path):
    # A NaN would be written as bare ``NaN``, which is not JSON.
    db = _db(tmp_path)
    with pytest.raises(ValueError):
        st.save_call("2026-10-19", "SPY", {"p": float("nan")}, db_path=db)
    assert st.calls_on("2026-10-19", db_path=db) == {}


def test_the_chain_slice_is_stored_compressed(tmp_path):
    db = _db(tmp_path)
    body = {"rows": [{"strike": float(i), "bid": 1.0, "ask": 1.1} for i in range(500)]}
    st.save_chain_slice("2026-10-19", "SPY", 1, body, db_path=db)
    conn = st.connect(db)
    try:
        blob = conn.execute("select body from chain_slices").fetchone()[0]
    finally:
        conn.close()
    import json
    assert isinstance(blob, bytes) and len(blob) < len(json.dumps(body)) / 3


def test_connect_creates_the_schema_in_wal_mode(tmp_path):
    conn = st.connect(_db(tmp_path))
    try:
        names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        assert {"bars", "chain_slices"} <= names
        assert conn.execute("pragma journal_mode").fetchone()[0].lower() == "wal"
        cols = [r[1] for r in conn.execute("pragma table_info(bars)")]
        assert cols == ["symbol", "interval", "ts", "open", "high", "low", "close", "volume"]
        pk = {r[1] for r in conn.execute("pragma table_info(bars)") if r[5]}
        assert pk == {"symbol", "interval", "ts"}
    finally:
        conn.close()


def test_connect_twice_is_harmless(tmp_path):
    st.connect(_db(tmp_path)).close()
    st.connect(_db(tmp_path)).close()


def test_connect_creates_the_parent_folder(tmp_path):
    db = tmp_path / "data" / "deeper" / "scalp.db"
    st.connect(db).close()
    assert db.exists()


@pytest.mark.parametrize("name", ["connect", "upsert_bars", "bars", "latest_ts",
                                  "save_chain_slice", "chain_slice", "save_call",
                                  "calls_on", "latest_call_day", "save_reading",
                                  "readings_on", "save_intraday_call",
                                  "intraday_calls_on", "save_intraday_slice",
                                  "intraday_slice"])
def test_no_default_path_bound_at_def(name):
    assert inspect.signature(getattr(st, name)).parameters["db_path"].default is None


def test_the_default_path_is_resolved_when_called(monkeypatch, tmp_path):
    # Proof the default is looked up at call time: moving SCALP_DB moves the file.
    import repo_paths
    target = tmp_path / "late.db"
    monkeypatch.setattr(repo_paths, "SCALP_DB", target)
    st.upsert_bars([("SPY", 300, 1, 1, 1, 1, 1, 1)])
    assert target.exists()
    assert st.bars("SPY", 300, 0, 10)[0].ts == 1


def test_the_store_imports_only_what_the_brief_allows():
    import ast
    tree = ast.parse(inspect.getsource(st))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert mods <= {"sqlite3", "zlib", "json", "repo_paths", "typing", "os", "pathlib",
                    "logging", "__future__"}, mods


def test_sqlite_error_type_is_not_swallowed(tmp_path):
    # A path that cannot be opened must surface, not read as "no bars".
    bad = tmp_path / "a_file"
    bad.write_text("x")
    with pytest.raises((sqlite3.Error, OSError)):
        st.bars("SPY", 300, 0, 10, db_path=bad / "scalp.db")


# The schema as it stood at HEAD, before the intraday tables. A store built from
# it is the shape prod holds; ``connect`` must add the new tables and leave the
# old ones exactly as they were.
OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol   TEXT    NOT NULL,
    interval INTEGER NOT NULL,
    ts       INTEGER NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    PRIMARY KEY (symbol, interval, ts)
);
CREATE TABLE IF NOT EXISTS chain_slices (
    day    TEXT    NOT NULL,
    symbol TEXT    NOT NULL,
    ts     INTEGER NOT NULL,
    body   BLOB    NOT NULL,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS outcomes (
    day          TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    iv_scale     REAL    NOT NULL,
    role         TEXT    NOT NULL,
    structure    TEXT    NOT NULL,
    bucket       TEXT,
    result       TEXT    NOT NULL,
    minutes      INTEGER,
    entry_value  REAL,
    end_pnl_pct  REAL,
    both_in_bar  INTEGER NOT NULL,
    interval     INTEGER NOT NULL,
    PRIMARY KEY (day, symbol, iv_scale, role, interval)
);
CREATE TABLE IF NOT EXISTS mornings (
    day    TEXT NOT NULL,
    symbol TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS verdicts (
    symbol TEXT NOT NULL,
    role   TEXT NOT NULL,
    bucket TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (symbol, role, bucket)
);
CREATE TABLE IF NOT EXISTS calls (
    day    TEXT NOT NULL,
    symbol TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS grades (
    day          TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    structure    TEXT    NOT NULL,
    model_result TEXT,
    chain_result TEXT,
    highlighted  INTEGER NOT NULL,
    avoided      INTEGER NOT NULL,
    p_base       REAL,
    PRIMARY KEY (day, symbol, role)
);
CREATE TABLE IF NOT EXISTS iv_calibration (
    day         TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    atm_iv      REAL NOT NULL,
    vol_reading REAL NOT NULL,
    ratio       REAL NOT NULL,
    PRIMARY KEY (day, symbol)
);
"""

# (name, type, notnull, pk position) for every pre-existing table, as at HEAD.
OLD_COLUMNS = {
    "bars": [("symbol", "TEXT", True, 1), ("interval", "INTEGER", True, 2),
             ("ts", "INTEGER", True, 3), ("open", "REAL", False, 0),
             ("high", "REAL", False, 0), ("low", "REAL", False, 0),
             ("close", "REAL", False, 0), ("volume", "REAL", False, 0)],
    "chain_slices": [("day", "TEXT", True, 1), ("symbol", "TEXT", True, 2),
                     ("ts", "INTEGER", True, 0), ("body", "BLOB", True, 0)],
    "calls": [("day", "TEXT", True, 1), ("symbol", "TEXT", True, 2),
              ("json", "TEXT", True, 0)],
    "grades": [("day", "TEXT", True, 1), ("symbol", "TEXT", True, 2),
               ("role", "TEXT", True, 3), ("structure", "TEXT", True, 0),
               ("model_result", "TEXT", False, 0), ("chain_result", "TEXT", False, 0),
               ("highlighted", "INTEGER", True, 0), ("avoided", "INTEGER", True, 0),
               ("p_base", "REAL", False, 0)],
    "outcomes": [("day", "TEXT", True, 1), ("symbol", "TEXT", True, 2),
                 ("iv_scale", "REAL", True, 3), ("role", "TEXT", True, 4),
                 ("structure", "TEXT", True, 0), ("bucket", "TEXT", False, 0),
                 ("result", "TEXT", True, 0), ("minutes", "INTEGER", False, 0),
                 ("entry_value", "REAL", False, 0), ("end_pnl_pct", "REAL", False, 0),
                 ("both_in_bar", "INTEGER", True, 0), ("interval", "INTEGER", True, 5)],
    "mornings": [("day", "TEXT", True, 1), ("symbol", "TEXT", True, 2),
                 ("json", "TEXT", True, 0)],
    "verdicts": [("symbol", "TEXT", True, 1), ("role", "TEXT", True, 2),
                 ("bucket", "TEXT", True, 3), ("json", "TEXT", True, 0)],
    "iv_calibration": [("day", "TEXT", True, 1), ("symbol", "TEXT", True, 2),
                       ("atm_iv", "REAL", True, 0), ("vol_reading", "REAL", True, 0),
                       ("ratio", "REAL", True, 0)],
}

# The new intraday tables: column names in order, and the primary key.
NEW_COLUMNS = {
    "intraday_readings": ["day", "slot", "symbol", "json"],
    "intraday_calls": ["day", "slot", "symbol", "json"],
    "intraday_chain_slices": ["day", "slot", "symbol", "ts", "body"],
    "intraday_outcomes": ["day", "slot", "symbol", "iv_scale", "role", "structure",
                          "block", "bucket", "result", "minutes", "entry_value",
                          "both_in_bar"],
    "intraday_verdicts": ["symbol", "block", "role", "bucket", "json"],
    "intraday_grades": ["day", "slot", "symbol", "role", "structure", "model_result",
                        "chain_result", "highlighted", "avoided", "confirm_word",
                        "p_base", "block"],
}
NEW_PRIMARY_KEYS = {
    "intraday_readings": ["day", "slot", "symbol"],
    "intraday_calls": ["day", "slot", "symbol"],
    "intraday_chain_slices": ["day", "slot", "symbol"],
    "intraday_outcomes": ["day", "slot", "symbol", "iv_scale", "role"],
    "intraday_verdicts": ["symbol", "block", "role", "bucket"],
    "intraday_grades": ["day", "slot", "symbol", "role"],
}


def _columns(conn, table):
    return [(r[1], r[2], bool(r[3]), r[5])
            for r in conn.execute(f"pragma table_info({table})")]


def test_existing_tables_unchanged(tmp_path):
    conn = st.connect(_db(tmp_path))
    try:
        for table, expected in OLD_COLUMNS.items():
            assert _columns(conn, table) == expected, table
    finally:
        conn.close()


def test_new_tables_open_on_an_old_store(tmp_path):
    db = _db(tmp_path)
    old = sqlite3.connect(db)
    old.executescript(OLD_SCHEMA)
    old.execute("INSERT INTO bars VALUES ('SPY', 300, 100, 1, 2, 0.5, 1.5, 9)")
    old.execute("INSERT INTO calls VALUES ('2026-10-19', 'SPY', '{\"direction\":1}')")
    old.commit()
    old.close()

    conn = st.connect(db)
    try:
        names = {r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'")}
        assert set(NEW_COLUMNS) <= names
        assert conn.execute("select open, volume from bars").fetchone() == (1.0, 9.0)
        assert conn.execute("select json from calls").fetchone() == ('{"direction":1}',)
    finally:
        conn.close()
    assert st.calls_on("2026-10-19", db_path=db) == {"SPY": {"direction": 1}}
    assert st.readings_on("2026-10-19", db_path=db) == {}


def test_the_new_tables_have_the_named_columns_and_keys(tmp_path):
    conn = st.connect(_db(tmp_path))
    try:
        for table, cols in NEW_COLUMNS.items():
            assert [r[1] for r in conn.execute(f"pragma table_info({table})")] == cols, table
        for table, keys in NEW_PRIMARY_KEYS.items():
            pk_rows = [r for r in conn.execute(f"pragma table_info({table})") if r[5]]
            assert [r[1] for r in sorted(pk_rows, key=lambda r: r[5])] == keys, table
    finally:
        conn.close()


def test_a_reading_round_trips_keyed_by_slot_and_symbol(tmp_path):
    db = _db(tmp_path)
    assert st.readings_on("2026-10-19", db_path=db) == {}
    st.save_reading("2026-10-19", "08:51", "SPY", {"score": 0.4}, db_path=db)
    st.save_reading("2026-10-19", "09:06", "SPY", {"score": -0.2}, db_path=db)
    st.save_reading("2026-10-19", "08:51", "QQQ", {"score": 1.0}, db_path=db)
    st.save_reading("2026-10-19", "08:51", "SPY", {"score": 0.9}, db_path=db)  # replaces
    st.save_reading("2026-10-20", "08:51", "SPY", {"score": 5}, db_path=db)
    assert st.readings_on("2026-10-19", db_path=db) == {
        ("08:51", "SPY"): {"score": 0.9},
        ("09:06", "SPY"): {"score": -0.2},
        ("08:51", "QQQ"): {"score": 1.0},
    }


def test_a_reading_with_a_nan_is_refused_not_stored(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError):
        st.save_reading("2026-10-19", "08:51", "SPY", {"p": float("nan")}, db_path=db)
    assert st.readings_on("2026-10-19", db_path=db) == {}


def test_an_intraday_call_round_trips_and_leaves_the_morning_call_alone(tmp_path):
    db = _db(tmp_path)
    assert st.intraday_calls_on("2026-10-19", db_path=db) == {}
    st.save_call("2026-10-19", "SPY", {"direction": 1}, db_path=db)
    st.save_intraday_call("2026-10-19", "08:51", "SPY", {"direction": -1}, db_path=db)
    st.save_intraday_call("2026-10-19", "09:06", "SPY", {"direction": 1}, db_path=db)
    st.save_intraday_call("2026-10-19", "09:06", "SPY", {"direction": 0}, db_path=db)
    assert st.intraday_calls_on("2026-10-19", db_path=db) == {
        ("08:51", "SPY"): {"direction": -1},
        ("09:06", "SPY"): {"direction": 0},
    }
    assert st.intraday_calls_on("2026-10-20", db_path=db) == {}
    assert st.calls_on("2026-10-19", db_path=db) == {"SPY": {"direction": 1}}


def test_an_intraday_call_with_a_nan_is_refused_not_stored(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError):
        st.save_intraday_call("2026-10-19", "08:51", "SPY", {"p": float("inf")}, db_path=db)
    assert st.intraday_calls_on("2026-10-19", db_path=db) == {}


def test_an_intraday_slice_round_trips_and_is_kept_per_slot(tmp_path):
    db = _db(tmp_path)
    assert st.intraday_slice("2026-10-19", "08:51", "SPY", db_path=db) is None
    body = {"calls": [{"strike": 500.0, "bid": 1.2, "ask": 1.3}], "note": "café", "n": None}
    st.save_intraday_slice("2026-10-19", "08:51", "SPY", 1234, body, db_path=db)
    st.save_intraday_slice("2026-10-19", "09:06", "SPY", 1300, {"v": 2}, db_path=db)
    assert st.intraday_slice("2026-10-19", "08:51", "SPY", db_path=db) == body
    assert st.intraday_slice("2026-10-19", "09:06", "SPY", db_path=db) == {"v": 2}
    assert st.intraday_slice("2026-10-19", "09:06", "QQQ", db_path=db) is None
    assert st.intraday_slice("2026-10-20", "08:51", "SPY", db_path=db) is None
    assert st.chain_slice("2026-10-19", "SPY", db_path=db) is None


def test_a_second_intraday_slice_for_the_slot_replaces_the_first(tmp_path):
    db = _db(tmp_path)
    st.save_intraday_slice("2026-10-19", "08:51", "SPY", 1, {"v": 1}, db_path=db)
    st.save_intraday_slice("2026-10-19", "08:51", "SPY", 2, {"v": 2}, db_path=db)
    assert st.intraday_slice("2026-10-19", "08:51", "SPY", db_path=db) == {"v": 2}
    conn = st.connect(db)
    try:
        assert conn.execute(
            "select count(*), max(ts) from intraday_chain_slices").fetchone() == (1, 2)
    finally:
        conn.close()


# The store's schema exactly as it stood at the weighted-chance plan's base commit
# (7de9ba6), before ``prob_sources`` and ``prob_models`` were added. Production holds
# these tables, so an edit to any of these statements is a schema change to a live
# store: the test below fails on one.
_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol   TEXT    NOT NULL,
    interval INTEGER NOT NULL,
    ts       INTEGER NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    PRIMARY KEY (symbol, interval, ts)
);
CREATE TABLE IF NOT EXISTS chain_slices (
    day    TEXT    NOT NULL,
    symbol TEXT    NOT NULL,
    ts     INTEGER NOT NULL,
    body   BLOB    NOT NULL,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS outcomes (
    day          TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    iv_scale     REAL    NOT NULL,
    role         TEXT    NOT NULL,
    structure    TEXT    NOT NULL,
    bucket       TEXT,
    result       TEXT    NOT NULL,
    minutes      INTEGER,
    entry_value  REAL,
    end_pnl_pct  REAL,
    both_in_bar  INTEGER NOT NULL,
    interval     INTEGER NOT NULL,
    PRIMARY KEY (day, symbol, iv_scale, role, interval)
);
CREATE TABLE IF NOT EXISTS mornings (
    day    TEXT NOT NULL,
    symbol TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS verdicts (
    symbol TEXT NOT NULL,
    role   TEXT NOT NULL,
    bucket TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (symbol, role, bucket)
);
CREATE TABLE IF NOT EXISTS calls (
    day    TEXT NOT NULL,
    symbol TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS grades (
    day          TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    structure    TEXT    NOT NULL,
    model_result TEXT,
    chain_result TEXT,
    highlighted  INTEGER NOT NULL,
    avoided      INTEGER NOT NULL,
    p_base       REAL,
    PRIMARY KEY (day, symbol, role)
);
CREATE TABLE IF NOT EXISTS iv_calibration (
    day         TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    atm_iv      REAL NOT NULL,
    vol_reading REAL NOT NULL,
    ratio       REAL NOT NULL,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS intraday_readings (
    day    TEXT NOT NULL,
    slot   TEXT NOT NULL,
    symbol TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (day, slot, symbol)
);
CREATE TABLE IF NOT EXISTS intraday_calls (
    day    TEXT NOT NULL,
    slot   TEXT NOT NULL,
    symbol TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (day, slot, symbol)
);
CREATE TABLE IF NOT EXISTS intraday_chain_slices (
    day    TEXT    NOT NULL,
    slot   TEXT    NOT NULL,
    symbol TEXT    NOT NULL,
    ts     INTEGER NOT NULL,
    body   BLOB    NOT NULL,
    PRIMARY KEY (day, slot, symbol)
);
CREATE TABLE IF NOT EXISTS intraday_outcomes (
    day          TEXT    NOT NULL,
    slot         TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    iv_scale     REAL    NOT NULL,
    role         TEXT    NOT NULL,
    structure    TEXT    NOT NULL,
    block        TEXT    NOT NULL,
    bucket       TEXT,
    result       TEXT    NOT NULL,
    minutes      INTEGER,
    entry_value  REAL,
    both_in_bar  INTEGER NOT NULL,
    PRIMARY KEY (day, slot, symbol, iv_scale, role)
);
CREATE TABLE IF NOT EXISTS intraday_verdicts (
    symbol TEXT NOT NULL,
    block  TEXT NOT NULL,
    role   TEXT NOT NULL,
    bucket TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (symbol, block, role, bucket)
);
CREATE TABLE IF NOT EXISTS intraday_grades (
    day          TEXT    NOT NULL,
    slot         TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    structure    TEXT    NOT NULL,
    model_result TEXT,
    chain_result TEXT,
    highlighted  INTEGER NOT NULL,
    avoided      INTEGER NOT NULL,
    confirm_word TEXT,
    p_base       REAL,
    block        TEXT    NOT NULL,
    PRIMARY KEY (day, slot, symbol, role)
);
"""


def _table_sql(conn) -> dict:
    return dict(conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall())


def test_prob_tables_are_new_and_old_tables_untouched(tmp_path):
    base = sqlite3.connect(":memory:")
    try:
        base.executescript(_BASE_SCHEMA)
        before = _table_sql(base)
    finally:
        base.close()
    assert {"bars", "outcomes", "intraday_readings"} <= set(before)
    conn = st.connect(_db(tmp_path))
    try:
        after = _table_sql(conn)
    finally:
        conn.close()
    for name, sql in before.items():
        assert after[name] == sql, f"{name} was edited"
    assert set(after) - set(before) == {"prob_sources", "prob_models"}
    conn = st.connect(_db(tmp_path))
    try:
        assert [r[1] for r in conn.execute("PRAGMA table_info(prob_sources)")] == [
            "kind", "day", "slot", "symbol", "json"]
        assert [r[1] for r in conn.execute("PRAGMA table_info(prob_models)")] == [
            "version", "kind", "role", "fitted_at", "json"]
    finally:
        conn.close()


def test_prob_sources_round_trip(tmp_path):
    db = _db(tmp_path)
    morning = {"matrix": {"a": 1}, "netprem": None, "sentiment": 6, "flow": {"window": 60}}
    intraday = {"matrix": {"a": 2}, "netprem": 1.5, "sentiment": 4, "flow": {"window": 30}}
    n = st.save_prob_sources([
        ("morning", "2026-10-19", "08:36", "SPY", morning),
        ("intraday", "2026-10-19", "09:06", "SPY", intraday),
        ("intraday", "2026-10-19", "09:06", "QQQ", {"matrix": {}}),
    ], db_path=db)
    assert n == 3
    assert st.prob_sources_all("morning", db_path=db) == {
        ("2026-10-19", "08:36", "SPY"): morning}
    assert st.prob_sources_all("intraday", db_path=db) == {
        ("2026-10-19", "09:06", "SPY"): intraday,
        ("2026-10-19", "09:06", "QQQ"): {"matrix": {}}}
    assert st.prob_sources_all("other", db_path=db) == {}
    assert st.save_prob_sources([], db_path=db) == 0


def test_save_prob_sources_replaces_same_key(tmp_path):
    db = _db(tmp_path)
    st.save_prob_sources([("morning", "2026-10-19", "08:36", "SPY", {"v": 1})], db_path=db)
    st.save_prob_sources([("morning", "2026-10-19", "08:36", "SPY", {"v": 2})], db_path=db)
    assert st.prob_sources_all("morning", db_path=db) == {
        ("2026-10-19", "08:36", "SPY"): {"v": 2}}
    conn = st.connect(db)
    try:
        assert conn.execute("select count(*) from prob_sources").fetchone() == (1,)
    finally:
        conn.close()


def test_readings_all_returns_every_day(tmp_path):
    db = _db(tmp_path)
    st.save_reading("2026-10-19", "08:36", "SPY", {"p": 1}, db_path=db)
    st.save_reading("2026-10-20", "09:06", "QQQ", {"p": 2}, db_path=db)
    assert st.readings_all(db_path=db) == {
        ("2026-10-19", "08:36", "SPY"): {"p": 1},
        ("2026-10-20", "09:06", "QQQ"): {"p": 2}}
    assert st.readings_all(db_path=_db(tmp_path / "empty")) == {}


def test_latest_prob_models_is_highest_version_and_keeps_older(tmp_path):
    db = _db(tmp_path)
    assert st.next_prob_version(db_path=db) == 1
    st.save_prob_models(1, "2026-10-22T10:00:00",
                        {("morning", "target"): {"w": [1]}}, db_path=db)
    assert st.next_prob_version(db_path=db) == 2
    st.save_prob_models(2, "2026-10-29T10:00:00",
                        {("morning", "target"): {"w": [2]},
                         ("intraday", "stop"): {"w": [3]}}, db_path=db)
    assert st.next_prob_version(db_path=db) == 3
    got = st.latest_prob_models(db_path=db)
    assert got == {"version": 2, "fitted_at": "2026-10-29T10:00:00",
                   "models": {("morning", "target"): {"w": [2]},
                              ("intraday", "stop"): {"w": [3]}}}
    conn = st.connect(db)
    try:
        assert conn.execute(
            "select count(*) from prob_models where version = 1").fetchone() == (1,)
    finally:
        conn.close()


def test_latest_prob_models_empty_is_empty_dict(tmp_path):
    assert st.latest_prob_models(db_path=_db(tmp_path)) == {}


def test_save_prob_models_is_one_transaction(tmp_path):
    db = _db(tmp_path)
    bad = {("morning", "target"): {"w": 1}, ("intraday", "stop"): {"w": float("nan")}}
    with pytest.raises(ValueError):
        st.save_prob_models(1, "2026-10-22T10:00:00", bad, db_path=db)
    assert st.latest_prob_models(db_path=db) == {}
