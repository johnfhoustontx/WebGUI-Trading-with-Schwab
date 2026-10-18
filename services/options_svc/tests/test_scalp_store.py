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
                                  "calls_on", "latest_call_day"])
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
